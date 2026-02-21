#!/usr/bin/env python3
"""Create and post a polished PoE build intelligence card to Discord."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from discord_publish_log import DEFAULT_LOG_PATH, append_publish_history
from poe_market_pipeline import estimate_holdings, fetch_currency_prices, fetch_div_card_prices, fetch_unique_prices


def read_snapshot(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_build_signals(items_payload: dict[str, Any]) -> dict[str, Any]:
    items = items_payload.get("items", [])
    gems: list[tuple[str, bool, str]] = []
    equipped = {it.get("inventoryId"): it for it in items if it.get("inventoryId")}

    for item in items:
        inv = str(item.get("inventoryId", ""))
        for gem in item.get("socketedItems", []) or []:
            gem_name = str(gem.get("typeLine") or gem.get("baseType") or gem.get("name") or "").strip()
            if gem_name:
                gems.append((gem_name, bool(gem.get("support")), inv))

    skill_names = [g[0] for g in gems if not g[1]]
    support_names = [g[0] for g in gems if g[1]]

    core_attack = "Power Siphon" if any("Power Siphon" in s for s in skill_names) else None
    secondary_attack = "Kinetic Bolt" if any("Kinetic Bolt" in s for s in skill_names) else None

    utility = []
    for key in ["Sniper's Mark", "Flame Wall", "Sigil of Power", "Frostblink", "Precision", "Spellslinger Support"]:
        if any(key in s for s in skill_names + support_names):
            utility.append(key.replace(" Support", ""))

    gear = {
        slot: f"{str(item.get('name', '')).strip()} {str(item.get('typeLine', '')).strip()}".strip()
        for slot, item in equipped.items()
        if slot in {"Weapon", "Offhand", "Helm", "BodyArmour", "Gloves", "Boots", "Belt", "Amulet", "Ring", "Ring2"}
    }

    return {
        "gems": gems,
        "skill_names": skill_names,
        "support_names": support_names,
        "core_attack": core_attack,
        "secondary_attack": secondary_attack,
        "utility": utility,
        "gear": gear,
    }


def with_wait_query(webhook_url: str) -> str:
    parsed = urllib.parse.urlsplit(webhook_url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query["wait"] = "true"
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))


def post_discord_embed(
    webhook_url: str,
    username: str,
    content: str,
    embeds: list[dict[str, Any]],
    *,
    log_path: str = DEFAULT_LOG_PATH,
) -> None:
    payload = {
        "username": username,
        "content": content,
        "embeds": embeds,
        "allowed_mentions": {"parse": []},
    }
    req = urllib.request.Request(
        with_wait_query(webhook_url),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "PoE-Assistant/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(f"Webhook post failed with HTTP {resp.status}")
            raw = resp.read().decode("utf-8", errors="replace")
            discord_message = json.loads(raw) if raw else None
            append_publish_history(
                source="post_build_intel_card",
                webhook_url=webhook_url,
                username=username,
                content=content,
                embeds=embeds,
                discord_message=discord_message if isinstance(discord_message, dict) else None,
                metadata={"card_type": "build_intelligence"},
                log_path=log_path,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"Webhook post failed with HTTP {exc.code}: {body}") from exc


def make_card(snapshot: dict[str, Any], league_override: str | None = None) -> dict[str, Any]:
    char = snapshot.get("items", {}).get("character", {})
    items_payload = snapshot.get("items", {})
    league = league_override or char.get("league") or snapshot.get("league") or "Standard"

    build = extract_build_signals(items_payload)

    currency_prices = fetch_currency_prices(league)
    unique_prices = fetch_unique_prices(league)
    div_prices = fetch_div_card_prices(league)
    priced_holdings, priced_count, total_count = estimate_holdings(items_payload, currency_prices, unique_prices, div_prices)

    top_holdings = priced_holdings[:3]
    holdings_line = "\n".join([f"- {h.label}: ~{h.chaos_value:.1f}c" for h in top_holdings]) or "- No liquid holdings identified yet"

    core_lines = []
    if build["core_attack"]:
        core_lines.append(f"- Main attack: **{build['core_attack']}**")
    if build["secondary_attack"]:
        core_lines.append(f"- Secondary attack: **{build['secondary_attack']}**")
    if build["utility"]:
        core_lines.append(f"- Utility stack: {', '.join(build['utility'])}")

    weapon = build["gear"].get("Weapon", "Unknown Weapon")
    offhand = build["gear"].get("Offhand", "Unknown Offhand")
    belt = build["gear"].get("Belt", "Unknown Belt")
    helm = build["gear"].get("Helm", "Unknown Helm")

    level = char.get("level", "?")
    class_name = char.get("class", "?")
    character_name = char.get("name", "Unknown")

    weapon_l = weapon.lower()
    offhand_l = offhand.lower()
    skills_blob = " ".join(build["skill_names"]).lower()
    has_wand_signal = any(k in skills_blob for k in ["power siphon", "kinetic bolt"])
    has_melee_signal = any(k in f"{weapon_l} {offhand_l}" for k in ["axe", "sword", "mace", "claw"])
    has_block_signal = "shield" in offhand_l

    if not core_lines:
        fallback = "shielded melee posture" if has_melee_signal and has_block_signal else "mixed leveling setup"
        core_lines.append(f"- {fallback} detected; no single endgame skill locked yet")

    if has_wand_signal:
        intent = (
            "Projectile attack profile with utility support. Current links suggest a transition phase before a tighter endgame setup."
        )
        next_actions = (
            "1) Commit to one primary projectile attack setup and remove split scaling paths.\n"
            "2) Improve life/res on rares first, then tune offensive supports around your chosen main link.\n"
            "3) Liquidate one priced unique and fund a focused weapon upgrade search."
        )
    elif has_melee_signal:
        intent = (
            "Melee weapon profile with frontline mapping posture. Gear points to a durable close-range setup rather than a caster shell."
        )
        next_actions = (
            "1) Lock your main melee skill into a stable 4-link/5-link and align supports to one damage type.\n"
            "2) Prioritize weapon pDPS upgrade path first; then patch resist/life deficits on armor and jewelry.\n"
            "3) If using shield, add one defensive layer (block, spell suppression, or guard uptime) before pushing harder content."
        )
    else:
        intent = (
            "Hybrid/transition profile detected. Current gem and gear signals look mid-migration rather than fully specialized."
        )
        next_actions = (
            "1) Pick one core skill package and remove off-plan gem branches.\n"
            "2) Upgrade weakest rare slots to stabilize defenses before adding more damage.\n"
            "3) Convert one liquid unique into chaos and run one targeted upgrade search for your main skill path."
        )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    embed = {
        "title": f"OpenClawZeroZeroZero // Build Intelligence Card",
        "description": f"**{character_name}** | Level **{level}** {class_name} | League **{league}**\nUpdated {now}",
        "color": 0xE67E22,
        "fields": [
            {"name": "Build Identity", "value": "\n".join(core_lines), "inline": False},
            {"name": "What The Build Is Trying To Do", "value": intent, "inline": False},
            {
                "name": "Current Gear Signal",
                "value": f"- Weapon: {weapon}\n- Offhand: {offhand}\n- Belt: {belt}\n- Helm: {helm}",
                "inline": False,
            },
            {
                "name": "Liquid Market Snapshot",
                "value": f"Priced {priced_count}/{total_count} items\n{holdings_line}",
                "inline": False,
            },
            {"name": "Next 3 Moves", "value": next_actions, "inline": False},
        ],
        "footer": {"text": "OpenClawZeroZeroZero Memory+Market Product"},
    }

    return {
        "content": "[LOG][build-intel] Character-aware product card generated from live gear + skills + market context.",
        "embed": embed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Post a build intelligence card to Discord")
    parser.add_argument("--snapshot", required=True, help="Path to market snapshot JSON (from poe_market_pipeline.py --output)")
    parser.add_argument("--webhook-url", default=os.environ.get("DISCORD_WEBHOOK_URL"), help="Discord webhook URL")
    parser.add_argument("--username", default="OpenClawZeroZeroZero", help="Webhook username")
    parser.add_argument("--league", default=None, help="Override league for pricing")
    parser.add_argument("--log-path", default=DEFAULT_LOG_PATH, help="Append-only publish history JSONL path")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without posting")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.dry_run and not args.webhook_url:
        print("ERROR: Provide --webhook-url or set DISCORD_WEBHOOK_URL.", file=sys.stderr)
        return 2

    snapshot = read_snapshot(args.snapshot)
    card = make_card(snapshot, league_override=args.league)

    if args.dry_run:
        print(json.dumps(card, indent=2))
        return 0

    post_discord_embed(args.webhook_url, args.username, card["content"], [card["embed"]], log_path=args.log_path)
    print(f"Posted build intelligence card to Discord. Logged at {args.log_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
