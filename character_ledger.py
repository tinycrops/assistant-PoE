#!/usr/bin/env python3
"""Character-scoped memory ledger for progression tracking."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CHARACTERS_DIR = ROOT / "characters"

KEY_STATS = [
    "defence.life",
    "defence.energy_shield",
    "defence.mana",
    "defence.fire_resist_percent",
    "defence.cold_resist_percent",
    "defence.lightning_resist_percent",
    "defence.chaos_resist_percent",
    "offence.total_dps",
    "offence.average_hit",
    "misc.movement_speed_modifier_percent",
]


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            item = json.loads(raw)
            if isinstance(item, dict):
                rows.append(item)
    return rows


def format_value(value: float | int | None) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, int):
        return str(value)
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def top_stat_changes(stat_changes: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for change in stat_changes:
        stat = str(change.get("stat", "")).strip()
        delta = change.get("delta")
        if not stat or not isinstance(delta, (int, float)):
            continue
        filtered.append(change)
    filtered.sort(key=lambda item: abs(float(item["delta"])), reverse=True)
    return filtered[:limit]


def selected_stats(panel_stats: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(f"{prefix}.{key}" if prefix else key, value)
            return
        flat[prefix] = node

    walk("", panel_stats)
    return {key: flat.get(key) for key in KEY_STATS if key in flat}


def build_snapshot_observations(
    stats: dict[str, Any],
    stat_changes: list[dict[str, Any]],
    equipped_changes: list[str],
    inventory_counts: dict[str, Any],
) -> list[str]:
    observations: list[str] = []

    life = stats.get("defence.life")
    es = stats.get("defence.energy_shield")
    dps = stats.get("offence.total_dps")
    if life is not None or es is not None or dps is not None:
        observations.append(
            f"Current baseline: life {format_value(life)}, energy shield {format_value(es)}, total DPS {format_value(dps)}."
        )

    if equipped_changes:
        observations.append(f"Gear changed in {len(equipped_changes)} slots during the latest stat-watch run.")

    top_changes = top_stat_changes(stat_changes, limit=3)
    if top_changes:
        parts = [
            f"{item['stat']} {format_value(item.get('before'))} -> {format_value(item.get('after'))}"
            for item in top_changes
        ]
        observations.append("Largest measured deltas: " + "; ".join(parts) + ".")

    if inventory_counts:
        observations.append(
            "Inventory footprint: "
            f"{inventory_counts.get('total_items', 0)} total items, "
            f"{inventory_counts.get('equipped_slots', 0)} equipped slots, "
            f"{inventory_counts.get('socketed_gems', 0)} socketed gems."
        )

    return observations


def build_market_observations(pricing_summary: dict[str, Any], posts: list[str]) -> list[str]:
    observations: list[str] = []
    priced_items = pricing_summary.get("priced_items")
    total_items = pricing_summary.get("total_items")
    known_value = pricing_summary.get("known_value_chaos")
    if isinstance(priced_items, int) and isinstance(total_items, int):
        coverage = (priced_items / total_items * 100.0) if total_items else 0.0
        observations.append(
            f"Known market value is {format_value(known_value)} chaos across {priced_items}/{total_items} priced items ({coverage:.0f}% coverage)."
        )

    top_holdings = pricing_summary.get("top_holdings", [])
    if isinstance(top_holdings, list) and top_holdings:
        top = top_holdings[0]
        observations.append(
            f"Top liquid or semi-liquid holding is {top.get('label', 'Unknown')} at about {format_value(top.get('chaos_value'))} chaos."
        )

    if posts:
        next_posts = [post for post in posts if post.startswith("[NEXT]")]
        if next_posts:
            observations.append(f"Latest action prompt: {next_posts[0]}")

    return observations


def ensure_ledger(character_name: str) -> tuple[Path, Path, dict[str, Any]]:
    slug = slugify(character_name)
    char_dir = CHARACTERS_DIR / slug
    ledger_path = char_dir / "ledger.json"
    journal_path = char_dir / "journal.jsonl"
    if ledger_path.exists():
        ledger = load_json(ledger_path)
    else:
        ledger = {
            "schema_version": 1,
            "character": {
                "name": character_name,
                "slug": slug,
            },
            "active_context": {},
            "latest_snapshot": {},
            "latest_market": {},
            "latest_observations": [],
            "milestones": [],
            "sources": {},
            "updated_at_utc": utc_now(),
        }
    return ledger_path, journal_path, ledger


def merge_unique_strings(existing: list[Any], incoming: list[str], limit: int = 12) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for raw in list(incoming) + [str(item) for item in existing]:
        item = str(raw).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def append_snapshot_history(ledger: dict[str, Any], entry: dict[str, Any], limit: int = 120) -> None:
    history = ledger.setdefault("snapshot_history", [])
    if not isinstance(history, list):
        history = []
        ledger["snapshot_history"] = history

    dedupe_key = str(entry.get("captured_at_utc", "")).strip()
    if dedupe_key:
        history = [item for item in history if not (isinstance(item, dict) and item.get("captured_at_utc") == dedupe_key)]
    history.insert(0, entry)
    history.sort(
        key=lambda item: parse_timestamp(item.get("captured_at_utc")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    del history[limit:]
    ledger["snapshot_history"] = history


def append_milestone(ledger: dict[str, Any], milestone: dict[str, Any]) -> None:
    milestones = ledger.setdefault("milestones", [])
    if not isinstance(milestones, list):
        milestones = []
        ledger["milestones"] = milestones
    dedupe_key = (
        milestone.get("type"),
        milestone.get("timestamp_utc"),
        milestone.get("summary"),
    )
    for item in milestones:
        if not isinstance(item, dict):
            continue
        existing_key = (item.get("type"), item.get("timestamp_utc"), item.get("summary"))
        if existing_key == dedupe_key:
            return
    milestones.insert(0, milestone)
    milestones.sort(
        key=lambda item: parse_timestamp(item.get("timestamp_utc")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    del milestones[12:]


def write_ledger(ledger_path: Path, ledger: dict[str, Any]) -> None:
    ledger["updated_at_utc"] = utc_now()
    save_json(ledger_path, ledger)


def update_active_context(ledger: dict[str, Any], event_type: str, timestamp_utc: Any) -> None:
    current = ledger.get("active_context", {})
    if not isinstance(current, dict):
        current = {}
    current_ts = parse_timestamp(current.get("last_event_at_utc"))
    incoming_ts = parse_timestamp(timestamp_utc)
    if current_ts is not None and incoming_ts is not None and incoming_ts < current_ts:
        return
    ledger["active_context"] = {
        "last_event_type": event_type,
        "last_event_at_utc": timestamp_utc,
    }


def update_from_live_character(*, character_doc: dict[str, Any], account: str, realm: str) -> None:
    character_name = str(character_doc.get("name", "")).strip()
    if not character_name:
        return

    ledger_path, journal_path, ledger = ensure_ledger(character_name)
    observed_at = utc_now()
    previous_character = ledger.get("character", {})
    previous_level = previous_character.get("level") if isinstance(previous_character, dict) else None

    ledger["character"] = {
        "name": character_name,
        "slug": slugify(character_name),
        "account": account,
        "realm": realm,
        "league": character_doc.get("league"),
        "class": character_doc.get("class"),
        "level": character_doc.get("level"),
        "last_live_confirmed_at_utc": observed_at,
    }
    update_active_context(ledger, "live_character_sync", observed_at)

    summary = (
        f"Live character confirmed at level {format_value(character_doc.get('level'))}"
        f" in {character_doc.get('league', 'unknown league')}"
    )
    if previous_level != character_doc.get("level"):
        summary += f" (previous ledger level: {format_value(previous_level)})"

    milestone = {
        "timestamp_utc": observed_at,
        "type": "live_character_sync",
        "summary": summary,
        "source": "character-window/get-characters",
    }
    append_milestone(ledger, milestone)
    write_ledger(ledger_path, ledger)

    journal_event = {
        "timestamp_utc": observed_at,
        "type": "live_character_sync",
        "summary": summary,
        "character": {
            "name": character_name,
            "league": character_doc.get("league"),
            "class": character_doc.get("class"),
            "level": character_doc.get("level"),
        },
        "source": milestone["source"],
    }
    append_jsonl(journal_path, journal_event)


def update_from_stat_watch(
    *,
    character_name: str,
    account: str,
    realm: str,
    snapshot_doc: dict[str, Any],
    panel_stats: dict[str, Any],
    history_record: dict[str, Any],
    snapshot_path: Path,
    stats_path: Path,
    history_path: Path,
    archived_paths: dict[str, Path] | None = None,
) -> None:
    ledger_path, journal_path, ledger = ensure_ledger(character_name)

    inventory_summary = snapshot_doc.get("inventory_summary", {})
    if not isinstance(inventory_summary, dict):
        inventory_summary = {}
    inventory_counts = inventory_summary.get("counts", {})
    if not isinstance(inventory_counts, dict):
        inventory_counts = {}

    stat_changes = history_record.get("stat_changes", [])
    if not isinstance(stat_changes, list):
        stat_changes = []
    equipped_changes = history_record.get("equipped_changes", [])
    if not isinstance(equipped_changes, list):
        equipped_changes = []

    stats_subset = selected_stats(panel_stats)
    observations = build_snapshot_observations(stats_subset, stat_changes, equipped_changes, inventory_counts)

    ledger["character"] = {
        "name": character_name,
        "slug": slugify(character_name),
        "account": account,
        "realm": realm,
        "league": snapshot_doc.get("character", {}).get("league")
        or snapshot_doc.get("items", {}).get("character", {}).get("league"),
    }
    update_active_context(ledger, "stat_watch", history_record.get("timestamp_utc"))
    ledger["latest_snapshot"] = {
        "captured_at_utc": history_record.get("timestamp_utc"),
        "stats": stats_subset,
        "inventory_counts": inventory_counts,
        "equipped_changes": equipped_changes,
        "top_stat_changes": top_stat_changes(stat_changes, limit=8),
    }
    if archived_paths:
        ledger["latest_snapshot"]["artifacts"] = {
            "snapshot_path": str(archived_paths["snapshot"].relative_to(ROOT)),
            "panel_stats_path": str(archived_paths["panel_stats"].relative_to(ROOT)),
            "delta_path": str(archived_paths["delta"].relative_to(ROOT)),
        }

    snapshot_entry: dict[str, Any] = {
        "captured_at_utc": history_record.get("timestamp_utc"),
        "stats": stats_subset,
        "inventory_counts": inventory_counts,
        "equipped_changes": equipped_changes,
        "top_stat_changes": top_stat_changes(stat_changes, limit=8),
    }
    if archived_paths:
        snapshot_entry["artifacts"] = {
            "snapshot_path": str(archived_paths["snapshot"].relative_to(ROOT)),
            "panel_stats_path": str(archived_paths["panel_stats"].relative_to(ROOT)),
            "delta_path": str(archived_paths["delta"].relative_to(ROOT)),
        }
    append_snapshot_history(ledger, snapshot_entry)

    ledger["latest_observations"] = merge_unique_strings(ledger.get("latest_observations", []), observations)
    ledger["sources"]["stat_watch"] = {
        "snapshot_path": str(snapshot_path.relative_to(ROOT)),
        "panel_stats_path": str(stats_path.relative_to(ROOT)),
        "history_path": str(history_path.relative_to(ROOT)),
    }
    if archived_paths:
        ledger["sources"]["stat_watch"]["archive_dir"] = str(archived_paths["archive_dir"].relative_to(ROOT))

    summary_bits: list[str] = []
    if equipped_changes:
        summary_bits.append(f"{len(equipped_changes)} gear slot changes")
    top_changes = top_stat_changes(stat_changes, limit=2)
    for item in top_changes:
        summary_bits.append(f"{item['stat']} to {format_value(item.get('after'))}")
    if not summary_bits:
        summary_bits.append("Snapshot recorded with no measured deltas")

    milestone = {
        "timestamp_utc": history_record.get("timestamp_utc"),
        "type": "stat_watch",
        "summary": "; ".join(summary_bits),
        "source": str(history_path.relative_to(ROOT)),
    }
    append_milestone(ledger, milestone)
    write_ledger(ledger_path, ledger)

    journal_event = {
        "timestamp_utc": history_record.get("timestamp_utc"),
        "type": "stat_watch",
        "summary": milestone["summary"],
        "equipped_changes": equipped_changes,
        "top_stat_changes": top_stat_changes(stat_changes, limit=8),
        "inventory_counts": inventory_counts,
        "source": str(history_path.relative_to(ROOT)),
    }
    append_jsonl(journal_path, journal_event)


def update_from_market_snapshot(market_doc: dict[str, Any], market_path: Path | None = None) -> None:
    character = market_doc.get("character", {})
    if not isinstance(character, dict):
        return
    character_name = str(character.get("name", "")).strip()
    if not character_name:
        return

    ledger_path, journal_path, ledger = ensure_ledger(character_name)
    pricing_summary = market_doc.get("pricing_summary", {})
    if not isinstance(pricing_summary, dict):
        pricing_summary = {}
    posts = market_doc.get("posts", [])
    if not isinstance(posts, list):
        posts = []

    observations = build_market_observations(pricing_summary, [str(post) for post in posts])
    ledger_character = ledger.setdefault("character", {})
    if isinstance(ledger_character, dict):
        ledger_character["league"] = character.get("league", ledger_character.get("league"))

    update_active_context(ledger, "market_sync", market_doc.get("generated_at_utc"))
    ledger["latest_market"] = {
        "generated_at_utc": market_doc.get("generated_at_utc"),
        "pricing_summary": pricing_summary,
        "posts": posts,
    }
    ledger["latest_observations"] = merge_unique_strings(ledger.get("latest_observations", []), observations)
    if market_path is not None:
        ledger["sources"]["market_snapshot"] = str(market_path.relative_to(ROOT))

    known_value = pricing_summary.get("known_value_chaos")
    top_holdings = pricing_summary.get("top_holdings", [])
    top_label = None
    if isinstance(top_holdings, list) and top_holdings:
        top = top_holdings[0]
        if isinstance(top, dict):
            top_label = top.get("label")
    summary = f"Market sync captured {format_value(known_value)} chaos in known value"
    if top_label:
        summary += f"; top holding {top_label}"

    milestone = {
        "timestamp_utc": market_doc.get("generated_at_utc"),
        "type": "market_sync",
        "summary": summary,
        "source": str(market_path.relative_to(ROOT)) if market_path is not None else "runtime",
    }
    append_milestone(ledger, milestone)
    write_ledger(ledger_path, ledger)

    journal_event = {
        "timestamp_utc": market_doc.get("generated_at_utc"),
        "type": "market_sync",
        "summary": summary,
        "pricing_summary": pricing_summary,
        "posts": posts,
        "source": milestone["source"],
    }
    append_jsonl(journal_path, journal_event)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed or refresh a character ledger from existing artifacts.")
    parser.add_argument("--character", required=True, help="Character name")
    parser.add_argument("--account", required=True, help="Account name")
    parser.add_argument("--realm", required=True, choices=["pc", "xbox", "sony"], help="Realm")
    parser.add_argument("--snapshot", default=None, help="Stat-watch snapshot JSON")
    parser.add_argument("--panel-stats", default=None, help="Stat-watch panel stats JSON")
    parser.add_argument("--history", default=None, help="Stat-watch history JSONL")
    parser.add_argument("--market-snapshot", default=None, help="Market snapshot JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.snapshot and args.panel_stats and args.history:
        snapshot_path = ROOT / args.snapshot
        panel_stats_path = ROOT / args.panel_stats
        history_path = ROOT / args.history
        history_rows = parse_jsonl(history_path)
        if history_rows:
            update_from_stat_watch(
                character_name=args.character,
                account=args.account,
                realm=args.realm,
                snapshot_doc=load_json(snapshot_path),
                panel_stats=load_json(panel_stats_path),
                history_record=history_rows[-1],
                snapshot_path=snapshot_path,
                stats_path=panel_stats_path,
                history_path=history_path,
            )

    if args.market_snapshot:
        market_path = ROOT / args.market_snapshot
        update_from_market_snapshot(load_json(market_path), market_path)

    print(f"Updated character ledger for {args.character}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
