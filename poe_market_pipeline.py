#!/usr/bin/env python3
"""Sync PoE character data, enrich with market prices, and post persona updates to Discord."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from character_ledger import update_from_market_snapshot
from discord_persona_sender.discord_persona_sender import build_persona_message, send_discord_message
from discord_persona_sender.discord_publish_log import DEFAULT_LOG_PATH
from poe_character_sync import (
    PoeApiError,
    choose_character,
    get_canonical_account_name,
    get_characters,
    get_items,
    get_passive_skills,
    normalize_account_name,
)

MARKET_HOST = "https://poe.ninja"
UNIQUE_ITEM_TYPES = ["UniqueWeapon", "UniqueArmour", "UniqueAccessory", "UniqueFlask", "UniqueJewel"]
CURRENCY_TYPES = ["Currency", "Fragment"]


@dataclass
class PricedHolding:
    label: str
    quantity: int
    chaos_value: float
    source: str


def http_get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (PoE Assistant)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"Market request failed (HTTP {exc.code}): {body[:200]}") from exc


def fetch_currency_prices(league: str) -> dict[str, float]:
    prices: dict[str, float] = {}
    for currency_type in CURRENCY_TYPES:
        query = urllib.parse.urlencode({"league": league, "type": currency_type})
        url = f"{MARKET_HOST}/poe1/api/economy/stash/current/currency/overview?{query}"
        payload = http_get_json(url)
        for line in payload.get("lines", []):
            name = str(line.get("currencyTypeName", "")).strip()
            chaos = line.get("chaosEquivalent")
            if not name or not isinstance(chaos, (int, float)):
                continue
            prices[name.lower()] = float(chaos)
    return prices


def fetch_unique_prices(league: str) -> dict[str, float]:
    prices: dict[str, float] = {}
    for item_type in UNIQUE_ITEM_TYPES:
        query = urllib.parse.urlencode({"league": league, "type": item_type})
        url = f"{MARKET_HOST}/poe1/api/economy/stash/current/item/overview?{query}"
        payload = http_get_json(url)
        for line in payload.get("lines", []):
            chaos = line.get("chaosValue")
            if not isinstance(chaos, (int, float)):
                continue
            key = str(line.get("name", "")).strip().lower()
            if not key:
                continue
            current = prices.get(key)
            candidate = float(chaos)
            if current is None or candidate > current:
                prices[key] = candidate
    return prices


def fetch_div_card_prices(league: str) -> dict[str, float]:
    prices: dict[str, float] = {}
    query = urllib.parse.urlencode({"league": league, "type": "DivinationCard"})
    url = f"{MARKET_HOST}/poe1/api/economy/stash/current/item/overview?{query}"
    payload = http_get_json(url)
    for line in payload.get("lines", []):
        chaos = line.get("chaosValue")
        if not isinstance(chaos, (int, float)):
            continue
        key = str(line.get("name", "")).strip().lower()
        if not key:
            continue
        prices[key] = float(chaos)
    return prices


def get_league(selected_char: dict[str, Any], items_payload: dict[str, Any], override: str | None) -> str:
    if override:
        return override

    char_league = str(selected_char.get("league", "")).strip()
    if char_league:
        return char_league

    for item in items_payload.get("items", []):
        league = str(item.get("league", "")).strip()
        if league:
            return league

    return "Standard"


def compact_item_label(item: dict[str, Any]) -> str:
    name = str(item.get("name", "")).strip()
    base = str(item.get("typeLine", "")).strip()
    if name and base:
        return f"{name} {base}"
    return name or base or "Unknown Item"


def estimate_holdings(
    items_payload: dict[str, Any],
    currency_prices: dict[str, float],
    unique_prices: dict[str, float],
    div_card_prices: dict[str, float],
) -> tuple[list[PricedHolding], int, int]:
    priced: list[PricedHolding] = []
    total_items = 0
    priced_items = 0

    for item in items_payload.get("items", []):
        total_items += 1
        quantity = int(item.get("stackSize") or 1)
        type_line = str(item.get("typeLine", "")).strip().lower()
        name = str(item.get("name", "")).strip().lower()
        rarity = str(item.get("rarity", "")).strip().lower()
        frame_type = item.get("frameType")

        chaos_value: float | None = None
        source = ""

        if type_line in currency_prices:
            chaos_value = quantity * currency_prices[type_line]
            source = "currency"
        elif rarity == "unique" and name and name in unique_prices:
            chaos_value = quantity * unique_prices[name]
            source = "item-name"
        elif frame_type == 6 and type_line in div_card_prices:
            chaos_value = quantity * div_card_prices[type_line]
            source = "div-card"

        if chaos_value is None or chaos_value <= 0:
            continue

        priced_items += 1
        priced.append(
            PricedHolding(
                label=compact_item_label(item),
                quantity=quantity,
                chaos_value=chaos_value,
                source=source,
            )
        )

    priced.sort(key=lambda p: p.chaos_value, reverse=True)
    return priced, priced_items, total_items


def build_persona_posts(
    selected_char_name: str,
    league: str,
    priced_holdings: list[PricedHolding],
    priced_count: int,
    total_count: int,
) -> list[str]:
    today = datetime.now(timezone.utc).date().isoformat()
    total_chaos = sum(p.chaos_value for p in priced_holdings)
    top3 = priced_holdings[:3]

    top_line = "; ".join(f"{h.label} x{h.quantity} ~{h.chaos_value:.1f}c" for h in top3) or "No priced holdings yet"
    coverage = (priced_count / total_count * 100.0) if total_count else 0.0

    log_message = build_persona_message(
        "LOG",
        f"{selected_char_name}/{league}/{today}",
        f"Market sync complete. Priced {priced_count}/{total_count} items ({coverage:.0f}% coverage). Estimated known value {total_chaos:.1f} chaos. Top: {top_line}.",
    )

    learn_message = build_persona_message(
        "LEARN",
        "market-coverage",
        f"Direct pricing works best for currency/uniques/div cards. Current coverage is {coverage:.0f}%, so rare-crafted gear still needs manual trade checks.",
    )

    if top3:
        next_body = f"List-test {top3[0].label} first, then run one upgrade search scoped to a {max(20, int(total_chaos * 0.2))}c budget from priced assets."
    else:
        next_body = "Farm one inventory tab of liquid currency, then rerun sync to establish a spendable budget baseline."

    next_message = build_persona_message("NEXT", "P1", next_body)
    return [log_message, learn_message, next_message]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PoE character + market sync pipeline for Discord persona updates")
    parser.add_argument("--sheet-json", default=None, help="Use an existing character sheet JSON instead of live PoE API fetch")
    parser.add_argument("--account", required=True, help='PoE account, e.g. "Name#1234"')
    parser.add_argument("--realm", default="pc", choices=["pc", "xbox", "sony"], help="Realm")
    parser.add_argument("--character", default=None, help="Character name (defaults to first character)")
    parser.add_argument("--poesessid", default=None, help="POESESSID cookie (if required)")
    parser.add_argument("--league", default=None, help="Override league for market pricing")
    parser.add_argument("--include-passive", action="store_true", help="Include passive payload in output JSON")
    parser.add_argument("--webhook-url", default=os.environ.get("DISCORD_WEBHOOK_URL"), help="Discord webhook URL")
    parser.add_argument("--discord-username", default="OpenClawZeroZeroZero", help="Webhook display name")
    parser.add_argument("--log-path", default=DEFAULT_LOG_PATH, help="Append-only publish history JSONL path")
    parser.add_argument("--output", default=None, help="Write combined JSON to this path")
    parser.add_argument("--dry-run", action="store_true", help="Do not post to Discord; print messages")
    parser.add_argument("--no-discord", action="store_true", help="Skip Discord posting entirely")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.no_discord and not args.dry_run and not args.webhook_url:
        print("ERROR: Provide --webhook-url or set DISCORD_WEBHOOK_URL (or use --dry-run/--no-discord).", file=sys.stderr)
        return 2

    try:
        normalized_account = normalize_account_name(args.account, args.realm)
        canonical_account = normalized_account
        selected_name = args.character or ""
        passive_payload: dict[str, Any] | None = None

        if args.sheet_json:
            with open(args.sheet_json, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
            items_payload = snapshot.get("items", {})
            if not isinstance(items_payload, dict):
                raise RuntimeError("sheet JSON is missing a valid 'items' object")
            selected_name = selected_name or str(snapshot.get("character", "")).strip() or "UnknownCharacter"
            selected = {"name": selected_name, "league": snapshot.get("league", "")}
            if args.include_passive:
                passive_payload = snapshot.get("passive_skills")
        else:
            characters = get_characters(normalized_account, args.realm, args.poesessid)
            try:
                canonical_account = get_canonical_account_name(normalized_account, args.poesessid)
            except PoeApiError:
                canonical_account = normalized_account

            selected = choose_character(characters, args.character)
            if selected is None:
                if args.character:
                    raise PoeApiError(f"Character '{args.character}' was not found.")
                raise PoeApiError("No characters found for this account.")

            selected_name = str(selected.get("name", ""))
            items_payload = get_items(canonical_account, selected_name, args.realm, args.poesessid)
            if args.include_passive:
                passive_payload = get_passive_skills(canonical_account, selected_name, args.realm, args.poesessid)

        league = get_league(selected, items_payload, args.league)
        currency_prices = fetch_currency_prices(league)
        unique_prices = fetch_unique_prices(league)
        div_card_prices = fetch_div_card_prices(league)
        priced_holdings, priced_count, total_count = estimate_holdings(
            items_payload,
            currency_prices,
            unique_prices,
            div_card_prices,
        )

        posts = build_persona_posts(selected_name, league, priced_holdings, priced_count, total_count)

        output = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "account": {
                "input": args.account,
                "normalized": normalized_account,
                "canonical": canonical_account,
                "realm": args.realm,
            },
            "character": {
                "name": selected_name,
                "league": league,
            },
            "pricing_summary": {
                "priced_items": priced_count,
                "total_items": total_count,
                "known_value_chaos": round(sum(p.chaos_value for p in priced_holdings), 2),
                "top_holdings": [
                    {
                        "label": p.label,
                        "quantity": p.quantity,
                        "chaos_value": round(p.chaos_value, 2),
                        "source": p.source,
                    }
                    for p in priced_holdings[:10]
                ],
            },
            "posts": posts,
            "items": items_payload,
        }
        if passive_payload is not None:
            output["passive_skills"] = passive_payload

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2)
            update_from_market_snapshot(output, Path(args.output))
        else:
            update_from_market_snapshot(output)

        if args.dry_run or args.no_discord:
            for post in posts:
                print(post)
            return 0

        for post in posts:
            send_discord_message(
                args.webhook_url,
                post,
                args.discord_username,
                source="poe_market_pipeline",
                metadata={
                    "account": args.account,
                    "character": selected_name,
                    "league": league,
                },
                log_path=args.log_path,
            )

        print(f"Posted market sync persona updates to Discord. Logged at {args.log_path}.")
        return 0

    except PoeApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"UNEXPECTED ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
