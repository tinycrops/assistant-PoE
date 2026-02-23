---
name: poe-weighted-trade-upgrade
description: Find Path of Exile item upgrades using trade weighted stat groups. Use when a user asks for weighted-sum item ranking, best value upgrades, or PoB/trade-style stat weighting. Supports instant-buy and in-person modes, enforces wearable level caps, uses POESESSID for authenticated trade queries, and falls back to strict-filter fetch plus local weighted reranking if weighted API queries are blocked.
---

# PoE Weighted Trade Upgrade

Use this skill to produce wearable, buyable upgrades ranked by weighted stat priorities.

## Workflow

1. Refresh live character state.
- Read current level and league from the character API.
- Never use stale level data.

2. Set trade mode.
- Use `status.option = "securable"` for instant buyout.
- Use `status.option = "online"` for in-person whispers.
- If the user provides a trade link, mirror its mode.

3. Build a weighted query.
- Add slot/category and rarity filters.
- Add `filters.req_filters.filters.lvl.max = <current_level>`.
- Add a `stats` group with `type = "weight"` (or `sum` when requested).
- Put priorities into per-filter `weight` values.
- Add practical constraints (budget, life/ES/res, required attributes).

4. Execute with authentication when possible.
- Include `POESESSID` cookie from `.env` or environment for trade calls.
- Use `POST /api/trade/search/{league}`.
- If successful, return the trade link and top listings.

5. Fallback if weighted query is blocked.
- If trade returns complexity-limit or similar failure for weighted queries:
  - Run a stricter non-weighted wearable query.
  - Fetch top candidates.
  - Compute local weighted score using the same requested weights.
  - Return ranked results and state that fallback was used.

6. Return concise output.
- Report exact level used and mode used.
- Provide one final trade link and 1-3 recommended listings.
- If fallback was used, say why in one sentence.

## API Endpoints

- Search: `https://www.pathofexile.com/api/trade/search/{league}`
- Fetch: `https://www.pathofexile.com/api/trade/fetch/{ids}?query={id}`
- Inspect existing search: `https://www.pathofexile.com/api/trade/search/{league}/{id}`
- Stat catalog: `https://www.pathofexile.com/api/trade/data/stats`
- Filter catalog: `https://www.pathofexile.com/api/trade/data/filters`

## Example Weighted Group

```json
{
  "type": "weight",
  "value": { "min": 80 },
  "filters": [
    { "id": "pseudo.pseudo_total_energy_shield", "weight": 1.2 },
    { "id": "pseudo.pseudo_increased_spell_damage", "weight": 1.2 },
    { "id": "pseudo.pseudo_increased_fire_damage", "weight": 1.1 },
    { "id": "explicit.stat_3988349707", "weight": 1.5 },
    { "id": "explicit.stat_3382807662", "weight": 1.6 },
    { "id": "pseudo.pseudo_total_life", "weight": 0.8 }
  ]
}
```

## Common IDs

- Max Life: `pseudo.pseudo_total_life`
- Max Energy Shield: `pseudo.pseudo_total_energy_shield`
- Spell Damage: `pseudo.pseudo_increased_spell_damage`
- Fire Damage: `pseudo.pseudo_increased_fire_damage`
- DoT Multiplier: `explicit.stat_3988349707`
- Fire DoT Multiplier: `explicit.stat_3382807662`
- Dexterity: `explicit.stat_3261801346`
- Total Elemental Resistance: `pseudo.pseudo_total_elemental_resistance`
