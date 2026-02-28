## Initialize Character Context
https://github.com/user-attachments/assets/20ad8f62-7558-4b1c-a1c2-bd13ba1ff931
# Roadmap
-Automated Item Filter Editing by Codex
-Automated Item Pricing by Codex
## Project Vision: Level-by-level guidance


https://github.com/user-attachments/assets/3c80ad9b-0d3a-4156-b13a-52e3a07f7061


## About

This repository is a Codex-operated Path of Exile assistant workflow: it captures character state, runs headless Path of Building calculations, enriches snapshots with market data, and publishes structured progression intelligence (`LOG`, `LEARN`, `NEXT`) to Discord.

This project is designed to be operated through [Codex](https://github.com/openai/codex): the built-in collaborative harness that sits between the player, the game, and the player's goals. In practice, Codex is the working interface that translates player intent into repeatable actions across this repo, reads game-derived data (account endpoints + PoB outputs), and turns that into structured outputs (market snapshots, memory updates, and build intelligence posts).

## User-Facing Capabilities

- Pull live character data from account endpoints (`character`, `items`, optional `passive_skills`)
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
  --account "$DEFAULT_ACCOUNT" \
  --realm "$DEFAULT_REALM" \
  --character "$DEFAULT_CHARACTER"
```

## Primary Commands

Character stat + inventory tracking:
```bash
python3 poe_stat_watch.py \
  --account "$DEFAULT_ACCOUNT" \
  --realm "$DEFAULT_REALM" \
  --character "$DEFAULT_CHARACTER" \
  --include-storage
```

Market sync + persona generation:
```bash
python3 poe_market_pipeline.py \
  --account "$DEFAULT_ACCOUNT" \
  --realm "$DEFAULT_REALM" \
  --character "$DEFAULT_CHARACTER" \
  --output openclaw_market_snapshot.json
```

Manual persona post:
```bash
python3 discord_persona_sender.py \
  --type LOG \
  --context "session/2026-02-21" \
  --body "Progress checkpoint."
```

Trade API rate-limit probe:
```bash
python3 trade_rate_limit_probe.py \
  --league "$DEFAULT_LEAGUE" \
  --mode "$DEFAULT_TRADE_MODE"
```

Weighted trade search:
```bash
python3 weighted_trade_search.py \
  --category accessory.ring \
  --price-max 35 \
  --weight-min 170 \
  --weight explicit.stat_3299347043=1.4 \
  --weight explicit.stat_2923486259=2.1 \
  --weight explicit.stat_2901986750=1.8
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
- Character memory ledger:
  - `characters/<character-slug>/ledger.json`
  - `characters/<character-slug>/journal.jsonl`
- Trade API rate-limit telemetry:
  - `logs/trade_api/rate_limit_history.jsonl`
  - `logs/trade_api/last_request_at.txt`
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

## Character Context

- `defaults.env` defines the active character Codex should operate against.
- Character-specific progress is preserved under `characters/<character-slug>/`.
- `poe_stat_watch.py` and `poe_market_pipeline.py` now update that ledger automatically so switching the active character does not discard prior observations.
- `weighted_trade_search.py` also stamps the ledger with live `level`, `class`, `league`, and `last_live_confirmed_at_utc` before building a trade query. Use that live-confirmed metadata for progression-sensitive trade advice instead of trusting an old stat snapshot.
- Progression-sensitive trade recommendations should start from a recent headless PoB stat-watch run. `weighted_trade_search.py` now requires a recent `latest_snapshot.captured_at_utc` in the character ledger by default and exits with a clear refresh message when the PoB snapshot is stale. Use `--allow-stale-pob` only when you intentionally want to bypass that guard.
- User-facing trade links should default to instant buyout via `status.option = "securable"`. Use `online` only when manual whisper trading is explicitly requested.
