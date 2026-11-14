---
title: "A returned plan must match the requested risk tier, and the plan author is told that tier"
author: Beyourahi
date: 2026-11-14
branch: feat/ep-resume-tier-check
cost: { minutes: 1, tokens: 14920 }
id: l-7031fa58
scope: repo
status: active
src: agent
---

## Decided
The plan gate now rejects a plan whose tier differs from the one requested (lower or higher), reusing the retry-once-then-blocked path. Exact match was chosen because the tier is set by rules and raised only by the tier re-check, which needs a fresh plan anyway.

## Tried and ruled out
Checking the tier without telling Opus the requested tier: Opus review round 1 failed it, because the plan packet is just facts.md and Opus would pick its own tier, blocking valid plans with a misleading "opus run invalid" reason. Editing the gate brief instead was avoided because any brief change requires rescoring the gate eval set.

## Watch out
SKILL.md sits a few bytes under its 23300-byte cap; additions must replace words. Running `trail check` inside a task worktree creates an untracked .trail/cache/ that makes implement.sh refuse with "worktree is not clean"; remove it before the next round.

In the code: scripts/gate.sh classify() and run_claude() (plan prompt gets "Requested tier: <tier>"); tests/smoke.sh plan tier mismatch pairs; SKILL.md resume line in "Never a reason to stop".
