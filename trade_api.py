#!/usr/bin/env python3
"""Shared Path of Exile trade API helpers with rate-limit logging."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HOST = "https://www.pathofexile.com"
DEFAULT_MIN_INTERVAL_SECONDS = float(os.environ.get("TRADE_API_MIN_INTERVAL_SECONDS", "15"))
RATE_LIMIT_LOG_PATH = Path(os.environ.get("TRADE_API_RATE_LIMIT_LOG", "logs/trade_api/rate_limit_history.jsonl"))
REQUEST_STATE_PATH = Path(os.environ.get("TRADE_API_STATE_PATH", "logs/trade_api/last_request_at.txt"))


class TradeApiError(RuntimeError):
    """Raised when a trade API request fails."""


@dataclass
class TradeApiResponse:
    status: int
    headers: dict[str, str]
    payload: dict[str, Any]


def _now() -> float:
    return time.time()


def _load_last_request_at(path: Path) -> float | None:
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _save_last_request_at(path: Path, ts: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{ts:.6f}\n", encoding="utf-8")


def enforce_min_interval(min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS) -> float:
    last_request_at = _load_last_request_at(REQUEST_STATE_PATH)
    now = _now()
    if last_request_at is None:
        return 0.0
    wait_for = max(0.0, min_interval_seconds - (now - last_request_at))
    if wait_for > 0:
        time.sleep(wait_for)
    return wait_for


def _response_headers_map(headers: Any) -> dict[str, str]:
    return {key: value for key, value in headers.items()}


def _extract_rate_limit_headers(headers: dict[str, str]) -> dict[str, str]:
    extracted: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered.startswith("x-rate-limit") or lowered == "retry-after":
            extracted[key] = value
    return extracted


def log_rate_limit_headers(
    *,
    method: str,
    url: str,
    status: int,
    headers: dict[str, str],
    waited_seconds: float,
    error_body: str | None = None,
) -> None:
    RATE_LIMIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": method,
        "url": url,
        "status": status,
        "waited_seconds": round(waited_seconds, 3),
        "rate_limit_headers": _extract_rate_limit_headers(headers),
    }
    if error_body:
        event["error_body"] = error_body[:500]
    with RATE_LIMIT_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def build_headers(*, poesessid: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (PoE Assistant)",
    }
    if poesessid:
        headers["Cookie"] = f"POESESSID={poesessid}"
    return headers


def request_json(
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    poesessid: str | None = None,
    timeout: int = 30,
    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
) -> TradeApiResponse:
    waited_seconds = enforce_min_interval(min_interval_seconds=min_interval_seconds)
    url = f"{HOST}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, headers=build_headers(poesessid=poesessid), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            headers = _response_headers_map(resp.headers)
            data = json.load(resp)
            _save_last_request_at(REQUEST_STATE_PATH, _now())
            log_rate_limit_headers(method=method, url=url, status=resp.status, headers=headers, waited_seconds=waited_seconds)
            return TradeApiResponse(status=resp.status, headers=headers, payload=data)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        headers = _response_headers_map(exc.headers)
        _save_last_request_at(REQUEST_STATE_PATH, _now())
        log_rate_limit_headers(
            method=method,
            url=url,
            status=exc.code,
            headers=headers,
            waited_seconds=waited_seconds,
            error_body=error_body,
        )
        raise TradeApiError(f"Trade API request failed ({exc.code}) for {path}: {error_body[:200]}") from exc


def get_trade_stats(*, poesessid: str | None = None) -> TradeApiResponse:
    return request_json("/api/trade/data/stats", poesessid=poesessid)


def get_trade_filters(*, poesessid: str | None = None) -> TradeApiResponse:
    return request_json("/api/trade/data/filters", poesessid=poesessid)


def post_trade_search(league: str, query: dict[str, Any], *, poesessid: str | None = None) -> TradeApiResponse:
    return request_json(f"/api/trade/search/{league}", method="POST", payload=query, poesessid=poesessid)


def fetch_trade_results(
    ids: list[str],
    query_id: str,
    *,
    poesessid: str | None = None,
) -> TradeApiResponse:
    encoded_ids = ",".join(urllib.parse.quote(item_id, safe="") for item_id in ids)
    encoded_query_id = urllib.parse.quote(query_id, safe="")
    return request_json(f"/api/trade/fetch/{encoded_ids}?query={encoded_query_id}", poesessid=poesessid)
