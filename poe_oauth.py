#!/usr/bin/env python3
"""PoE OAuth helpers (PKCE/public client + bearer API helpers)."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

OAUTH_HOST = "https://www.pathofexile.com"
API_HOST = "https://api.pathofexile.com"


class PoeOAuthError(RuntimeError):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def generate_pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def default_user_agent(client_id: str, contact: str, version: str = "0.1.0") -> str:
    return f"OAuth {client_id}/{version} (contact: {contact}) assistant-poe"


def token_endpoint_post(form: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        f"{OAUTH_HOST}/oauth/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise PoeOAuthError(f"OAuth token exchange failed (HTTP {exc.code}): {raw[:400]}") from exc


def refresh_access_token(
    token_doc: dict[str, Any],
    client_id: str,
    client_secret: str | None = None,
) -> dict[str, Any]:
    refresh_token = str(token_doc.get("refresh_token", "")).strip()
    if not refresh_token:
        raise PoeOAuthError("Token document has no refresh_token.")

    form = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if client_secret:
        form["client_secret"] = client_secret

    fresh = token_endpoint_post(form)
    now = datetime.now(timezone.utc)
    fresh["obtained_at_utc"] = now.isoformat()
    expires_in = fresh.get("expires_in")
    if isinstance(expires_in, (int, float)):
        fresh["expires_at_utc"] = (now + timedelta(seconds=int(expires_in))).isoformat()
    return fresh


def token_expired_or_soon(token_doc: dict[str, Any], safety_seconds: int = 60) -> bool:
    expires_at = token_doc.get("expires_at_utc")
    if not isinstance(expires_at, str) or not expires_at:
        return False
    try:
        when = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= (when - timedelta(seconds=safety_seconds))


def _api_get_json(path: str, access_token: str, user_agent: str) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{API_HOST}{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": user_agent,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise PoeOAuthError(f"API request failed (HTTP {exc.code}) for {path}: {raw[:400]}") from exc


def _realm_prefix(realm: str) -> str:
    return "" if realm == "pc" else f"/{realm}"


def fetch_stashes_with_items(
    access_token: str,
    realm: str,
    league: str,
    user_agent: str,
) -> dict[str, Any]:
    realm_prefix = _realm_prefix(realm)
    league_path = urllib.parse.quote(league, safe="")
    listed = _api_get_json(f"/stash{realm_prefix}/{league_path}", access_token, user_agent)
    stashes = listed.get("stashes", [])
    if not isinstance(stashes, list):
        stashes = []

    # Fetch each top-level tab by its public ID to include items.
    full_tabs: list[dict[str, Any]] = []
    for tab in stashes:
        if not isinstance(tab, dict):
            continue
        tab_id = str(tab.get("id", "")).strip()
        if not tab_id:
            continue
        tab_id_path = urllib.parse.quote(tab_id, safe="")
        full = _api_get_json(f"/stash{realm_prefix}/{league_path}/{tab_id_path}", access_token, user_agent)
        full_tabs.append(full.get("stash", full))

    return {
        "stashes_list": stashes,
        "stashes_full": full_tabs,
    }
