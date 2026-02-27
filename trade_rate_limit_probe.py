#!/usr/bin/env python3
"""Probe the current trade API rate-limit headers and write them to the local log."""

from __future__ import annotations

import argparse
import json
import os

from trade_api import RATE_LIMIT_LOG_PATH, post_trade_search


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Path of Exile trade API rate-limit headers.")
    parser.add_argument("--league", default=os.environ.get("DEFAULT_LEAGUE", "Standard"))
    parser.add_argument("--mode", default=os.environ.get("DEFAULT_TRADE_MODE", "securable"), choices=["securable", "online"])
    parser.add_argument("--category", default="accessory.ring", help="Trade category filter for the probe search.")
    parser.add_argument("--poesessid", default=os.environ.get("POESESSID"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = {
        "query": {
            "status": {"option": args.mode},
            "filters": {
                "type_filters": {
                    "filters": {
                        "category": {"option": args.category},
                    }
                }
            },
        },
        "sort": {"price": "asc"},
    }
    response = post_trade_search(args.league, payload, poesessid=args.poesessid)
    print(f"Status: {response.status}")
    print(json.dumps({k: v for k, v in response.headers.items() if k.lower().startswith('x-rate-limit') or k.lower() == 'retry-after'}, indent=2, sort_keys=True))
    print(f"Logged to: {RATE_LIMIT_LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
