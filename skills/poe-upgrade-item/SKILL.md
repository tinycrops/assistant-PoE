---
name: poe-upgrade-item
description: Find upgrade items for Path of Exile characters using the live trade API. Use when a user wants item recommendations, cheapest listings, instant-buy links, or trade filters tailored to current character constraints (especially current level and stat needs).
---

# PoE Upgrade Item

Use this workflow to produce wearable, buyable upgrade links and recommendations.

## Workflow

1. Refresh character state before every recommendation.
- Run the local snapshot pipeline for the named character.
- Read current class, level, key skills, and equipped slot to replace.
- Never rely on stale level data.

2. Confirm trade mode.
- Use `status.option = "securable"` for instant-buyout.
- Use `status.option = "online"` for in-person whisper.
- If the user gives a trade link, fetch `GET /api/trade/search/{league}/{id}` and mirror its mode.

3. Build a wearable query.
- Add a required-level cap: `filters.req_filters.filters.lvl.max = <current_level>`.
- Add category/type filters for the target slot.
- Add the user-requested stat filters (for example Dexterity minimum).
- Add practical guardrails when appropriate (life and total resist minimums).

4. Execute and verify.
- Submit `POST /api/trade/search/{league}`.
- Fetch top listings via `GET /api/trade/fetch/{ids}?query={search_id}`.
- Report top prices, key mods, and include one direct trade link.

5. Return concise output.
- State the exact current level used.
- State instant-buy vs in-person mode used.
- Provide the final trade link and 1-3 best listings.

## API Notes

- Search endpoint: `https://www.pathofexile.com/api/trade/search/{league}`
- Fetch endpoint: `https://www.pathofexile.com/api/trade/fetch/{ids}?query={id}`
- Inspect existing search: `https://www.pathofexile.com/api/trade/search/{league}/{id}`
- Stat definitions: `https://www.pathofexile.com/api/trade/data/stats`
- Filter definitions: `https://www.pathofexile.com/api/trade/data/filters`

## Common Filters

- Instant buyout: `query.status.option = "securable"`
- In-person online: `query.status.option = "online"`
- Max required level: `query.filters.req_filters.filters.lvl.max = <level>`
- Rare ring category: `query.filters.type_filters.filters.category.option = "accessory.ring"`
- Rarity rare: `query.filters.type_filters.filters.rarity.option = "rare"`

Useful stat IDs:
- Dexterity: `explicit.stat_3261801346`
- Maximum Life: `explicit.stat_3299347043`
- Total Elemental Resistance (pseudo): `pseudo.pseudo_total_elemental_resistance`

## Minimal Query Template

```json
{
  "query": {
    "status": {"option": "securable"},
    "filters": {
      "type_filters": {
        "filters": {
          "category": {"option": "accessory.ring"},
          "rarity": {"option": "rare"}
        }
      },
      "req_filters": {
        "filters": {
          "lvl": {"max": 40}
        }
      }
    },
    "stats": [
      {
        "type": "and",
        "filters": [
          {"id": "explicit.stat_3261801346", "value": {"min": 20}}
        ]
      }
    ]
  },
  "sort": {"price": "asc"}
}
```
