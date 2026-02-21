# Discord Persona + Market Pipeline

## Security First
- Rotate any webhook URL that was ever pasted in chat.
- Store webhook in env var instead of command history:

```bash
export DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'
```

## 1) Send Structured Persona Posts

```bash
python3 discord_persona_sender.py \
  --type LOG \
  --context "T16-Session/2026-02-17" \
  --body "Flask uptime collapsed during backtrack; death chain from DoT overlap." \
  --dry-run
```

Remove `--dry-run` to post.
Each successful post is appended to `logs/discord_publish_history.jsonl`.

## 2) Run Character + Market Sync Pipeline

```bash
python3 poe_market_pipeline.py \
  --account "Name#1234" \
  --realm pc \
  --character "OpenClawZeroZeroZero" \
  --output openclaw_market_snapshot.json \
  --dry-run
```

Remove `--dry-run` to post 3 messages (`LOG`, `LEARN`, `NEXT`) to Discord.
Those posts are also appended to `logs/discord_publish_history.jsonl`.

## 3) Generate Build Intelligence Card With OpenAI GPT-5.2

```bash
export OPENAI_API_KEY='...'
python3 openai_build_intel_card.py \
  --snapshot openclaw_market_snapshot.json \
  --dry-run
```

Remove `--dry-run` to post the GPT-generated card to Discord.
Each run writes an artifact JSON to `logs/build_intel_runs/<timestamp>.json` so outputs are reproducible and auditable.

If account data is private, add:

```bash
--poesessid "<32-hex>"
```

## What This Pipeline Does
- Uses the existing PoE flow (`get-characters`, `get-items`, optional `get-passive-skills`).
- Pulls live prices from `poe.ninja` current stash economy endpoints.
- Estimates known value for priced holdings (best for currency/uniques/div cards).
- Produces actionable persona posts for memory and next-session decisions.

## Notes
- Rare-crafted gear is usually unpriced by this lightweight pipeline and should be checked manually on trade.
- Keep `POESESSID` and webhook URLs out of committed files.
- To inspect publish history:

```bash
tail -n 20 logs/discord_publish_history.jsonl
```

- To inspect model-generation artifacts:

```bash
ls -lt logs/build_intel_runs | head
```
