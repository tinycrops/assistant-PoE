#!/usr/bin/env python3
"""Pull Path of Exile character data using the same flow Path of Building uses.

Usage:
  python3 poe_character_sync.py --onboarding
  python3 poe_character_sync.py --account "YourAccount#1234"
  python3 poe_character_sync.py --account "YourAccount#1234" --poesessid "<32-hex>"
  python3 poe_character_sync.py --account "YourAccount#1234" --poesessid "<32-hex>" --character "CharName" --include-passive --include-items
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

REALM_MAP = {
    "pc": "pc",
    "xbox": "xbox",
    "sony": "sony",
}

HOST = "https://www.pathofexile.com"


@dataclass
class HttpResult:
    status: int
    body: str
    headers: dict[str, str]


class PoeApiError(RuntimeError):
    pass


def prompt_non_empty(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("Value cannot be empty.")


def prompt_choice(prompt: str, choices: list[str], default: str | None = None) -> str:
    normalized = {choice.lower(): choice for choice in choices}
    while True:
        suffix = f" [{'/'.join(choices)}]"
        if default:
            suffix = f"{suffix} (default: {default})"
        value = input(f"{prompt}{suffix}: ").strip().lower()
        if not value and default:
            return default
        if value in normalized:
            return normalized[value]
        print(f"Please choose one of: {', '.join(choices)}")


def prompt_yes_no(prompt: str, default_yes: bool = True) -> bool:
    choices = "Y/n" if default_yes else "y/N"
    while True:
        value = input(f"{prompt} [{choices}]: ").strip().lower()
        if not value:
            return default_yes
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please enter y or n.")


def prompt_poesessid() -> str:
    while True:
        value = input("Enter POESESSID (32 hex chars): ").strip()
        if re.fullmatch(r"[0-9a-fA-F]{32}", value):
            return value
        print("Invalid POESESSID format. Expected 32 hex characters.")


def normalize_account_name(raw: str, realm: str) -> str:
    value = raw.strip()
    if realm == "pc":
        value = re.sub(r"\s+", "", value)
    else:
        value = value.replace(" ", "+")
    value = re.sub(r"(.*)[#\-]", r"\1#", value)
    return value


def http_get(url: str, poesessid: str | None = None) -> HttpResult:
    headers = {"User-Agent": "PoE-Assistant-Prototyper/1.0"}
    if poesessid:
        headers["Cookie"] = f"POESESSID={poesessid}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return HttpResult(status=resp.status, body=body, headers=dict(resp.headers.items()))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return HttpResult(status=e.code, body=body, headers=dict(e.headers.items()) if e.headers else {})


def get_characters(account_name: str, realm: str, poesessid: str | None) -> list[dict[str, Any]]:
    acct = urllib.parse.quote(account_name, safe="")
    url = f"{HOST}/character-window/get-characters?accountName={acct}&realm={realm}"
    resp = http_get(url, poesessid=poesessid)

    if resp.status == 401:
        raise PoeApiError("Sign-in required (401). Provide a valid POESESSID.")
    if resp.status == 403:
        raise PoeApiError("Account profile/characters are private (403). Provide POESESSID or change privacy settings.")
    if resp.status == 404:
        raise PoeApiError("Account not found (404). Check account name + discriminator (e.g. Name#1234).")
    if resp.status != 200:
        raise PoeApiError(f"Failed to fetch characters (HTTP {resp.status}).")

    try:
        data = json.loads(resp.body)
    except json.JSONDecodeError as e:
        raise PoeApiError(f"Character list response was not valid JSON: {e}") from e

    if not isinstance(data, list):
        raise PoeApiError("Character list response was not a JSON array.")

    return data


def try_get_characters(account_name: str, realm: str, poesessid: str | None) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        return get_characters(account_name, realm, poesessid), None
    except PoeApiError as e:
        return None, str(e)


def get_canonical_account_name(account_name: str, poesessid: str | None) -> str:
    acct = urllib.parse.quote(account_name, safe="")
    url = f"{HOST}/account/view-profile/{acct}"
    resp = http_get(url, poesessid=poesessid)

    if resp.status != 200:
        raise PoeApiError(f"Failed to fetch profile page for account canonicalization (HTTP {resp.status}).")

    match = re.search(r"/view-profile/([^/]+)/characters", resp.body)
    if not match:
        raise PoeApiError("Could not extract canonical account name from profile page.")

    return urllib.parse.unquote(match.group(1))


def get_passive_skills(account_name: str, character_name: str, realm: str, poesessid: str | None) -> dict[str, Any]:
    acct = urllib.parse.quote(account_name, safe="")
    char = urllib.parse.quote(character_name, safe="")
    url = f"{HOST}/character-window/get-passive-skills?accountName={acct}&character={char}&realm={realm}"
    resp = http_get(url, poesessid=poesessid)

    if resp.status != 200:
        raise PoeApiError(f"Failed to fetch passive skills (HTTP {resp.status}).")

    try:
        return json.loads(resp.body)
    except json.JSONDecodeError as e:
        raise PoeApiError(f"Passive skills response was not valid JSON: {e}") from e


def get_items(account_name: str, character_name: str, realm: str, poesessid: str | None) -> dict[str, Any]:
    acct = urllib.parse.quote(account_name, safe="")
    char = urllib.parse.quote(character_name, safe="")
    url = f"{HOST}/character-window/get-items?accountName={acct}&character={char}&realm={realm}"
    resp = http_get(url, poesessid=poesessid)

    if resp.status != 200:
        raise PoeApiError(f"Failed to fetch items (HTTP {resp.status}).")

    try:
        return json.loads(resp.body)
    except json.JSONDecodeError as e:
        raise PoeApiError(f"Items response was not valid JSON: {e}") from e


def get_stash_items(
    account_name: str,
    realm: str,
    league: str,
    poesessid: str | None,
    tab_index: int | None = None,
    tabs: int = 0,
) -> dict[str, Any]:
    acct = urllib.parse.quote(account_name, safe="")
    query: dict[str, Any] = {
        "accountName": acct,
        "realm": realm,
        "league": league,
        "tabs": tabs,
    }
    if tab_index is not None:
        query["tabIndex"] = int(tab_index)
    # accountName is already quoted and must stay as-is
    encoded = "&".join(f"{k}={v}" for k, v in query.items())
    url = f"{HOST}/character-window/get-stash-items?{encoded}"
    resp = http_get(url, poesessid=poesessid)

    if resp.status == 401:
        raise PoeApiError("Stash access requires authentication (401). Provide a valid POESESSID.")
    if resp.status == 403:
        raise PoeApiError("Stash access forbidden (403). Stash tabs are not public or POESESSID is missing/invalid.")
    if resp.status != 200:
        raise PoeApiError(f"Failed to fetch stash items (HTTP {resp.status}).")

    try:
        return json.loads(resp.body)
    except json.JSONDecodeError as e:
        raise PoeApiError(f"Stash response was not valid JSON: {e}") from e


def choose_character(characters: list[dict[str, Any]], requested_name: str | None) -> dict[str, Any] | None:
    if not characters:
        return None
    if not requested_name:
        return characters[0]

    needle = requested_name.lower()
    for char in characters:
        if str(char.get("name", "")).lower() == needle:
            return char
    return None


def select_character_interactive(characters: list[dict[str, Any]]) -> dict[str, Any]:
    print("\nCharacters found:")
    for i, char in enumerate(characters, start=1):
        name = str(char.get("name", "?"))
        level = str(char.get("level", "?"))
        league = str(char.get("league", "?"))
        char_class = str(char.get("class", "?"))
        print(f"  {i}. {name} | lvl {level} | {char_class} | {league}")

    while True:
        raw = input("Select character number: ").strip()
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(characters):
                return characters[idx - 1]
        print("Please enter a valid character number.")


def run_onboarding() -> int:
    print("Path of Exile Account Onboarding")
    print("This flow mirrors Path of Building account verification and character import.")
    print("")

    realm = prompt_choice("Choose realm", sorted(REALM_MAP.keys()), default="pc").lower()
    raw_account = prompt_non_empty('Enter account name with discriminator (e.g. Name#1234): ')
    account = normalize_account_name(raw_account, REALM_MAP[realm])
    poesessid: str | None = None

    characters, err = try_get_characters(account, REALM_MAP[realm], poesessid)
    if err:
        print(f"\nInitial character lookup failed: {err}")
        if "Sign-in required" in err or "private" in err:
            print("Your account likely has hidden characters or needs authentication.")
            print('Option 1: Set "Hide Characters" to off in https://www.pathofexile.com/my-account/privacy')
            print("Option 2: continue with POESESSID cookie authentication")
            if prompt_yes_no("Use POESESSID now?", default_yes=True):
                poesessid = prompt_poesessid()
                characters, err = try_get_characters(account, REALM_MAP[realm], poesessid)
        if err:
            print(f"ERROR: {err}", file=sys.stderr)
            return 2

    if not characters:
        print("ERROR: Account has no characters to import.", file=sys.stderr)
        return 2

    try:
        canonical_account = get_canonical_account_name(account, poesessid)
    except PoeApiError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    selected = select_character_interactive(characters)
    selected_name = str(selected.get("name", ""))

    include_passive = prompt_yes_no("Fetch passive tree data?", default_yes=True)
    include_items = prompt_yes_no("Fetch items and skills data?", default_yes=True)

    result: dict[str, Any] = {
        "input_account": raw_account,
        "normalized_account": account,
        "canonical_account": canonical_account,
        "realm": REALM_MAP[realm],
        "character_count": len(characters),
        "selected_character": selected_name,
    }

    try:
        if include_passive:
            result["passive_skills"] = get_passive_skills(canonical_account, selected_name, REALM_MAP[realm], poesessid)
        if include_items:
            result["items"] = get_items(canonical_account, selected_name, REALM_MAP[realm], poesessid)
    except PoeApiError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print("\nOnboarding complete. Result:")
    print(json.dumps(result, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PoE account verification + character import prototype")
    parser.add_argument("--onboarding", action="store_true", help="Run interactive onboarding flow")
    parser.add_argument("--account", required=False, help='PoE account name with discriminator, e.g. "Name#1234"')
    parser.add_argument("--realm", default="pc", choices=sorted(REALM_MAP.keys()), help="Realm: pc, xbox, sony")
    parser.add_argument("--poesessid", default=None, help="POESESSID cookie (32 hex chars)")
    parser.add_argument("--character", default=None, help="Character name for passive/items pulls")
    parser.add_argument("--include-passive", action="store_true", help="Fetch passive tree payload for selected character")
    parser.add_argument("--include-items", action="store_true", help="Fetch items payload for selected character")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.onboarding:
        return run_onboarding()
    if not args.account:
        print("ERROR: --account is required unless --onboarding is used.", file=sys.stderr)
        return 2

    realm = REALM_MAP[args.realm]
    account = normalize_account_name(args.account, realm)

    try:
        characters = get_characters(account, realm, args.poesessid)
        canonical_account = get_canonical_account_name(account, args.poesessid)

        result: dict[str, Any] = {
            "input_account": args.account,
            "normalized_account": account,
            "canonical_account": canonical_account,
            "realm": realm,
            "character_count": len(characters),
            "characters": characters,
        }

        selected = choose_character(characters, args.character)
        if args.character and selected is None:
            raise PoeApiError(f"Character '{args.character}' was not found on this account/realm.")

        if selected and (args.include_passive or args.include_items):
            selected_name = str(selected.get("name", ""))
            result["selected_character"] = selected_name

            if args.include_passive:
                result["passive_skills"] = get_passive_skills(canonical_account, selected_name, realm, args.poesessid)

            if args.include_items:
                result["items"] = get_items(canonical_account, selected_name, realm, args.poesessid)

        print(json.dumps(result, indent=2))
        return 0
    except PoeApiError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"UNEXPECTED ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
