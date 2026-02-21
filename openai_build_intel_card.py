#!/usr/bin/env python3
"""Generate and post a Build Intelligence Card using OpenAI GPT-5.2.

Repeatable workflow:
1) Read a deterministic snapshot JSON.
2) Build a versioned prompt from extracted features.
3) Call OpenAI Responses API with model gpt-5.2.
4) Validate/sanitize card output.
5) Post to Discord and append local publish history.
6) Persist run artifact for audit/replay.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from math import ceil
from datetime import datetime, timezone
from typing import Any

from discord_publish_log import DEFAULT_LOG_PATH
from observability import run_with_observability
from observability.context import build_observability_config
from post_build_intel_card import extract_build_signals, post_discord_embed

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.2"
PROMPT_VERSION = "build-intel-card.v1.2026-02-17"
DEFAULT_DAILY_TOKEN_BUDGET = 850_000
DEFAULT_TOKEN_LEDGER_PATH = "logs/openai_token_usage.jsonl"
DEFAULT_RESERVED_OUTPUT_TOKENS = 2000
DEFAULT_OBSERVABILITY_MODE = "jsonl+mlflow"
DEFAULT_OBSERVABILITY_LOG_PATH = "logs/dspy_observability.jsonl"


def clamp_text(value: str, max_len: int) -> str:
    value = value.strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 3].rstrip() + "..."


def read_snapshot(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def summarize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    items_payload = snapshot.get("items", {})
    char = items_payload.get("character", {})
    build = extract_build_signals(items_payload)

    gear_slots = ["Weapon", "Offhand", "Helm", "BodyArmour", "Gloves", "Boots", "Belt", "Amulet", "Ring", "Ring2"]
    gear = {slot: build["gear"].get(slot, "Unknown") for slot in gear_slots}

    skill_names = sorted(set(build.get("skill_names", [])))
    support_names = sorted(set(build.get("support_names", [])))

    pricing = snapshot.get("pricing_summary", {})
    top_holdings = pricing.get("top_holdings", [])

    summary = {
        "character": {
            "name": char.get("name", "Unknown"),
            "class": char.get("class", "Unknown"),
            "level": char.get("level", "?"),
            "league": char.get("league", snapshot.get("character", {}).get("league", "Standard")),
            "realm": char.get("realm", snapshot.get("account", {}).get("realm", "pc")),
        },
        "build_signals": {
            "core_attack": build.get("core_attack"),
            "secondary_attack": build.get("secondary_attack"),
            "utility": build.get("utility", []),
            "active_skills": skill_names,
            "support_skills": support_names,
        },
        "gear": gear,
        "pricing_summary": {
            "priced_items": pricing.get("priced_items", 0),
            "total_items": pricing.get("total_items", 0),
            "known_value_chaos": pricing.get("known_value_chaos", 0),
            "top_holdings": top_holdings[:5],
        },
    }
    return summary


def build_messages(summary: dict[str, Any]) -> tuple[str, str]:
    system = (
        "You are a Path of Exile build analyst. "
        "Generate a high-signal Discord Build Intelligence Card from structured character data. "
        "Be concise, concrete, and actionable. Avoid fluff."
    )

    schema_instructions = {
        "required_output": {
            "log_message": "string; must start with [LOG][build-intel]",
            "embed": {
                "title": "string",
                "description": "string",
                "color": "integer between 0 and 16777215",
                "fields": [
                    {"name": "Build Identity", "value": "string", "inline": False},
                    {"name": "What The Build Is Trying To Do", "value": "string", "inline": False},
                    {"name": "Current Gear Signal", "value": "string", "inline": False},
                    {"name": "Liquid Market Snapshot", "value": "string", "inline": False},
                    {"name": "Next 3 Moves", "value": "string with 3 numbered lines", "inline": False},
                ],
                "footer": {"text": "string"},
            },
            "analysis_version": PROMPT_VERSION,
        }
    }

    user = (
        f"Prompt version: {PROMPT_VERSION}\n"
        "Create a polished but factual build card. Make inferences only from provided data.\n"
        "If data is uncertain, say so briefly and still provide concrete next moves.\n"
        "Return JSON only, no markdown.\n\n"
        f"Output contract:\n{json.dumps(schema_instructions, indent=2)}\n\n"
        f"Character data:\n{json.dumps(summary, indent=2)}"
    )

    return system, user


def estimate_tokens(text: str) -> int:
    # Lightweight estimate for preflight budget checks without tokenizer deps.
    return max(1, ceil(len(text) / 4))


def append_jsonl(path: str, payload: dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def utc_day(iso_ts: str) -> str:
    return iso_ts[:10]


def used_tokens_today(ledger_path: str, day: str, model: str) -> int:
    total = 0
    if not os.path.exists(ledger_path):
        return total
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") != "openai_call_completed":
                continue
            if row.get("status") != "success":
                continue
            if row.get("model") != model:
                continue
            ts = str(row.get("timestamp_utc", ""))
            if utc_day(ts) != day:
                continue
            total += int(row.get("actual_total_tokens") or 0)
    return total


def extract_total_tokens(response: dict[str, Any]) -> int | None:
    usage = response.get("usage")
    if isinstance(usage, dict):
        total = usage.get("total_tokens")
        if isinstance(total, int):
            return total
        if isinstance(total, str) and total.isdigit():
            return int(total)
    return None


def call_openai_responses(api_key: str, model: str, system_msg: str, user_msg: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_msg}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_msg}],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "build_intel_card",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["log_message", "embed", "analysis_version"],
                    "properties": {
                        "log_message": {"type": "string"},
                        "analysis_version": {"type": "string"},
                        "embed": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["title", "description", "color", "fields", "footer"],
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "color": {"type": "integer", "minimum": 0, "maximum": 16777215},
                                "fields": {
                                    "type": "array",
                                    "minItems": 5,
                                    "maxItems": 5,
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["name", "value", "inline"],
                                        "properties": {
                                            "name": {"type": "string"},
                                            "value": {"type": "string"},
                                            "inline": {"type": "boolean"},
                                        },
                                    },
                                },
                                "footer": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["text"],
                                    "properties": {"text": {"type": "string"}},
                                },
                            },
                        },
                    },
                },
            }
        },
    }

    req = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "PoE-Assistant/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"OpenAI API request failed (HTTP {exc.code}): {detail}") from exc


def extract_model_json(response: dict[str, Any]) -> dict[str, Any]:
    text_out = response.get("output_text")
    if isinstance(text_out, str) and text_out.strip():
        return json.loads(text_out)

    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                return json.loads(content["text"])

    raise RuntimeError("OpenAI response did not include parseable JSON output.")


def sanitize_card(card: dict[str, Any]) -> dict[str, Any]:
    if not str(card.get("log_message", "")).startswith("[LOG][build-intel]"):
        card["log_message"] = "[LOG][build-intel] " + str(card.get("log_message", "Generated build intelligence card.")).strip()

    embed = card.get("embed", {})
    embed["title"] = clamp_text(str(embed.get("title", "Build Intelligence Card")), 256)
    embed["description"] = clamp_text(str(embed.get("description", "")), 4096)
    embed["color"] = int(embed.get("color", 0xE67E22))

    fields = []
    for field in embed.get("fields", [])[:5]:
        fields.append(
            {
                "name": clamp_text(str(field.get("name", "Field")), 256),
                "value": clamp_text(str(field.get("value", "")), 1024),
                "inline": False,
            }
        )

    required_field_names = [
        "Build Identity",
        "What The Build Is Trying To Do",
        "Current Gear Signal",
        "Liquid Market Snapshot",
        "Next 3 Moves",
    ]
    while len(fields) < 5:
        missing_name = required_field_names[len(fields)]
        fields.append({"name": missing_name, "value": "Insufficient data.", "inline": False})

    embed["fields"] = fields
    footer = embed.get("footer", {})
    embed["footer"] = {"text": clamp_text(str(footer.get("text", "OpenClawZeroZeroZero Memory+Market Product")), 2048)}

    card["embed"] = embed
    card["analysis_version"] = str(card.get("analysis_version", PROMPT_VERSION))
    card["log_message"] = clamp_text(str(card["log_message"]), 1900)
    return card


def write_run_artifact(path: str, artifact: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2)


def default_run_artifact_path() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"logs/build_intel_runs/{ts}.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and post Build Intelligence Card using OpenAI GPT-5.2")
    parser.add_argument("--snapshot", required=True, help="Input snapshot JSON from poe_market_pipeline.py --output")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model (default: gpt-5.2)")
    parser.add_argument("--openai-api-key", default=os.environ.get("OPENAI_API_KEY"), help="OpenAI API key")
    parser.add_argument("--webhook-url", default=os.environ.get("DISCORD_WEBHOOK_URL"), help="Discord webhook URL")
    parser.add_argument("--username", default="OpenClawZeroZeroZero", help="Discord webhook username")
    parser.add_argument("--log-path", default=DEFAULT_LOG_PATH, help="Publish history JSONL path")
    parser.add_argument("--run-artifact", default=None, help="Path to save full generation artifact JSON")
    parser.add_argument(
        "--daily-token-budget",
        type=int,
        default=int(os.environ.get("OPENAI_DAILY_TOKEN_BUDGET", str(DEFAULT_DAILY_TOKEN_BUDGET))),
        help=f"Hard daily token budget for this model (default: {DEFAULT_DAILY_TOKEN_BUDGET})",
    )
    parser.add_argument(
        "--token-ledger-path",
        default=os.environ.get("OPENAI_TOKEN_LEDGER_PATH", DEFAULT_TOKEN_LEDGER_PATH),
        help=f"JSONL path for token usage ledger (default: {DEFAULT_TOKEN_LEDGER_PATH})",
    )
    parser.add_argument(
        "--reserve-output-tokens",
        type=int,
        default=int(os.environ.get("OPENAI_RESERVE_OUTPUT_TOKENS", str(DEFAULT_RESERVED_OUTPUT_TOKENS))),
        help=f"Preflight reserved output tokens estimate (default: {DEFAULT_RESERVED_OUTPUT_TOKENS})",
    )
    parser.add_argument(
        "--observability",
        default=os.environ.get("DSPY_OBSERVABILITY", DEFAULT_OBSERVABILITY_MODE),
        choices=["off", "jsonl", "jsonl+mlflow"],
        help="Observability mode (default: jsonl+mlflow)",
    )
    parser.add_argument(
        "--observability-log-path",
        default=os.environ.get("DSPY_OBSERVABILITY_LOG_PATH", DEFAULT_OBSERVABILITY_LOG_PATH),
        help=f"JSONL path for DSPy observability events (default: {DEFAULT_OBSERVABILITY_LOG_PATH})",
    )
    parser.add_argument(
        "--dspy-strict",
        action="store_true",
        help="Fail if DSPy observability runtime errors instead of falling back to direct OpenAI call",
    )
    parser.add_argument("--dry-run", action="store_true", help="Generate card but do not post")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.openai_api_key:
        print("ERROR: Provide --openai-api-key or set OPENAI_API_KEY.", file=sys.stderr)
        return 2
    if not args.dry_run and not args.webhook_url:
        print("ERROR: Provide --webhook-url or set DISCORD_WEBHOOK_URL.", file=sys.stderr)
        return 2

    snapshot = read_snapshot(args.snapshot)
    summary = summarize_snapshot(snapshot)
    system_msg, user_msg = build_messages(summary)
    observability_config = build_observability_config(
        mode=args.observability,
        log_path=args.observability_log_path,
        dspy_strict=args.dspy_strict,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    day = utc_day(now_iso)
    estimated_input_tokens = estimate_tokens(system_msg) + estimate_tokens(user_msg)
    estimated_total_tokens = estimated_input_tokens + max(0, args.reserve_output_tokens)
    used_today = used_tokens_today(args.token_ledger_path, day, args.model)
    remaining_before = args.daily_token_budget - used_today
    run_id = uuid.uuid4().hex[:12]
    observability_meta: dict[str, Any] = {"enabled": False, "mode": "off", "event_count": 0}
    observability_errors: list[str] = []

    if estimated_total_tokens > remaining_before:
        append_jsonl(
            args.token_ledger_path,
            {
                "event": "openai_preflight_blocked",
                "timestamp_utc": now_iso,
                "run_id": run_id,
                "model": args.model,
                "snapshot_path": args.snapshot,
                "daily_budget_tokens": args.daily_token_budget,
                "used_today_tokens": used_today,
                "remaining_before_tokens": remaining_before,
                "estimated_input_tokens": estimated_input_tokens,
                "reserved_output_tokens": args.reserve_output_tokens,
                "estimated_total_tokens": estimated_total_tokens,
            },
        )
        print(
            "ERROR: Daily token budget would be exceeded "
            f"(model={args.model}, day={day}, used={used_today}, remaining={remaining_before}, "
            f"estimated_call={estimated_total_tokens}, budget={args.daily_token_budget}).",
            file=sys.stderr,
        )
        return 3
    try:
        if observability_config.enabled:
            try:
                observed = run_with_observability(
                    config=observability_config,
                    run_id=run_id,
                    model=args.model,
                    snapshot_path=args.snapshot,
                    prompt_version=PROMPT_VERSION,
                    api_key=args.openai_api_key,
                    system_msg=system_msg,
                    user_msg=user_msg,
                    runner=call_openai_responses,
                )
                raw_response = observed.raw_response
                observability_meta = observed.observability
            except Exception as exc:
                if observability_config.dspy_strict:
                    raise
                observability_errors.append(str(exc))
                observability_meta = {
                    "enabled": True,
                    "mode": observability_config.mode,
                    "log_path": observability_config.log_path,
                    "mlflow_enabled": False,
                    "fallback_used": True,
                    "event_count": 0,
                }
                raw_response = call_openai_responses(args.openai_api_key, args.model, system_msg, user_msg)
        else:
            raw_response = call_openai_responses(args.openai_api_key, args.model, system_msg, user_msg)
    except Exception as exc:
        append_jsonl(
            args.token_ledger_path,
            {
                "event": "openai_call_completed",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "run_id": run_id,
                "model": args.model,
                "snapshot_path": args.snapshot,
                "status": "error",
                "error": str(exc),
                "daily_budget_tokens": args.daily_token_budget,
                "used_today_tokens": used_today,
                "estimated_input_tokens": estimated_input_tokens,
                "reserved_output_tokens": args.reserve_output_tokens,
                "estimated_total_tokens": estimated_total_tokens,
            },
        )
        raise

    actual_total_tokens = extract_total_tokens(raw_response)
    if actual_total_tokens is None:
        actual_total_tokens = estimated_total_tokens
    append_jsonl(
        args.token_ledger_path,
        {
            "event": "openai_call_completed",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "model": args.model,
            "snapshot_path": args.snapshot,
            "status": "success",
            "daily_budget_tokens": args.daily_token_budget,
            "used_today_tokens_before_call": used_today,
            "estimated_input_tokens": estimated_input_tokens,
            "reserved_output_tokens": args.reserve_output_tokens,
            "estimated_total_tokens": estimated_total_tokens,
            "actual_total_tokens": actual_total_tokens,
            "response_id": raw_response.get("id"),
        },
    )
    model_card = extract_model_json(raw_response)
    card = sanitize_card(model_card)

    run_artifact_path = args.run_artifact or default_run_artifact_path()
    artifact = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "prompt_version": PROMPT_VERSION,
        "model": args.model,
        "snapshot_path": args.snapshot,
        "summary": summary,
        "token_budget": {
            "day_utc": day,
            "daily_budget_tokens": args.daily_token_budget,
            "used_today_tokens_before_call": used_today,
            "remaining_before_call_tokens": remaining_before,
            "estimated_total_tokens": estimated_total_tokens,
            "actual_total_tokens": actual_total_tokens,
        },
        "system_prompt": system_msg,
        "user_prompt": user_msg,
        "raw_openai_response": raw_response,
        "model_card": model_card,
        "sanitized_card": card,
        "observability": observability_meta,
        "observability_errors": observability_errors,
        "posted": False,
    }

    if args.dry_run:
        write_run_artifact(run_artifact_path, artifact)
        print(json.dumps(card, indent=2))
        print(f"Run artifact saved: {run_artifact_path}")
        return 0

    post_discord_embed(args.webhook_url, args.username, card["log_message"], [card["embed"]], log_path=args.log_path)
    artifact["posted"] = True
    artifact["log_path"] = args.log_path
    write_run_artifact(run_artifact_path, artifact)

    print(f"Posted AI Build Intelligence Card to Discord. Artifact: {run_artifact_path}. Publish log: {args.log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
