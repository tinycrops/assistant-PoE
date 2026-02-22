---
name: pob-skill-damage-item-compare
description: Compare the effect of an item swap on a character's skill damage in Path of Exile using headless Path of Building. Use when a user asks which item is better for a specific skill's DPS, asks to prove an item choice with PoB calculations, or provides pasted item text and wants deterministic before/after damage deltas.
---

# PoB Skill Damage Item Compare

Run a deterministic PoB comparison between current gear and a candidate item for one target skill.

## Workflow

1. Use a snapshot JSON that contains `items` and `passive_skills`.
2. Collect candidate item text from in-game copy.
3. Run the script with target `--skill-name` and `--slot`.
4. Report baseline, candidate, and delta for key damage metrics.

## Command

```bash
python3 skills/pob-skill-damage-item-compare/scripts/compare_skill_damage_item_swap.py \
  --snapshot PathOfBuilding/spec/current_snapshot.json \
  --candidate-item-file /tmp/candidate_item.txt \
  --slot Weapon \
  --skill-name "Detonate Dead" \
  --output PathOfBuilding/spec/skill_damage_item_compare.json
```

## Inputs

- `--snapshot`: Snapshot JSON with `items` and `passive_skills`.
- `--candidate-item-file` or `--candidate-item-text`: Candidate item text.
- `--slot`: Inventory slot to replace (examples: `Weapon`, `Offhand`, `Helmet`, `BodyArmour`, `Ring`, `Gloves`, `Boots`, `Amulet`, `Belt`).
- `--skill-name`: Skill to force-select in PoB before calculating damage.
- `--metric-keys`: Optional comma-separated metric keys (default includes `CombinedDPS`, `TotalDPS`, `IgniteDPS`, `WithIgniteDPS`, `TotalDotDPS`, `AverageHit`, `Speed`).
- `--primary-metric`: Metric used for winner summary (default `CombinedDPS`).

## Output

Output JSON includes:

- `baseline_item` and `candidate_item`
- `baseline_metrics` and `candidate_metrics`
- `deltas` (candidate minus baseline)
- `summary` with winner for `--primary-metric`

Use this as the evidence payload for downstream reasoning or Discord posts.
