# Executed Plan and First Results

## Objective

Use the data already present in `assistant-PoE` to invent and run a benchmark that tests whether a model can learn something meaningful about the repository's intended behavior.

The chosen angle was:

> Treat the character as a living memory object and evaluate whether a model can identify the true immediate next traced state of that character.

That directly matches the repository thesis documented in:

- `docs/research/repo-retrospective-2026-03-22.md`
- `AGENTS.md`

## Plan

1. Inspect existing durable traces in `characters/` and `logs/stat_watch/`.
2. Build a benchmark from those traces rather than inventing synthetic labels.
3. Define an evaluation that tests continuity, not just one-shot prediction.
4. Train at least one small model on the extracted data.
5. Compare against a non-neural heuristic baseline.

## What Was Built

### 1. Benchmark generator

`build_benchmark.py`

- Reads `logs/stat_watch/*_history.jsonl`
- Reconstructs cumulative state vectors over time
- Tracks 28 numeric stats and 16 gear-slot occupancy signals
- Builds anchor -> true-next-state ranking examples
- Adds distractors from same-character non-adjacent states and other-character states

### 2. Ranker

`train_ranker.py`

- Trains a small MLP over pair features:
  - anchor stats
  - candidate stats
  - deltas
  - absolute deltas
  - slot occupancy transitions
  - same-character flag
  - time delta
- Evaluates ranking quality using:
  - `top1_accuracy`
  - `mrr`

### 3. Baseline

A heuristic baseline ranks candidates by:

- same-character preference
- minimal total stat delta
- small positive time delta

## Data Extracted

From the current repository state, the benchmark built:

- 3 characters with usable stat-watch histories
- 20 reconstructed states
- 12 train anchors
- 5 eval anchors

This is small, but it is fully grounded in real repo traces.

## Commands Run

```bash
python3 research/character_continuity_benchmark/build_benchmark.py
python3 research/character_continuity_benchmark/train_ranker.py \
  --dataset research/character_continuity_benchmark/generated/continuity_benchmark.json \
  --output research/character_continuity_benchmark/generated/results.json \
  --epochs 400 \
  --hidden-dim 128 \
  --device cpu
```

## Results

From `generated/results.json`:

- Heuristic eval:
  - `top1_accuracy = 0.8`
  - `mrr = 0.85`
- Learned model eval:
  - `top1_accuracy = 1.0`
  - `mrr = 1.0`

## Interpretation

This is real traction, with three important caveats:

1. The model is not solving Path of Exile broadly.
   It is solving a narrow continuity-ranking task.

2. The dataset is tiny.
   The result is promising, not conclusive.

3. The current eval split is mostly the tail of `collabwitch_codex`.
   So this is strongest as a proof that continuity signal exists in the traces, not yet a robust cross-character benchmark.

## Why This Still Matters

This experiment demonstrates that:

- the repo already contains enough structured history to support a benchmark,
- the benchmark can be built from existing logs rather than manual annotation,
- continuity is measurable,
- a learned model can outperform a heuristic on the held-out ranking task.

That means the repository is already beyond "tool scripts" and into the territory of trainable character-state intelligence.

## Next Moves

1. Expand the dataset with archived snapshots and future stat-watch runs.
2. Add market-sync and journal events as additional modalities.
3. Build a second benchmark:
   - state -> next action prompt
   - or state -> journal event type / milestone
4. Remove or reduce reliance on the same-character feature and make negatives harder.
5. Scale the benchmark once the task contract feels stable, then consider Kaggle GPUs/TPUs for larger runs.
