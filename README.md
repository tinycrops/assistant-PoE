# PoE Character Tracking + Discord Memory Pipeline

This repo tracks a Path of Exile character over time, computes build stats through headless Path of Building (PoB), and publishes structured Discord updates.

## User-Facing Capabilities

- Pull live character data from PoE account endpoints (`character`, `items`, optional `passive_skills`)
- Enrich holdings with live market pricing (currency, uniques, div cards)
- Generate structured memory posts (`LOG`, `LEARN`, `NEXT`) for Discord
- Build and post visual build intelligence cards (rule-based or OpenAI-driven)
- Snapshot and diff PoB stat panels over time:
  - `offence`
  - `defence`
  - `misc`
  - `charges`
- Track inventory state per snapshot:
  - equipped items by slot
  - flasks
  - backpack contents
  - socketed gems
- Optional storage/stash fetch (auth-dependent)

## Quick Start

1. Install deps:
```bash
pip3 install -r requirements.txt
```
2. Create local secrets file:
```bash
cp .env.example .env
```
3. Retrieve your `POESESSID` cookie (tiny tutorial):
   - https://poedb.tw/us/POESESSID
   - Add it to `.env` as:
```bash
POESESSID=your_cookie_value_here
```
4. Load env and defaults:
```bash
set -a; source .env; source defaults.env; set +a
```
5. Run a tracking snapshot:
```bash
python3 poe_stat_watch.py \
  --account "$POE_DEFAULT_ACCOUNT" \
  --realm "$POE_DEFAULT_REALM" \
  --character "$POE_DEFAULT_CHARACTER"
```

## Primary Commands

Character stat + inventory tracking:
```bash
python3 poe_stat_watch.py \
  --account "$POE_DEFAULT_ACCOUNT" \
  --realm "$POE_DEFAULT_REALM" \
  --character "$POE_DEFAULT_CHARACTER" \
  --include-storage
```

Market sync + persona generation:
```bash
python3 poe_market_pipeline.py \
  --account "$POE_DEFAULT_ACCOUNT" \
  --realm "$POE_DEFAULT_REALM" \
  --character "$POE_DEFAULT_CHARACTER" \
  --output openclaw_market_snapshot.json
```

Manual persona post:
```bash
python3 discord_persona_sender.py \
  --type LOG \
  --context "session/2026-02-21" \
  --body "Progress checkpoint."
```

## Auth Modes

- `POESESSID` cookie auth:
  - Supports private account endpoints and legacy stash calls where permitted.
- OAuth scaffold:
  - `poe_oauth_login.py` provides local PKCE flow.
  - `poe_oauth.py` includes token refresh + bearer helpers.
  - Requires your own approved PoE OAuth app credentials from GGG.

## Outputs and Data Files

- Snapshot + diffs:
  - `logs/stat_watch/<character>_snapshot.json`
  - `logs/stat_watch/<character>_panel_stats.json`
  - `logs/stat_watch/<character>_history.jsonl`
- Discord post history:
  - `logs/discord_publish_history.jsonl`
- OpenAI build intel artifacts:
  - `logs/build_intel_runs/<timestamp>.json`

## Known Limitations

- Current zone/location is not returned by the character-window endpoints used here.
- Storage/stash visibility depends on auth and endpoint constraints.
- PoB can expose internal defaults for some mechanics; extractor logic filters known false positives (for example optional charge types not actively in use).

## Security Notes

- Keep secrets in `.env` only.
- `.env` is gitignored; `.env.example` is safe to commit.
- Do not commit `POESESSID`, webhook URLs, or API keys.

## Today’s Tracking Scope Record

- Full progression notes for today’s run are in:
  - `docs/session-2026-02-21-level1-to-act2.md`
