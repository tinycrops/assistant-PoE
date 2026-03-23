#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F
from torch import nn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a pairwise ranker for the character continuity benchmark.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    return parser.parse_args()


def set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def resolve_device(name: str) -> torch.device:
    if name == "cpu":
        return torch.device("cpu")
    if name == "cuda":
        return torch.device("cuda")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def build_pair_features(anchor: dict[str, Any], candidate: dict[str, Any], tracked_stats: list[str], tracked_slots: list[str]) -> list[float]:
    anchor_stats = anchor["stat_vector"]
    candidate_stats = candidate["stat_vector"]
    anchor_slots = anchor["slot_filled"]
    candidate_slots = candidate["slot_filled"]

    features: list[float] = []
    for stat in tracked_stats:
        a = float(anchor_stats.get(stat, 0.0))
        c = float(candidate_stats.get(stat, 0.0))
        features.extend([a, c, c - a, abs(c - a)])

    for slot in tracked_slots:
        a = float(anchor_slots.get(slot, 0))
        c = float(candidate_slots.get(slot, 0))
        features.extend([a, c, c - a])

    time_delta_minutes = (
        (parse_ts(candidate["timestamp_utc"]) - parse_ts(anchor["timestamp_utc"])).total_seconds() / 60.0
    )
    features.extend(
        [
            1.0 if anchor["character"] == candidate["character"] else 0.0,
            float(len(anchor.get("changed_slots", []))),
            float(len(candidate.get("changed_slots", []))),
            time_delta_minutes,
            math.log1p(max(0.0, time_delta_minutes)),
        ]
    )
    return features


def parse_ts(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)


class Ranker(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


def build_training_rows(records: list[dict[str, Any]], tracked_stats: list[str], tracked_slots: list[str]) -> tuple[torch.Tensor, torch.Tensor]:
    rows: list[list[float]] = []
    labels: list[float] = []
    for record in records:
        anchor = record["anchor"]
        rows.append(build_pair_features(anchor, record["positive"], tracked_stats, tracked_slots))
        labels.append(1.0)
        for negative in record["negatives"]:
            rows.append(build_pair_features(anchor, negative, tracked_stats, tracked_slots))
            labels.append(0.0)
    return torch.tensor(rows, dtype=torch.float32), torch.tensor(labels, dtype=torch.float32)


def heuristic_score(anchor: dict[str, Any], candidate: dict[str, Any], tracked_stats: list[str]) -> float:
    if anchor["character"] != candidate["character"]:
        return -1e9
    delta_sum = 0.0
    for stat in tracked_stats:
        delta_sum += abs(float(candidate["stat_vector"].get(stat, 0.0)) - float(anchor["stat_vector"].get(stat, 0.0)))
    time_penalty = (parse_ts(candidate["timestamp_utc"]) - parse_ts(anchor["timestamp_utc"])).total_seconds()
    return -(delta_sum + 0.001 * max(0.0, time_penalty))


def evaluate_records(
    records: list[dict[str, Any]],
    tracked_stats: list[str],
    tracked_slots: list[str],
    model: Ranker | None,
    device: torch.device,
) -> dict[str, Any]:
    top1 = 0
    mrr = 0.0
    rows = []

    for record in records:
        anchor = record["anchor"]
        candidates = [record["positive"], *record["negatives"]]
        labels = [1] + [0] * len(record["negatives"])
        if model is None:
            scores = [heuristic_score(anchor, candidate, tracked_stats) for candidate in candidates]
        else:
            feature_rows = [build_pair_features(anchor, candidate, tracked_stats, tracked_slots) for candidate in candidates]
            x = torch.tensor(feature_rows, dtype=torch.float32, device=device)
            with torch.no_grad():
                scores = model(x).cpu().tolist()

        ranked = sorted(zip(scores, labels, candidates), key=lambda item: item[0], reverse=True)
        rank = next(index + 1 for index, (_, label, _) in enumerate(ranked) if label == 1)
        top1 += int(rank == 1)
        mrr += 1.0 / rank
        rows.append(
            {
                "anchor_id": anchor["node_id"],
                "positive_id": record["positive"]["node_id"],
                "rank_of_true_next": rank,
                "ranked_candidate_ids": [candidate["node_id"] for _, _, candidate in ranked],
            }
        )

    total = max(1, len(records))
    return {
        "top1_accuracy": top1 / total,
        "mrr": mrr / total,
        "rows": rows,
    }


def main() -> None:
    args = parse_args()
    set_seed(args.seed)
    dataset = load_json(args.dataset)
    tracked_stats = dataset["tracked_stats"]
    tracked_slots = dataset["tracked_slots"]
    train_records = dataset["train"]
    eval_records = dataset["eval"]

    x_train, y_train = build_training_rows(train_records, tracked_stats, tracked_slots)
    device = resolve_device(args.device)
    model = Ranker(x_train.shape[1], args.hidden_dim).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    x_train = x_train.to(device)
    y_train = y_train.to(device)

    history = []
    for epoch in range(args.epochs):
        model.train()
        logits = model(x_train)
        loss = F.binary_cross_entropy_with_logits(logits, y_train)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        if (epoch + 1) % 25 == 0:
            history.append({"epoch": epoch + 1, "loss": float(loss.item())})

    heuristic_eval = evaluate_records(eval_records, tracked_stats, tracked_slots, model=None, device=device)
    model_eval = evaluate_records(eval_records, tracked_stats, tracked_slots, model=model, device=device)
    train_eval = evaluate_records(train_records, tracked_stats, tracked_slots, model=model, device=device)

    summary = {
        "dataset": str(args.dataset),
        "device": str(device),
        "epochs": args.epochs,
        "hidden_dim": args.hidden_dim,
        "lr": args.lr,
        "train_pair_count": int(y_train.numel()),
        "eval_anchor_count": len(eval_records),
        "history": history,
        "heuristic_eval": {k: v for k, v in heuristic_eval.items() if k != "rows"},
        "model_eval": {k: v for k, v in model_eval.items() if k != "rows"},
        "train_eval": {k: v for k, v in train_eval.items() if k != "rows"},
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"summary": summary, "heuristic_rows": heuristic_eval["rows"], "model_rows": model_eval["rows"]}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
