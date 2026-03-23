# Character Continuity Benchmark

This experiment asks a repo-native question:

> Given the current traced state of a Path of Exile character, can a model identify the true immediate next state from a mixed candidate set?

The benchmark is built entirely from existing `assistant-PoE` artifacts, primarily:

- `logs/stat_watch/*_history.jsonl`
- the character-centered thesis documented in `docs/research/repo-retrospective-2026-03-22.md`

## Why this benchmark

The repository treats a character as a living memory object rather than a one-shot payload. That makes continuity a first-class property worth testing. Instead of asking for open-ended advice immediately, this benchmark measures whether a model can track the progression logic already present in the traces.

## Task

For each anchor state:

- one candidate is the true next chronological state for that character
- several candidates are distractors drawn from same-character non-adjacent states and other-character states

The model must rank the true next state highest.

## Files

- `build_benchmark.py`: reconstructs cumulative stat/gear states from history logs and writes `generated/continuity_benchmark.json`
- `train_ranker.py`: trains a small pairwise ranker and compares against a heuristic baseline

## Metrics

- `top1_accuracy`
- `mrr`

## Notes

- This is a small-data benchmark. It should be treated as an early traction probe, not a definitive research result.
- The benchmark is still valuable because it is grounded in real repository traces and tests the repo's core continuity thesis directly.
