# Codex Repair Loop Research Report

## Why the Current System Works, When It Works

**Date:** 2026-03-23  
**Repository:** `assistant-PoE`  
**Primary runtime:** Codex conversations in `~/.codex`

## Abstract

This report documents a practical shift in how we should think about the current system.

The important discovery is not that the assistant is broadly reliable.
It is that the system already contains a valuable learning loop:

1. the user brings a real problem,
2. Codex makes an attempt,
3. Codex gets something wrong,
4. the user names the mistake,
5. Codex repairs the approach,
6. the result sometimes becomes genuinely useful.

That loop is the real product substrate right now.
It is also the best research substrate we currently have.

Instead of treating the conversation history in `~/.codex` as messy residue, we should treat it as a corpus of failure, correction, and repair.

## Main Claim

The current system should be understood as a **supervised repair environment**, not an autonomous application.

That means:

- the user's corrective feedback is first-class data,
- the assistant's mistakes are not just failures,
- and the most valuable training signal is the transition from bad attempt to repaired method.

## Evidence Base

This conclusion is grounded in direct inspection of:

- `/home/ath/.codex/history.jsonl`
- `/home/ath/.codex/sessions/...`
- the current `assistant-PoE` research artifacts
- the first seed repair dataset now added under `research/codex_repair_dataset/`

## What We Found

### 1. `~/.codex` already contains explicit correction signals

The history index contains clear user interventions like:

- `you didn't check the builds that are attached to the build links you sent me`
- `you doubled down on your mistakes from step3`
- `the solution you made is making my computer hum`

These are not vague dislikes.
They are precise labels for assistant failure.

### 2. Canonical session logs preserve the full repair sequence

The rollout transcripts make it possible to recover:

- the original request,
- the assistant's mistaken method,
- the user's correction,
- and the repaired answer.

That is enough to build explicit repair episodes instead of vague chat summaries.

### 3. The fractured `% increased maximum Mana` jewel session is a quintessential artifact

Canonical transcript:

- [rollout-2026-03-22T22-07-36-019d1872-9877-7772-8153-0fcccdba8a2d.jsonl](/home/ath/.codex/sessions/2026/03/22/rollout-2026-03-22T22-07-36-019d1872-9877-7772-8153-0fcccdba8a2d.jsonl)

Why it matters:

- the user asked a practical market question about a fractured mana jewel base,
- the first answer overfit to aggregate poe.ninja slices,
- the user correctly pointed out that the linked builds did not actually validate the claim,
- the repair switched to inspecting attached build data directly.

This is exactly the pattern we want to capture and learn from.

## Research Artifacts Added

### Repair dataset scaffold

New subtree:

- [README.md](/home/ath/Desktop/assistant-PoE/research/codex_repair_dataset/README.md)
- [repair_episode_schema.json](/home/ath/Desktop/assistant-PoE/research/codex_repair_dataset/repair_episode_schema.json)
- [mistake_taxonomy.json](/home/ath/Desktop/assistant-PoE/research/codex_repair_dataset/mistake_taxonomy.json)
- [extract_from_history.py](/home/ath/Desktop/assistant-PoE/research/codex_repair_dataset/extract_from_history.py)
- [extract_from_sessions.py](/home/ath/Desktop/assistant-PoE/research/codex_repair_dataset/extract_from_sessions.py)
- [repair_episodes.jsonl](/home/ath/Desktop/assistant-PoE/research/codex_repair_dataset/projected/repair_episodes.jsonl)

The first seed episode is the fractured mana jewel case.

### Semantic memory upgrade

We also rebuilt the semantic memory index and fixed the retrieval path so `~/.codex` can be searched by meaning instead of only keywords.

Operational outcome:

- the index now rebuilds against `ath-ms-7a72` for Ollama embedding inference,
- and the mana-jewel repair artifact is retrievable semantically.

## Why This Matters

This gives us a more honest and more useful direction for experimental modeling.

We do not need to pretend the assistant is close to full autonomy.
We already have something more concrete:

- a live loop,
- real correction data,
- repeated mistake classes,
- and a growing corpus of repaired episodes.

That is enough to start training narrow helpers for:

- correction detection,
- mistake classification,
- repair planning,
- and acceptance prediction.

## Practical Reframing

The right near-term goal is not:

> build a fully autonomous assistant

The right near-term goal is:

> reduce repeated mistake classes inside the current repair loop

That is measurable.
It also fits the current reality of the product much better.

## Next Steps

1. Continue promoting high-value Codex sessions into repair episodes.
2. Hand-label the first 25-50 episodes with the small mistake taxonomy.
3. Use the semantic memory index to retrieve related past repair cases by meaning.
4. Train small models in `~/fontemon` only on narrow repair tasks first.
5. Measure success by reduced corrective turns and lower recurrence of the same mistake class.

## Conclusion

The current system is not broken because it needs a better story.
It needs better use of the story it is already generating.

Every time the user says:

- `that's wrong`
- `that's not what I meant`
- `you missed the method`
- `fix it`

the system is being handed one of the highest-value supervision signals available to it.

This report marks the point where we stop treating that signal as noise and start treating it as the foundation of the next phase of research.
