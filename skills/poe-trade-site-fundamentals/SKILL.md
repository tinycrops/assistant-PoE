---
name: poe-trade-site-fundamentals
description: Teach and apply Path of Exile trade site fundamentals end-to-end. Use when a user asks for trade links, item/gem/currency search setup, league-specific queries, canonical short-link generation, filter interpretation, price sorting, online vs instant-buy status behavior, or troubleshooting invalid trade URLs/queries.
---

# PoE Trade Site Fundamentals

Use this skill to produce accurate trade searches and teach the user how the official trade site works.

## Workflow

1. Confirm target league and item intent.
- Ask only when missing and risky.
- Default to the user-provided league if present in the conversation.

2. Build the search query JSON.
- Always include `query.status.option` and choose the right mode.
- Set item identity correctly (`name`, `type`, `category`, or stat filters as needed).
- Add constraints in the correct filter groups (`misc_filters`, `type_filters`, `req_filters`, `trade_filters`, `socket_filters`).

3. Generate a canonical trade link.
- Canonical user-facing format is: `https://www.pathofexile.com/trade/search/{league}/{id}`.
- Obtain `{id}` by posting JSON to `POST /api/trade/search/{league}`.
- Do not present a long `?q=` URL as the final answer when a short canonical link is expected.

4. Validate results quality before replying.
- Ensure filters match the user request exactly (e.g., quality 20 and uncorrupted).
- Confirm query mode (`online` vs `securable`) and sorting are intentional.
- Return one clean link, then short notes about mode and key filters.

5. Explain only what is needed.
- Keep the final response concise unless the user asks for a deeper tutorial.
- If the user wants learning material, teach from the reference guide below.

## Canonical Rules

- Prefer official API + short link workflow over hand-constructed `?q=` links.
- Treat `https://www.pathofexile.com/trade/search/{league}/{id}` as the source-of-truth result link.
- Use `online` for in-person whispering and `securable` for instant exchange flow when relevant.
- Keep league casing as used by the site.

## Reference Guide

Read [references/trade-site-fundamentals.md](references/trade-site-fundamentals.md) when the user asks for comprehensive explanations, advanced filtering, or debugging of incorrect queries.
