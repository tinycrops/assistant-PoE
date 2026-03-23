#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
STAT_WATCH_DIR = ROOT / "logs" / "stat_watch"
OUTPUT_DIR = Path(__file__).resolve().parent / "generated"
OUTPUT_PATH = OUTPUT_DIR / "continuity_benchmark.json"

TRACKED_STATS = [
    "charges.endurance.max",
    "charges.frenzy.max",
    "charges.power.max",
    "defence.armour",
    "defence.block_chance_percent",
    "defence.chaos_resist_percent",
    "defence.cold_resist_percent",
    "defence.energy_shield",
    "defence.evasion",
    "defence.fire_resist_percent",
    "defence.life",
    "defence.lightning_resist_percent",
    "defence.mana",
    "defence.physical_damage_reduction_percent",
    "defence.spell_suppression_percent",
    "misc.dexterity",
    "misc.intelligence",
    "misc.life_regen",
    "misc.mana_regen",
    "misc.movement_speed_modifier_percent",
    "misc.net_recovery",
    "misc.strength",
    "offence.attack_or_cast_rate",
    "offence.average_damage",
    "offence.average_hit",
    "offence.crit_chance_percent",
    "offence.hit_chance_percent",
    "offence.total_dps",
]

TRACKED_SLOTS = [
    "Weapon",
    "Weapon2",
    "Offhand",
    "Helm",
    "BodyArmour",
    "Gloves",
    "Boots",
    "Belt",
    "Amulet",
    "Ring",
    "Ring2",
    "Flask",
    "Flask2",
    "Flask3",
    "Flask4",
    "Flask5",
]

GEAR_RE = re.compile(r"^(?P<slot>[^:]+): (?P<before>.+?) -> (?P<after>.+)$")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass
class StateNode:
    node_id: str
    character: str
    timestamp_utc: str
    state_index: int
    stat_vector: dict[str, float]
    slot_filled: dict[str, int]
    equipped_changes: list[str]
    changed_slots: list[str]


def parse_gear_change(change: str) -> tuple[str, str, str] | None:
    match = GEAR_RE.match(change.strip())
    if not match:
        return None
    return match.group("slot"), match.group("before").strip(), match.group("after").strip()


def reconstruct_states(history_path: Path) -> list[StateNode]:
    character = history_path.name.removesuffix("_history.jsonl")
    rows = [json.loads(line) for line in history_path.read_text().splitlines() if line.strip()]
    rows.sort(key=lambda row: parse_timestamp(row["timestamp_utc"]))

    stats = {stat: 0.0 for stat in TRACKED_STATS}
    slots = {slot: 0 for slot in TRACKED_SLOTS}
    nodes: list[StateNode] = []

    for index, row in enumerate(rows):
        for change in row.get("stat_changes", []):
            stat = str(change.get("stat", "")).strip()
            if stat in stats:
                stats[stat] = float(change.get("after", stats[stat]))

        changed_slots: list[str] = []
        for change in row.get("equipped_changes", []):
            parsed = parse_gear_change(change)
            if not parsed:
                continue
            slot, _, after = parsed
            if slot not in slots:
                slots[slot] = 0
            slots[slot] = 0 if after == "None" else 1
            changed_slots.append(slot)

        nodes.append(
            StateNode(
                node_id=f"{character}:{index:03d}",
                character=character,
                timestamp_utc=row["timestamp_utc"],
                state_index=index,
                stat_vector=dict(stats),
                slot_filled=dict(slots),
                equipped_changes=[str(item) for item in row.get("equipped_changes", [])],
                changed_slots=sorted(set(changed_slots)),
            )
        )

    return nodes


def node_payload(node: StateNode) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "character": node.character,
        "timestamp_utc": node.timestamp_utc,
        "state_index": node.state_index,
        "stat_vector": node.stat_vector,
        "slot_filled": node.slot_filled,
        "equipped_changes": node.equipped_changes,
        "changed_slots": node.changed_slots,
    }


def split_anchors(nodes_by_character: dict[str, list[StateNode]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train: list[dict[str, Any]] = []
    eval_: list[dict[str, Any]] = []
    all_nodes = [node for nodes in nodes_by_character.values() for node in nodes]

    for character, nodes in nodes_by_character.items():
        if len(nodes) < 2:
            continue
        anchor_count = len(nodes) - 1
        split_at = max(1, math.floor(anchor_count * 0.7))
        for anchor_index in range(anchor_count):
            anchor = nodes[anchor_index]
            positive = nodes[anchor_index + 1]
            same_char_negatives = [node for node in nodes if node.node_id not in {anchor.node_id, positive.node_id}]
            other_negatives = [node for node in all_nodes if node.character != character]
            negative_pool = same_char_negatives[:3] + other_negatives[:3]
            record = {
                "anchor": node_payload(anchor),
                "positive": node_payload(positive),
                "negatives": [node_payload(node) for node in negative_pool],
            }
            if anchor_index < split_at:
                train.append(record)
            else:
                eval_.append(record)

    return train, eval_


def main() -> None:
    nodes_by_character: dict[str, list[StateNode]] = {}
    for path in sorted(STAT_WATCH_DIR.glob("*_history.jsonl")):
        nodes = reconstruct_states(path)
        if nodes:
            nodes_by_character[nodes[0].character] = nodes

    train, eval_ = split_anchors(nodes_by_character)

    payload = {
        "tracked_stats": TRACKED_STATS,
        "tracked_slots": TRACKED_SLOTS,
        "characters": {character: [node_payload(node) for node in nodes] for character, nodes in nodes_by_character.items()},
        "train": train,
        "eval": eval_,
        "summary": {
            "character_count": len(nodes_by_character),
            "state_count": sum(len(nodes) for nodes in nodes_by_character.values()),
            "train_anchor_count": len(train),
            "eval_anchor_count": len(eval_),
        },
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
