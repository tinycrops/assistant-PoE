#!/usr/bin/env python3
"""Run local PoE OAuth PKCE flow and save access/refresh token JSON."""

from __future__ import annotations

import argparse
import json
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from poe_oauth import OAUTH_HOST, generate_pkce_pair, token_endpoint_post


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PoE OAuth login (PKCE, local callback).")
    parser.add_argument("--client-id", required=True, help="Your PoE OAuth client_id")
    parser.add_argument("--client-secret", default=None, help="Optional PoE OAuth client_secret")
    parser.add_argument(
        "--scope",
        default="account:profile account:characters account:league_accounts account:stashes",
        help="Space-separated scopes",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Local callback host")
    parser.add_argument("--port", type=int, default=5057, help="Local callback port")
    parser.add_argument(
        "--redirect-uri",
        default=None,
        help="Override redirect URI. Defaults to http://127.0.0.1:<port>/oauth/callback",
    )
    parser.add_argument(
        "--token-out",
        default="logs/poe_oauth_token.json",
        help="Token output JSON path",
    )
    parser.add_argument("--no-open-browser", action="store_true", help="Print URL without opening browser")
    parser.add_argument("--timeout-seconds", type=int, default=300, help="Callback wait timeout")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    redirect_uri = args.redirect_uri or f"http://{args.host}:{args.port}/oauth/callback"
    state = secrets_token()
    verifier, challenge = generate_pkce_pair()

    result: dict[str, str] = {}
    event = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/oauth/callback":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return

            params = urllib.parse.parse_qs(parsed.query)
            code = (params.get("code") or [""])[0]
            got_state = (params.get("state") or [""])[0]
            error = (params.get("error") or [""])[0]
            if error:
                result["error"] = error
            elif not code:
                result["error"] = "missing_code"
            elif got_state != state:
                result["error"] = "state_mismatch"
            else:
                result["code"] = code

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if "code" in result:
                self.wfile.write(b"<h1>OAuth complete</h1><p>You can close this tab.</p>")
            else:
                self.wfile.write(b"<h1>OAuth failed</h1><p>Check terminal output.</p>")
            event.set()

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
            return

    server = HTTPServer((args.host, args.port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    auth_query = urllib.parse.urlencode(
        {
            "client_id": args.client_id,
            "scope": args.scope,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    auth_url = f"{OAUTH_HOST}/oauth/authorize?{auth_query}"

    print("Open this URL to authorize:")
    print(auth_url)
    if not args.no_open_browser:
        webbrowser.open(auth_url)

    if not event.wait(args.timeout_seconds):
        server.shutdown()
        raise SystemExit(f"Timed out waiting for OAuth callback after {args.timeout_seconds}s.")

    server.shutdown()
    if "error" in result:
        raise SystemExit(f"OAuth callback error: {result['error']}")
    code = result.get("code")
    if not code:
        raise SystemExit("OAuth callback did not provide an authorization code.")

    form = {
        "client_id": args.client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    if args.client_secret:
        form["client_secret"] = args.client_secret

    token = token_endpoint_post(form)
    now = datetime.now(timezone.utc)
    token["obtained_at_utc"] = now.isoformat()
    expires_in = token.get("expires_in")
    if isinstance(expires_in, (int, float)):
        token["expires_at_utc"] = (now + timedelta(seconds=int(expires_in))).isoformat()
    token["client_id"] = args.client_id
    token["scope_requested"] = args.scope
    token["redirect_uri"] = redirect_uri

    out_path = Path(args.token_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(token, indent=2), encoding="utf-8")
    print(f"Saved token: {out_path}")
    return 0


def secrets_token() -> str:
    import secrets

    return secrets.token_urlsafe(24)


if __name__ == "__main__":
    raise SystemExit(main())
