#!/usr/bin/env python3
"""Compare how an item swap changes damage metrics for a selected skill using headless PoB."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POB_DIR = REPO_ROOT / "PathOfBuilding"
DEFAULT_RUNNER = Path.home() / ".codex" / "skills" / "headless-pob-usage" / "scripts" / "run_headless_pob.sh"
TEMPLATE_PATH = Path(__file__).resolve().parent / "skill_damage_item_swap.lua.template"
DEFAULT_METRIC_KEYS = [
    "CombinedDPS",
    "TotalDPS",
    "IgniteDPS",
    "WithIgniteDPS",
    "TotalDotDPS",
    "AverageHit",
    "Speed",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pob-dir", default=str(DEFAULT_POB_DIR), help="Path to PathOfBuilding checkout")
    p.add_argument("--runner", default=str(DEFAULT_RUNNER), help="Path to run_headless_pob.sh")
    p.add_argument("--snapshot", default=None, help="Snapshot JSON (default: <pob-dir>/spec/current_snapshot.json)")
    p.add_argument("--candidate-item-file", default=None, help="Path to pasted PoE item text")
    p.add_argument("--candidate-item-text", default=None, help="Raw pasted PoE item text")
    p.add_argument("--slot", required=True, help="Inventory slot to replace (for example Weapon, Offhand, Helmet)")
    p.add_argument("--skill-name", required=True, help="Skill name to force-select for damage calculations")
    p.add_argument("--metric-keys", default=",".join(DEFAULT_METRIC_KEYS), help="Comma-separated metric keys to compare")
    p.add_argument("--primary-metric", default="CombinedDPS", help="Metric key used for winner summary")
    p.add_argument("--output", default=None, help="Output JSON path (default: <pob-dir>/spec/skill_damage_item_compare.json)")
    p.add_argument("--keep-temp", action="store_true", help="Keep generated temp files under PathOfBuilding/spec")
    return p.parse_args()


def read_text_arg(args: argparse.Namespace) -> str:
    if bool(args.candidate_item_file) == bool(args.candidate_item_text):
        raise SystemExit("Provide exactly one of --candidate-item-file or --candidate-item-text")
    if args.candidate_item_file:
        return Path(args.candidate_item_file).read_text(encoding="utf-8")
    return str(args.candidate_item_text)


def split_sections(text: str) -> list[list[str]]:
    sections: list[list[str]] = []
    cur: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "--------":
            if cur:
                sections.append(cur)
            cur = []
            continue
        cur.append(line)
    if cur:
        sections.append(cur)
    return sections


def parse_candidate_item(text: str) -> dict[str, Any]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 4:
        raise ValueError("Candidate item text is too short")

    rarity_idx = next((i for i, l in enumerate(lines) if l.startswith("Rarity:")), None)
    if rarity_idx is None or rarity_idx + 2 >= len(lines):
        raise ValueError("Could not parse item name/type from pasted text")

    name = lines[rarity_idx + 1]
    type_line = lines[rarity_idx + 2]

    ilvl_match = re.search(r"Item Level:\s*(\d+)", text)
    ilvl = int(ilvl_match.group(1)) if ilvl_match else None

    sections = split_sections(text)
    implicit_mods: list[str] = []
    explicit_mods: list[str] = []

    implicit_section_idx = None
    for i, sec in enumerate(sections):
        sec_impl = [ln.replace(" (implicit)", "") for ln in sec if "(implicit)" in ln]
        if sec_impl:
            implicit_mods.extend(sec_impl)
            implicit_section_idx = i

    if implicit_section_idx is not None and implicit_section_idx + 1 < len(sections):
        explicit_mods = list(sections[implicit_section_idx + 1])
    elif sections:
        explicit_mods = list(sections[-1])

    skip_prefixes = (
        "Item Class:",
        "Rarity:",
        "Requirements:",
        "Sockets:",
        "Item Level:",
        "Level:",
        "Str:",
        "Dex:",
        "Int:",
        "Quality:",
        "--------",
    )
    explicit_mods = [ln for ln in explicit_mods if not ln.startswith(skip_prefixes) and "(implicit)" not in ln]

    if not explicit_mods and not implicit_mods:
        raise ValueError("No mods parsed from candidate item text")

    out = {
        "name": name,
        "typeLine": type_line,
        "implicitMods": implicit_mods,
        "explicitMods": explicit_mods,
    }
    if ilvl is not None:
        out["ilvl"] = ilvl
    return out


def normalize_slot(slot: str) -> str:
    raw = slot.strip()
    no_space = raw.replace(" ", "")
    aliases = {
        "body armour": "BodyArmour",
        "bodyarmour": "BodyArmour",
        "weapon": "Weapon",
        "mainhand": "Weapon",
        "offhand": "Offhand",
        "shield": "Offhand",
        "helmet": "Helmet",
        "boots": "Boots",
        "gloves": "Gloves",
        "amulet": "Amulet",
        "belt": "Belt",
    }
    if raw.lower() in aliases:
        return aliases[raw.lower()]
    if no_space.lower() in aliases:
        return aliases[no_space.lower()]
    return no_space


def find_slot_item(items: list[dict[str, Any]], slot: str) -> tuple[int, dict[str, Any]]:
    for idx, item in enumerate(items):
        if str(item.get("inventoryId", "")) == slot:
            return idx, item
    raise ValueError(f"No equipped item found for slot '{slot}'")


def build_candidate_snapshot(snapshot: dict[str, Any], candidate: dict[str, Any], slot: str) -> tuple[dict[str, Any], dict[str, Any]]:
    out = json.loads(json.dumps(snapshot))
    items = out.get("items", {}).get("items", [])
    if not isinstance(items, list):
        raise ValueError("Snapshot has invalid items payload")

    idx, equipped = find_slot_item(items, slot)
    item = items[idx]
    item["name"] = candidate["name"]
    item["typeLine"] = candidate["typeLine"]
    item["implicitMods"] = candidate.get("implicitMods", [])
    item["explicitMods"] = candidate.get("explicitMods", [])
    if candidate.get("ilvl") is not None:
        item["ilvl"] = int(candidate["ilvl"])

    for k in ("craftedMods", "fracturedMods", "enchantMods", "scourgeMods"):
        item.pop(k, None)

    items[idx] = item
    return out, equipped


def metric_keys_arg(raw: str) -> list[str]:
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise SystemExit("--metric-keys must include at least one key")
    return keys


def lua_string_literal(v: str) -> str:
    return json.dumps(v)


def lua_table_of_strings(values: list[str]) -> str:
    return "{" + ", ".join(lua_string_literal(v) for v in values) + "}"


def render_lua(template: str, baseline_container: str, candidate_container: str, output_container: str, skill_name: str, metric_keys: list[str]) -> str:
    return (
        template.replace("__BASELINE_PATH__", baseline_container)
        .replace("__CANDIDATE_PATH__", candidate_container)
        .replace("__OUTPUT_PATH__", output_container)
        .replace("__TARGET_SKILL__", skill_name)
        .replace("__METRIC_KEYS_LUA__", lua_table_of_strings(metric_keys))
    )


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )


def pct_change(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before == 0:
        return None
    return ((after - before) / before) * 100.0


def extract_item_view(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "inventoryId": item.get("inventoryId"),
        "name": item.get("name"),
        "typeLine": item.get("typeLine"),
        "implicitMods": item.get("implicitMods", []),
        "explicitMods": item.get("explicitMods", []),
        "ilvl": item.get("ilvl"),
    }


def main() -> int:
    args = parse_args()
    pob_dir = Path(args.pob_dir).resolve()
    runner = Path(args.runner).resolve()
    slot = normalize_slot(args.slot)
    metric_keys = metric_keys_arg(args.metric_keys)

    snapshot = Path(args.snapshot).resolve() if args.snapshot else (pob_dir / "spec" / "current_snapshot.json")
    output = Path(args.output).resolve() if args.output else (pob_dir / "spec" / "skill_damage_item_compare.json")

    if not pob_dir.exists():
        raise SystemExit(f"PoB directory not found: {pob_dir}")
    if not runner.exists():
        raise SystemExit(f"Runner script not found: {runner}")
    if not snapshot.exists():
        raise SystemExit(f"Snapshot not found: {snapshot}")
    if not TEMPLATE_PATH.exists():
        raise SystemExit(f"Lua template missing: {TEMPLATE_PATH}")

    candidate_text = read_text_arg(args)
    candidate_item = parse_candidate_item(candidate_text)

    baseline_doc = json.loads(snapshot.read_text(encoding="utf-8"))
    candidate_doc, equipped_item = build_candidate_snapshot(baseline_doc, candidate_item, slot)

    spec_dir = pob_dir / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)

    if args.keep_temp:
        tmp_root = spec_dir / "_tmp_skill_damage_item_compare"
        tmp_root.mkdir(parents=True, exist_ok=True)
        temp_context = None
    else:
        temp_context = TemporaryDirectory(prefix="skill_damage_item_compare_", dir=spec_dir)
        tmp_root = Path(temp_context.name)

    try:
        baseline_host = tmp_root / "baseline_snapshot.json"
        candidate_host = tmp_root / "candidate_snapshot.json"
        compare_lua_host = tmp_root / "compare_skill_damage_item_swap.lua"
        result_host = tmp_root / "result.json"

        baseline_host.write_text(json.dumps(baseline_doc), encoding="utf-8")
        candidate_host.write_text(json.dumps(candidate_doc), encoding="utf-8")

        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        baseline_container = f"/workdir/spec/{tmp_root.name}/baseline_snapshot.json"
        candidate_container = f"/workdir/spec/{tmp_root.name}/candidate_snapshot.json"
        result_container = f"/workdir/spec/{tmp_root.name}/result.json"
        rendered = render_lua(
            template,
            baseline_container,
            candidate_container,
            result_container,
            args.skill_name,
            metric_keys,
        )
        compare_lua_host.write_text(rendered, encoding="utf-8")

        run(
            [
                str(runner),
                "--pob-dir",
                str(pob_dir),
                "--lua-script",
                f"../spec/{tmp_root.name}/compare_skill_damage_item_swap.lua",
            ]
        )

        result = json.loads(result_host.read_text(encoding="utf-8"))
        result["slot"] = slot
        result["skill_name"] = args.skill_name
        result["baseline_item"] = extract_item_view(equipped_item)
        result["candidate_item"] = candidate_item

        primary_before = result.get("baseline_metrics", {}).get(args.primary_metric)
        primary_after = result.get("candidate_metrics", {}).get(args.primary_metric)
        primary_delta = result.get("deltas", {}).get(args.primary_metric)
        result["summary"] = {
            "primary_metric": args.primary_metric,
            "baseline": primary_before,
            "candidate": primary_after,
            "delta": primary_delta,
            "pct_change": pct_change(primary_before, primary_after),
            "winner": "candidate"
            if isinstance(primary_before, (int, float)) and isinstance(primary_after, (int, float)) and primary_after > primary_before
            else "baseline",
        }

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")

        print(f"Wrote {output}")
        print(
            f"Primary metric {args.primary_metric}: baseline={primary_before} "
            f"candidate={primary_after} delta={primary_delta} "
            f"winner={result['summary']['winner']}"
        )
    finally:
        if temp_context is not None:
            temp_context.cleanup()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
