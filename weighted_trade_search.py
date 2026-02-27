#!/usr/bin/env python3
"""Run a weighted Path of Exile trade search through the shared trade client."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from poe_character_sync import PoeApiError, choose_character, get_characters, normalize_account_name
from trade_api import RATE_LIMIT_LOG_PATH, TradeApiError, fetch_trade_results, post_trade_search


def env_first(*keys: str, default: str | None = None) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value is not None and value != "":
            return value
    return default


def parse_weight(raw: str) -> dict[str, Any]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("Weights must use stat_id=weight format.")
    stat_id, weight_text = raw.split("=", 1)
    stat_id = stat_id.strip()
    if not stat_id:
        raise argparse.ArgumentTypeError("Weight stat id cannot be empty.")
    try:
        weight = float(weight_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid weight value in '{raw}'.") from exc
    return {"id": stat_id, "weight": weight}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a weighted trade search and print a canonical trade link.")
    parser.add_argument("--account", default=env_first("DEFAULT_ACCOUNT"), help="Account name with discriminator.")
    parser.add_argument("--character", default=env_first("DEFAULT_CHARACTER"), help="Character name.")
    parser.add_argument("--realm", default=env_first("DEFAULT_REALM", default="pc"), choices=["pc", "xbox", "sony"])
    parser.add_argument("--league", default=None, help="Optional league override. Defaults to the live character league.")
    parser.add_argument("--poesessid", default=os.environ.get("POESESSID"))
    parser.add_argument("--mode", default=env_first("DEFAULT_TRADE_MODE", default="securable"), choices=["securable", "online"])
    parser.add_argument("--category", required=True, help="Trade category, for example armour.helmet or accessory.ring.")
    parser.add_argument("--rarity", default="rare", help="Trade rarity filter, default: rare.")
    parser.add_argument("--price-max", type=float, default=None, help="Maximum buyout in chaos.")
    parser.add_argument("--weight-min", type=float, default=None, help="Minimum weighted score.")
    parser.add_argument("--weight", action="append", default=[], type=parse_weight, help="Weighted stat in stat_id=weight format. Repeat per stat.")
    parser.add_argument("--fetch-limit", type=int, default=3, help="Number of top listings to fetch and summarize.")
    return parser.parse_args()


def resolve_character_state(args: argparse.Namespace) -> dict[str, Any]:
    if not args.account:
        raise SystemExit("Missing --account or DEFAULT_ACCOUNT")
    if not args.character:
        raise SystemExit("Missing --character or DEFAULT_CHARACTER")
    account = normalize_account_name(args.account, args.realm)
    characters = get_characters(account, args.realm, args.poesessid)
    selected = choose_character(characters, args.character)
    if selected is None:
        raise PoeApiError(f"Character '{args.character}' was not found on this account/realm.")
    return selected


def build_query(args: argparse.Namespace, character: dict[str, Any]) -> dict[str, Any]:
    level = int(character.get("level", 100))
    query: dict[str, Any] = {
        "query": {
            "status": {"option": args.mode},
            "filters": {
                "type_filters": {
                    "filters": {
                        "category": {"option": args.category},
                        "rarity": {"option": args.rarity},
                    }
                },
                "req_filters": {
                    "filters": {
                        "lvl": {"max": level},
                    }
                },
            },
            "stats": [
                {
                    "type": "weight",
                    "filters": args.weight,
                }
            ],
        },
        "sort": {"price": "asc"},
    }
    if args.weight_min is not None:
        query["query"]["stats"][0]["value"] = {"min": args.weight_min}
    if args.price_max is not None:
        query["query"]["filters"]["trade_filters"] = {"filters": {"price": {"max": args.price_max}}}
    return query


def summarize_result(entry: dict[str, Any]) -> dict[str, Any]:
    listing = entry.get("listing", {})
    item = entry.get("item", {})
    account = listing.get("account", {})
    price = listing.get("price") or {}
    return {
        "id": entry.get("id"),
        "name": " ".join(part for part in [str(item.get("name", "")).strip(), str(item.get("typeLine", "")).strip()] if part).strip() or "Unknown Item",
        "price": {
            "amount": price.get("amount"),
            "currency": price.get("currency"),
        },
        "seller": account.get("name"),
    }


def main() -> int:
    args = parse_args()
    if not args.weight:
        raise SystemExit("Provide at least one --weight stat_id=weight pair.")

    try:
        character = resolve_character_state(args)
        league = args.league or str(character.get("league", "")).strip() or env_first("DEFAULT_LEAGUE", default="Standard")
        query = build_query(args, character)
        search = post_trade_search(league, query, poesessid=args.poesessid)
        query_id = str(search.payload.get("id", "")).strip()
        result_ids = [str(item_id) for item_id in search.payload.get("result", []) if str(item_id).strip()]
        if not query_id:
            raise SystemExit("Trade search returned no query id.")

        print(f"Character: {character.get('name')} | level {character.get('level')} | {character.get('class')} | {league}")
        print(f"Mode: {args.mode}")
        print(f"Category: {args.category}")
        print(f"Trade Link: https://www.pathofexile.com/trade/search/{league}/{query_id}")
        print(f"Results: {len(result_ids)}")

        if result_ids and args.fetch_limit > 0:
            top_ids = result_ids[: args.fetch_limit]
            fetched = fetch_trade_results(top_ids, query_id, poesessid=args.poesessid)
            summaries = [summarize_result(entry) for entry in fetched.payload.get("result", [])]
            print(json.dumps({"top_listings": summaries}, indent=2))

        print(f"Rate log: {RATE_LIMIT_LOG_PATH}")
        return 0
    except (PoeApiError, TradeApiError) as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
