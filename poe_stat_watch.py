#!/usr/bin/env python3
"""Snapshot current PoE character and report panel stat deltas vs previous run."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from character_ledger import update_from_stat_watch
from poe_character_sync import PoeApiError, get_stash_items, normalize_account_name
from poe_oauth import (
    PoeOAuthError,
    default_user_agent,
    fetch_stashes_with_items,
    refresh_access_token,
    token_expired_or_soon,
)

ROOT = Path(__file__).resolve().parent
POB_DIR = ROOT / "PathOfBuilding"
POB_SPEC_DIR = POB_DIR / "spec"
HEADLESS_RUNNER = Path.home() / ".codex" / "skills" / "headless-pob-usage" / "scripts" / "run_headless_pob.sh"
EXTRACTOR_LUA = "../spec/ExtractPanelStatsFromSnapshot.lua"


def env_first(*keys: str, default: str | None = None) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value is not None and value != "":
            return value
    return default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PoE character stat watcher with PoB panel deltas.")
    parser.add_argument("--account", default=env_first("DEFAULT_ACCOUNT", "POE_DEFAULT_ACCOUNT"), help="PoE account (Name#1234)")
    parser.add_argument("--realm", default=env_first("DEFAULT_REALM", "POE_DEFAULT_REALM", default="pc"), choices=["pc", "xbox", "sony"])
    parser.add_argument("--character", default=env_first("DEFAULT_CHARACTER", "POE_DEFAULT_CHARACTER"), help="Character name")
    parser.add_argument("--poesessid", default=os.environ.get("POESESSID"), help="POESESSID if needed for private profile")
    parser.add_argument("--league", default=None, help="Optional league override")
    parser.add_argument(
        "--state-dir",
        default="logs/stat_watch",
        help="Directory for latest snapshot/stats and history",
    )
    parser.add_argument(
        "--reset-baseline",
        action="store_true",
        help="Reset baseline before capture (treat this run as first snapshot)",
    )
    parser.add_argument(
        "--include-storage",
        action="store_true",
        help="Attempt stash (storage) fetch and include in snapshot (typically requires POESESSID).",
    )
    parser.add_argument(
        "--stash-tab-index",
        type=int,
        default=None,
        help="Optional stash tab index to fetch item list for a specific tab.",
    )
    parser.add_argument(
        "--oauth-token-file",
        default=env_first("OAUTH_TOKEN_FILE", "POE_OAUTH_TOKEN_FILE", default="logs/poe_oauth_token.json"),
        help="PoE OAuth token JSON path (from poe_oauth_login.py).",
    )
    parser.add_argument(
        "--oauth-client-id",
        default=env_first("OAUTH_CLIENT_ID", "POE_OAUTH_CLIENT_ID"),
        help="PoE OAuth client_id (used for token refresh).",
    )
    parser.add_argument(
        "--oauth-client-secret",
        default=env_first("OAUTH_CLIENT_SECRET", "POE_OAUTH_CLIENT_SECRET"),
        help="Optional PoE OAuth client_secret (used for token refresh).",
    )
    parser.add_argument(
        "--oauth-contact",
        default=env_first("OAUTH_CONTACT", "POE_OAUTH_CONTACT", default="local-user"),
        help="Contact string for OAuth API User-Agent.",
    )
    return parser.parse_args()


def require_args(args: argparse.Namespace) -> None:
    missing = []
    if not args.account:
        missing.append("--account or DEFAULT_ACCOUNT")
    if not args.character:
        missing.append("--character or DEFAULT_CHARACTER")
    if missing:
        raise SystemExit(f"Missing required values: {', '.join(missing)}")


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        details = "\n".join(x for x in [f"STDERR:\n{stderr}" if stderr else "", f"STDOUT:\n{stdout}" if stdout else ""] if x)
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{details}")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def archive_timestamp(timestamp_utc: str) -> str:
    compact = timestamp_utc.replace("-", "").replace(":", "")
    compact = compact.replace("+00:00", "Z")
    compact = compact.replace(".", "_")
    return compact


def archive_snapshot_artifacts(
    *,
    state_dir: Path,
    char_slug: str,
    timestamp_utc: str,
    snapshot_doc: dict[str, Any],
    panel_stats: dict[str, Any],
    history_record: dict[str, Any],
) -> dict[str, Path]:
    archive_dir = state_dir / "archive" / char_slug
    stem = archive_timestamp(timestamp_utc)
    snapshot_archive = archive_dir / f"{stem}_snapshot.json"
    panel_stats_archive = archive_dir / f"{stem}_panel_stats.json"
    delta_archive = archive_dir / f"{stem}_delta.json"
    save_json(snapshot_archive, snapshot_doc)
    save_json(panel_stats_archive, panel_stats)
    save_json(delta_archive, history_record)
    return {
        "archive_dir": archive_dir,
        "snapshot": snapshot_archive,
        "panel_stats": panel_stats_archive,
        "delta": delta_archive,
    }


def item_label(item: dict[str, Any]) -> str:
    name = str(item.get("name", "")).strip()
    type_line = str(item.get("typeLine", "")).strip()
    return f"{name} {type_line}".strip() if name else type_line or "Unknown Item"


def build_inventory_summary(items_payload: dict[str, Any]) -> dict[str, Any]:
    raw_items = items_payload.get("items", [])
    if not isinstance(raw_items, list):
        raw_items = []

    equipped: dict[str, str] = {}
    flasks: list[str] = []
    backpack: list[str] = []
    socketed_gems: list[dict[str, str]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        inv = str(item.get("inventoryId", "")).strip()
        label = item_label(item)

        if inv == "MainInventory":
            backpack.append(label)
        elif inv.startswith("Flask"):
            flasks.append(label)
        else:
            equipped[inv or "UnknownSlot"] = label

        for gem in item.get("socketedItems", []) or []:
            if not isinstance(gem, dict):
                continue
            socketed_gems.append(
                {
                    "host_slot": inv or "UnknownSlot",
                    "gem": item_label(gem),
                }
            )

    return {
        "counts": {
            "total_items": len(raw_items),
            "equipped_slots": len(equipped),
            "flasks": len(flasks),
            "backpack_items": len(backpack),
            "socketed_gems": len(socketed_gems),
        },
        "equipped": equipped,
        "flasks": flasks,
        "backpack": backpack,
        "socketed_gems": socketed_gems,
    }


def build_storage_summary(stash_payload: dict[str, Any]) -> dict[str, Any]:
    items = stash_payload.get("items", [])
    if not isinstance(items, list):
        items = []

    tabs = stash_payload.get("tabs", [])
    if not isinstance(tabs, list):
        tabs = []

    tab_map: dict[int, dict[str, Any]] = {}
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        idx = tab.get("i")
        if isinstance(idx, int):
            tab_map[idx] = {
                "name": tab.get("n"),
                "type": tab.get("type"),
                "color": tab.get("colour"),
            }

    by_tab: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        idx = item.get("inventoryId")
        key = str(idx if idx is not None else "unknown")
        by_tab.setdefault(key, []).append(item_label(item))

    return {
        "counts": {
            "tabs_returned": len(tabs),
            "items_returned": len(items),
        },
        "tabs": tab_map,
        "items_by_inventory_id": by_tab,
    }


def load_oauth_token(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_oauth_token(path: Path, token: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(token, f, indent=2)


def flatten_stats(group: str, payload: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(f"{prefix}.{key}" if prefix else key, value)
            return
        if isinstance(node, (int, float)):
            out[prefix] = float(node)

    walk(group, payload)
    return out


def diff_numeric(current: dict[str, Any], previous: dict[str, Any] | None) -> list[tuple[str, float, float, float]]:
    cur_map: dict[str, float] = {}
    prev_map: dict[str, float] = {}
    for group in ("offence", "defence", "misc", "charges"):
        cur_map.update(flatten_stats(group, current.get(group, {})))
        if previous:
            prev_map.update(flatten_stats(group, previous.get(group, {})))

    keys = sorted(set(cur_map) | set(prev_map))
    diffs: list[tuple[str, float, float, float]] = []
    for key in keys:
        before = prev_map.get(key, 0.0)
        after = cur_map.get(key, 0.0)
        delta = after - before
        if abs(delta) > 1e-9:
            diffs.append((key, before, after, delta))
    return diffs


def diff_equipped(current: dict[str, Any], previous: dict[str, Any] | None) -> list[str]:
    cur_eq = current.get("equipped", {}) if isinstance(current.get("equipped"), dict) else {}
    prev_eq = previous.get("equipped", {}) if previous and isinstance(previous.get("equipped"), dict) else {}
    changes: list[str] = []
    for slot in sorted(set(cur_eq) | set(prev_eq)):
        before = str(prev_eq.get(slot, "None"))
        after = str(cur_eq.get(slot, "None"))
        if before != after:
            changes.append(f"{slot}: {before} -> {after}")
    return changes


def format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def main() -> int:
    args = parse_args()
    require_args(args)

    state_dir = ROOT / args.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)

    char_slug = slugify(args.character)
    snapshot_path = state_dir / f"{char_slug}_snapshot.json"
    stats_path = state_dir / f"{char_slug}_panel_stats.json"
    history_path = state_dir / f"{char_slug}_history.jsonl"

    if args.reset_baseline and stats_path.exists():
        stats_path.unlink()

    previous_stats = load_json(stats_path) if stats_path.exists() else None

    pipeline_cmd = [
        sys.executable,
        "poe_market_pipeline.py",
        "--account",
        args.account,
        "--realm",
        args.realm,
        "--character",
        args.character,
        "--include-passive",
        "--output",
        str(snapshot_path),
        "--dry-run",
        "--no-discord",
    ]
    if args.league:
        pipeline_cmd.extend(["--league", args.league])
    if args.poesessid:
        pipeline_cmd.extend(["--poesessid", args.poesessid])

    run(pipeline_cmd)

    snapshot_doc = load_json(snapshot_path)
    items_payload = snapshot_doc.get("items", {})
    if not isinstance(items_payload, dict):
        items_payload = {}
    snapshot_doc["character_state"] = {
        "zone": None,
        "zone_known": False,
        "zone_source": "Path of Exile character-window account/items APIs did not return a location field.",
    }
    snapshot_doc["inventory_summary"] = build_inventory_summary(items_payload)

    if args.include_storage:
        league = str(snapshot_doc.get("character", {}).get("league", "")).strip() or str(
            snapshot_doc.get("items", {}).get("character", {}).get("league", "")
        ).strip()
        storage_doc: dict[str, Any] = {
            "enabled": True,
            "league": league,
            "tab_index": args.stash_tab_index,
            "raw": None,
            "summary": None,
            "error": None,
            "source": None,
        }

        oauth_token_path = Path(args.oauth_token_file)
        if oauth_token_path.exists():
            try:
                token = load_oauth_token(oauth_token_path)
                client_id = args.oauth_client_id or str(token.get("client_id", "")).strip()
                if not client_id:
                    raise PoeOAuthError(
                        "OAuth token exists but client_id is missing. Set --oauth-client-id or include client_id in token file."
                    )
                if token_expired_or_soon(token):
                    token = refresh_access_token(token, client_id=client_id, client_secret=args.oauth_client_secret)
                    token["client_id"] = client_id
                    save_oauth_token(oauth_token_path, token)
                access_token = str(token.get("access_token", "")).strip()
                if not access_token:
                    raise PoeOAuthError("OAuth token file has no access_token.")
                ua = default_user_agent(client_id, args.oauth_contact)
                stash_all = fetch_stashes_with_items(
                    access_token=access_token,
                    realm=args.realm,
                    league=league,
                    user_agent=ua,
                )
                storage_doc["raw"] = stash_all
                # Convert to flat summary similar to old storage summary.
                stashes_full = stash_all.get("stashes_full", [])
                if not isinstance(stashes_full, list):
                    stashes_full = []
                items_flat: list[dict[str, Any]] = []
                tabs_meta: list[dict[str, Any]] = []
                for tab in stashes_full:
                    if not isinstance(tab, dict):
                        continue
                    tabs_meta.append(tab)
                    for item in tab.get("items", []) or []:
                        if isinstance(item, dict):
                            # preserve stash provenance in summary
                            item_copy = dict(item)
                            item_copy["_stash_name"] = tab.get("name")
                            item_copy["_stash_id"] = tab.get("id")
                            items_flat.append(item_copy)
                storage_doc["summary"] = build_storage_summary(
                    {
                        "tabs": [{"i": i, "n": t.get("name"), "type": t.get("type")} for i, t in enumerate(tabs_meta)],
                        "items": items_flat,
                    }
                )
                storage_doc["source"] = "oauth"
            except PoeOAuthError as exc:
                storage_doc["error"] = str(exc)

        # Fallback to cookie flow when OAuth was not used or failed.
        if storage_doc["summary"] is None and storage_doc["error"] is None:
            try:
                normalized_account = normalize_account_name(args.account, args.realm)
                stash = get_stash_items(
                    account_name=normalized_account,
                    realm=args.realm,
                    league=league,
                    poesessid=args.poesessid,
                    tab_index=args.stash_tab_index,
                    tabs=1 if args.stash_tab_index is None else 0,
                )
                storage_doc["raw"] = stash
                storage_doc["summary"] = build_storage_summary(stash)
                storage_doc["source"] = "poesessid"
            except PoeApiError as exc:
                storage_doc["error"] = str(exc)
                storage_doc["source"] = "poesessid"

        snapshot_doc["storage"] = storage_doc
    else:
        snapshot_doc["storage"] = {
            "enabled": False,
            "error": "Storage fetch disabled. Use --include-storage.",
        }
    save_json(snapshot_path, snapshot_doc)

    pob_snapshot = POB_SPEC_DIR / "current_snapshot.json"
    pob_stats = POB_SPEC_DIR / "current_panel_stats.json"
    shutil.copyfile(snapshot_path, pob_snapshot)

    run(
        [
            str(HEADLESS_RUNNER),
            "--pob-dir",
            str(POB_DIR),
            "--lua-script",
            EXTRACTOR_LUA,
        ]
    )
    current_stats = load_json(pob_stats)
    save_json(stats_path, current_stats)

    numeric_diffs = diff_numeric(current_stats, previous_stats)
    equipped_diffs = diff_equipped(current_stats, previous_stats)

    ts = datetime.now(timezone.utc).isoformat()
    record = {
        "timestamp_utc": ts,
        "account": args.account,
        "realm": args.realm,
        "character": args.character,
        "equipped_changes": equipped_diffs,
        "stat_changes": [
            {"stat": name, "before": before, "after": after, "delta": delta}
            for name, before, after, delta in numeric_diffs
        ],
    }
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True) + "\n")

    archived_paths = archive_snapshot_artifacts(
        state_dir=state_dir,
        char_slug=char_slug,
        timestamp_utc=ts,
        snapshot_doc=snapshot_doc,
        panel_stats=current_stats,
        history_record=record,
    )

    update_from_stat_watch(
        character_name=args.character,
        account=args.account,
        realm=args.realm,
        snapshot_doc=snapshot_doc,
        panel_stats=current_stats,
        history_record=record,
        snapshot_path=snapshot_path,
        stats_path=stats_path,
        history_path=history_path,
        archived_paths=archived_paths,
    )

    print(f"Character: {args.character} ({args.realm})")
    print(f"Captured: {ts}")
    print("")
    if previous_stats is None:
        print("Baseline established. No prior snapshot for delta.")
    else:
        if equipped_diffs:
            print("Equipped changes:")
            for line in equipped_diffs:
                print(f"- {line}")
            print("")
        if numeric_diffs:
            print("Panel deltas:")
            for stat, before, after, delta in numeric_diffs:
                sign = "+" if delta > 0 else ""
                print(
                    f"- {stat}: {format_number(before)} -> {format_number(after)} ({sign}{format_number(delta)})"
                )
        else:
            print("No panel stat changes since last snapshot.")

    print("")
    inv = snapshot_doc.get("inventory_summary", {})
    if isinstance(inv, dict):
        counts = inv.get("counts", {})
        if isinstance(counts, dict):
            print(
                "Inventory summary: "
                f"{counts.get('total_items', 0)} total, "
                f"{counts.get('equipped_slots', 0)} equipped slots, "
                f"{counts.get('flasks', 0)} flasks, "
                f"{counts.get('backpack_items', 0)} backpack items, "
                f"{counts.get('socketed_gems', 0)} socketed gems."
            )
    print("Zone: unknown (not exposed by the character-window endpoints used here).")
    storage = snapshot_doc.get("storage", {})
    if isinstance(storage, dict) and storage.get("enabled"):
        summary = storage.get("summary")
        error = storage.get("error")
        source = storage.get("source")
        if error:
            print(f"Storage: unavailable ({error})")
        elif isinstance(summary, dict):
            counts = summary.get("counts", {})
            if isinstance(counts, dict):
                print(
                    "Storage summary: "
                    f"{counts.get('tabs_returned', 0)} tabs metadata, "
                    f"{counts.get('items_returned', 0)} items returned"
                    f"{f' (source={source})' if source else ''}."
                )
    print("")
    print(f"Saved snapshot: {snapshot_path}")
    print(f"Saved panel stats: {stats_path}")
    print(f"Appended history: {history_path}")
    print(f"Archived snapshot set: {archived_paths['archive_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
