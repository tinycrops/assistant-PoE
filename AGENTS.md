# PoE Discord Memory+Market Guide

## Objective
Run the working pipeline that:
- syncs character data,
- enriches with live market pricing,
- posts structured memory updates (`LOG`, `LEARN`, `NEXT`),
- can post a visual build intelligence card.

## Required Input
- `account` with discriminator, e.g. `Name#1234`
- `realm`: `pc`, `xbox`, or `sony`
- optional `character` name
- optional `POESESSID` (only if needed)
- `DISCORD_WEBHOOK_URL`
- `OPENAI_API_KEY` (for GPT-5.2 Build Intelligence Card generation)

## Working Programs
- `poe_market_pipeline.py`: live character + market sync + persona post generation
- `discord_persona_sender.py`: manual structured posts
- `post_build_intel_card.py`: polished build-aware Discord embed from snapshot
- `openai_build_intel_card.py`: repeatable GPT-5.2 Build Intelligence Card generator/poster
- `discord_publish_log.py`: append-only publish history logger used by all posting scripts

## Fast Flow (Working)
1. Set webhook env var.
2. Run pipeline in dry-run.
3. Run pipeline live (posts to Discord).
4. Optionally post build intelligence card from snapshot.

## Commands

Install/update dependencies:
```bash
pip3 install -r requirements.txt
```

Load saved defaults for this workspace:
```bash
set -a; source defaults.env; set +a
```

Set webhook:
```bash
export DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'
```

Preview persona output:
```bash
set -a; source defaults.env; set +a
python3 poe_market_pipeline.py \
  --account "$POE_DEFAULT_ACCOUNT" \
  --realm "$POE_DEFAULT_REALM" \
  --character "$POE_DEFAULT_CHARACTER" \
  --output openclaw_market_snapshot.json \
  --dry-run
```

Post persona output to channel:
```bash
python3 poe_market_pipeline.py \
  --account "Name#1234" \
  --realm pc \
  --character "OpenClawZeroZeroZero" \
  --output openclaw_market_snapshot.json
```

If private/auth-required:
```bash
python3 poe_market_pipeline.py \
  --account "Name#1234" \
  --realm pc \
  --character "OpenClawZeroZeroZero" \
  --poesessid "<32-hex>" \
  --output openclaw_market_snapshot.json
```

Manual persona post:
```bash
python3 discord_persona_sender.py \
  --type LOG \
  --context "session/2026-02-17" \
  --body "Short factual note for memory." \
  --dry-run
```

Build intelligence card from snapshot:
```bash
python3 post_build_intel_card.py \
  --snapshot openclaw_market_snapshot.json \
  --dry-run
```

Post build intelligence card:
```bash
python3 post_build_intel_card.py \
  --snapshot openclaw_market_snapshot.json
```

Generate build intelligence card with OpenAI GPT-5.2 (repeatable process):
```bash
export OPENAI_API_KEY='...'
export MLFLOW_TRACKING_URI='http://127.0.0.1:5000'  # optional
export MLFLOW_EXPERIMENT_NAME='PoE-Build-Intel'      # optional
python3 openai_build_intel_card.py \
  --snapshot openclaw_market_snapshot.json \
  --observability jsonl+mlflow \
  --dry-run
```

Post GPT-5.2 generated build intelligence card:
```bash
python3 openai_build_intel_card.py \
  --snapshot openclaw_market_snapshot.json
```

Launch local observability viewer (prompts + outputs + events):
```bash
python3 observability_viewer.py --host 0.0.0.0 --port 5051
```

## Notes
- Market pricing is strongest for currency, uniques, and divination cards.
- Rare-crafted gear still needs manual trade checks.
- For item swap DPS proofs, use `skills/pob-skill-damage-item-compare` to compare how a candidate item changes a selected skill's damage metrics in headless PoB.
- `https://poedb.tw/poe-api` is a valuable quick reference for PoE endpoint discovery and payload shapes; verify final trade behavior against live `pathofexile.com` API responses because docs there can lag current production behavior.
- Keep `POESESSID` and webhook secrets out of committed files.
- Use environment variables for secrets (`OPENAI_API_KEY`, `DISCORD_WEBHOOK_URL`); do not pass secrets in command history when avoidable.
- Publish history path (default): `logs/discord_publish_history.jsonl`
- Optional override on all posting scripts: `--log-path <path>`
- OpenAI run artifacts path (default): `logs/build_intel_runs/<timestamp>.json`
- DSPy observability JSONL path (default): `logs/dspy_observability.jsonl`
- Observability mode env var: `DSPY_OBSERVABILITY=off|jsonl|jsonl+mlflow`
- Observability log path env var: `DSPY_OBSERVABILITY_LOG_PATH=<path>`

Inspect recent publish records:
```bash
tail -n 20 logs/discord_publish_history.jsonl
```
