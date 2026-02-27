#!/usr/bin/env python3
"""Send structured persona posts to a Discord webhook.

Example:
  DISCORD_WEBHOOK_URL=... python3 discord_persona_sender.py \
    --type LOG --context "T16 Harvest/2026-02-17" \
    --body "Died to stacked DoT after flask drop."
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from discord_publish_log import DEFAULT_LOG_PATH, append_publish_history

MAX_CONTENT_LEN = 1900


def build_persona_message(post_type: str, context: str, body: str) -> str:
    normalized_type = post_type.strip().upper()
    if normalized_type not in {"LOG", "LEARN", "NEXT"}:
        raise ValueError("post_type must be LOG, LEARN, or NEXT")

    context_clean = context.strip() or "general"
    body_clean = " ".join(body.strip().split())
    if not body_clean:
        raise ValueError("body cannot be empty")

    message = f"[{normalized_type}][{context_clean}] {body_clean}"
    if len(message) > MAX_CONTENT_LEN:
        message = message[: MAX_CONTENT_LEN - 3] + "..."
    return message


def with_wait_query(webhook_url: str) -> str:
    parsed = urllib.parse.urlsplit(webhook_url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query["wait"] = "true"
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))


def send_discord_message(
    webhook_url: str,
    content: str,
    username: str,
    *,
    source: str = "discord_persona_sender",
    metadata: dict[str, str] | None = None,
    log_path: str = DEFAULT_LOG_PATH,
) -> None:
    payload = {
        "content": content,
        "username": username,
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
                raise RuntimeError(f"Discord webhook failed (HTTP {resp.status})")
            raw = resp.read().decode("utf-8", errors="replace")
            discord_message = json.loads(raw) if raw else None
            append_publish_history(
                source=source,
                webhook_url=webhook_url,
                username=username,
                content=content,
                embeds=[],
                discord_message=discord_message if isinstance(discord_message, dict) else None,
                metadata=metadata,
                log_path=log_path,
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"Discord webhook failed (HTTP {exc.code}): {detail}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send structured LOG/LEARN/NEXT persona posts to Discord")
    parser.add_argument("--webhook-url", default=os.environ.get("DISCORD_WEBHOOK_URL"), help="Discord webhook URL")
    parser.add_argument("--username", default="OpenClawZeroZeroZero", help="Display name for webhook posts")
    parser.add_argument("--type", required=True, choices=["LOG", "LEARN", "NEXT"], help="Persona post type")
    parser.add_argument("--context", default="general", help="Short context tag")
    parser.add_argument("--body", required=True, help="Post body")
    parser.add_argument("--log-path", default=DEFAULT_LOG_PATH, help="Append-only publish history JSONL path")
    parser.add_argument("--dry-run", action="store_true", help="Print payload instead of posting")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.webhook_url:
        print("ERROR: Provide --webhook-url or set DISCORD_WEBHOOK_URL.", file=sys.stderr)
        return 2

    try:
        content = build_persona_message(args.type, args.context, args.body)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(content)
        return 0

    try:
        send_discord_message(
            args.webhook_url,
            content,
            args.username,
            metadata={"post_type": args.type, "context": args.context},
            log_path=args.log_path,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Posted to Discord. Logged at {args.log_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
