# PoE Trade Site Fundamentals Reference

## Table of Contents

1. Canonical Link Formats
2. Core API Endpoints
3. Search JSON Anatomy
4. Status Modes (`online` vs `securable`)
5. Item Identity Rules
6. Common Filter Groups
7. Sorting and Price Semantics
8. Example Queries
9. Frequent Mistakes and Fixes
10. Teaching Pattern for Users

## 1. Canonical Link Formats

- Preferred final link: `https://www.pathofexile.com/trade/search/{league}/{id}`
- `{id}` is returned by `POST /api/trade/search/{league}`.
- Long-form `?q=` links can encode JSON but are not the canonical share format users expect.

## 2. Core API Endpoints

- Create search: `POST https://www.pathofexile.com/api/trade/search/{league}`
- Fetch listing details: `GET https://www.pathofexile.com/api/trade/fetch/{ids}?query={id}`
- Inspect existing search: `GET https://www.pathofexile.com/api/trade/search/{league}/{id}`
- Stat catalog: `GET https://www.pathofexile.com/api/trade/data/stats`
- Filter catalog: `GET https://www.pathofexile.com/api/trade/data/filters`

## 3. Search JSON Anatomy

Minimal shape:

```json
{
  "query": {
    "status": { "option": "online" },
    "filters": {}
  },
  "sort": { "price": "asc" }
}
```

Typical top-level keys under `query`:

- `status`: seller availability mode
- `name`: usually unique item name
- `type`: base item or gem name
- `stats`: array of explicit/pseudo/enchant/stat-group filters
- `filters`: grouped constraint buckets

## 4. Status Modes (`online` vs `securable`)

- `online`: seller is online; whisper/in-person trade flow.
- `securable`: item can be secured in the site flow (instant exchange behavior where supported).
- If user asks for "instant buy", prefer `securable`.
- If user asks for "online only" or manual trading, use `online`.

## 5. Item Identity Rules

- Gems: use `type` (example: `"type": "Blade Blast"`).
- Currency/fragments/maps: usually use category + filters rather than relying only on `type`.
- Rares: often search by category + stat filters, not by `name`.
- Uniques: `name` is often primary, `type` may be optional context.

## 6. Common Filter Groups

- `misc_filters`
  - `quality`
  - `corrupted`
  - `gem_level`
  - many item-specific misc flags
- `req_filters`
  - required level/attributes constraints
- `type_filters`
  - category/rarity/identified/etc.
- `trade_filters`
  - price constraints, indexed age
- `socket_filters`
  - links/sockets/colors

Example snippet for a gem request:

```json
"filters": {
  "misc_filters": {
    "filters": {
      "quality": { "min": 20, "max": 20 },
      "corrupted": { "option": "false" }
    }
  }
}
```

## 7. Sorting and Price Semantics

- Cheapest-first: `"sort": { "price": "asc" }`
- Highest-first: `"sort": { "price": "desc" }`
- If no listed price exists, listings may not sort as expected.
- Do not assume currency normalization details; the trade backend handles practical ordering.

## 8. Example Queries

### Example A: 20% quality, uncorrupted Blade Blast, online

```json
{
  "query": {
    "status": { "option": "online" },
    "type": "Blade Blast",
    "filters": {
      "misc_filters": {
        "filters": {
          "quality": { "min": 20, "max": 20 },
          "corrupted": { "option": "false" }
        }
      }
    }
  },
  "sort": { "price": "asc" }
}
```

Workflow:

1. `POST /api/trade/search/{league}` with the JSON above.
2. Read response field `id`.
3. Return `https://www.pathofexile.com/trade/search/{league}/{id}`.

### Example B: same gem but instant-buy style

Use the same payload and replace:

```json
"status": { "option": "securable" }
```

## 9. Frequent Mistakes and Fixes

- Mistake: share long `?q=` links as final output.
  - Fix: always convert to canonical `/trade/search/{league}/{id}` via API search creation.

- Mistake: wrong league in URL.
  - Fix: use user-specified league exactly; if absent, ask or apply a clear default.

- Mistake: only setting `quality.min = 20` without max.
  - Fix: for exact 20, set both `min` and `max` to `20`.

- Mistake: forgetting `corrupted` condition.
  - Fix: add `"corrupted": { "option": "false" }` for uncorrupted.

- Mistake: mismatched mode vs user intent.
  - Fix: map intent explicitly (`online` or `securable`).

## 10. Teaching Pattern for Users

Use this concise structure when users ask to learn:

1. Clarify league and intent.
2. Explain query anatomy (`status`, identity, filters, sort).
3. Show one minimal JSON example.
4. Show API call and canonical link conversion.
5. Point out 2-3 common mistakes to avoid.
