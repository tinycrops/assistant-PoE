#!/usr/bin/env python3
"""Append-only local history for Discord webhook publishes."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

DEFAULT_LOG_PATH = "logs/discord_publish_history.jsonl"


def extract_webhook_id(webhook_url: str) -> str | None:
    match = re.search(r"/api/webhooks/(\d+)/", webhook_url)
    if not match:
        return None
    return match.group(1)


def append_publish_history(
    *,
    source: str,
    webhook_url: str,
    username: str,
    content: str,
    embeds: list[dict[str, Any]] | None = None,
    discord_message: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    log_path: str = DEFAULT_LOG_PATH,
) -> None:
    record: dict[str, Any] = {
        "published_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "webhook_id": extract_webhook_id(webhook_url),
        "username": username,
        "content": content,
        "embeds": embeds or [],
        "embed_count": len(embeds or []),
        "discord_message_id": (discord_message or {}).get("id"),
        "discord_timestamp": (discord_message or {}).get("timestamp"),
        "metadata": metadata or {},
    }

    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True) + "\n")
