---
title: "Change the execution protocol in this repo first, then copy it to the installed skill"
author: Beyourahi
date: 2026-11-14
branch: feat/execution-protocol-sync
id: l-fee4a0a0
scope: repo
status: active
src: agent
touches:
  - skills/execution-protocol/SKILL.md
  - skills/execution-protocol/scripts/implement.sh
  - AGENTS.md
  - CONTRIBUTING.md
  - skills/execution-protocol/tests/implement-smoke.sh
---

## Decided
- `skills/execution-protocol/SKILL.md` and its folder here are the source of truth; `~/.agents/skills/execution-protocol` stays a plain copy, not a symlink. Policy, the git guard and `~/.claude/skills` resolve that path, and `skills/execution-protocol/scripts/implement.sh` resolves its own real path, so a symlink into a checkout would run unmerged branch code as live policy.
- The sync rsync in `AGENTS.md` and `CONTRIBUTING.md` has no --delete, so local-only graft output survives; drift is caught by the diff check before starting a change and after syncing the merged main.

## Tried and ruled out
- Importing the skill through `skills/execution-protocol/scripts/implement.sh` fails every round with "skip marker added in a test file", because `skills/execution-protocol/tests/implement-smoke.sh` holds an it.skip fixture. At the round cap the conductor copies it verbatim and verifies byte identity.

## Watch out
- The installed copy drifted 2026-11-11 to 2026-11-14 because the outside-repo rule in `skills/execution-protocol/SKILL.md` sent skill edits to the live files; step 5 now routes them through this repo.

In the code:
- drift check: diff -rq -x __pycache__ -x graft skills/execution-protocol ~/.agents/skills/execution-protocol
