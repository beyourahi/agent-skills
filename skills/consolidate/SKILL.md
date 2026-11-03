---
name: consolidate
description: Get all unfinished work from every branch, worktree, clone, stash and PR into main, then delete everything else. Use when the user says "consolidate".
license: MIT
---

Consolidate and clean up this repo: get every piece of unfinished work into main, then delete all other branches and worktrees. No backups. The verification below is the safety net, so be thorough.

SCOPE (include work created by the user AND by any AI agent/harness/model):
- All worktrees registered to this repo (`git worktree list`), wherever they live on disk
- Other clones/copies of this repo anywhere on the computer (search the home dir and common agent/temp dirs for git repos whose remote matches this repo's origin)
- All local branches, stashes, and uncommitted/untracked changes in every location above
- All remote branches and open PRs on GitHub (use `gh`)

STEP 1 — AUDIT (read-only):
- Fetch and prune remotes. Update main to the latest.
- For every branch/worktree/clone/stash/PR, find what is NOT already in main: unmerged commits (`git cherry`, `git log main..branch`), uncommitted changes, untracked files.
- Classify each as: (a) already in main, (b) obsolete/superseded, or (c) still-needed work.
- Start the routing run now (`$R`: see ROUTING), on main at its pre-consolidation commit: `python3 $R start --skill consolidate --repo . --base <main SHA>` (every review compares against this base).
- PLAN (read-only, before any integration decision or mutation): when there is still-needed or uncertain work, write a handoff (scope, the audit ledger with every item and its evidence, repo instructions, constraints, conflict risks) and run ONE routed planning task (see ROUTING). Add `--kind architecture --subject <item>` only for a significant/high-risk item (diverged design, big conflict, schema/auth/deploy changes). Skip it when everything is already in main or obsolete. Check the returned plan against these rules and the repo's instructions; adopt or reject each part with a reason. The plan never approves, authorizes or deletes anything. Planning `unavailable`/`handoff` with no fresh session: plan here and say so.
- Show the user a summary table (with the adopted plan) and wait for approval before changing anything.

STEP 2 — RECONCILE:
- Bring every (c) item into main by merging, cherry-picking, or porting by hand if main has diverged. Resolve conflicts in favor of main's current design.
- Commit any uncommitted work you need to keep before integrating it.
- Build and run the tests. Fix anything broken. Push main.

STEP 3 — VERIFY:
- Re-check every branch, worktree, stash, and PR against the updated main. Confirm that each one is either fully in main or that the user approved discarding it.
- Ask the user about anything you're unsure of. Never discard it silently.
- INDEPENDENT REVIEW (always, even when no code changed; before any cleanup): write the ledger (every item, its classification, how it was integrated, conflict resolutions, build/test commands and results, and the user's exact approval for each item to discard) and run `review --kind reconciliation --required --context <ledger> --with-prs` plus `--location <path>` for every extra clone (and `--main-ref <branch>` if the default branch is not detected). If main's code changed, also run a separate `review --kind code --required`. Fix findings here (never in the reviewer), re-run affected builds/tests, then review the new candidate. Then run `gate --require reconciliation` (add `--require code` when main's code changed); it fails if a required review is missing, failed, unavailable, stale (main or any branch/worktree/stash/clone/PR changed since), or its receipt was altered.

STEP 4 — CLEANUP (only after verify passes, `gate` exits 0, and the user authorized every discarded item):
- Remove all worktrees (`git worktree remove`, then `git worktree prune`)
- Delete all local branches except main
- Delete all remote branches except main. Close leftover PRs with a comment saying the work was consolidated into main.
- Drop stashes. Ask before deleting any extra clones from disk.

If a required review cannot run (all routes `unavailable`, or a `handoff` you cannot run in a fresh read-only native subagent), say so and do NOT clean up; integration stays, cleanup waits.

ROUTING (shared with dependency-upgrade; one implementation): `R=~/.agents/skills/dependency-upgrade/scripts/routed_task.py`
- `start` (STEP 1) → `run_dir`. Then `python3 $R plan <run_dir> --context <handoff>` / `python3 $R review <run_dir> --kind <k> ...` / `python3 $R gate <run_dir>`. Always pass `--session-harness`, `--session-model`, `--session-effort` with this session's exact values. Run tasks one at a time as background commands (a task can take up to 30 min) and wait for the JSON.
- Each task runs on exactly one model, in a fresh read-only session outside the repo, by fixed priority with usage-aware fallback: Claude Opus 5.5 medium (Claude Code) → GPT-6.1 Sol high (Codex CLI) → DeepSeek V4.1 Flash 1017 high (OpenCode Go `opencode-go/deepseek-v4.1-flash`) → this session's own model/effort/harness. Unknown quota is attempted, not skipped; exhausted, unauthenticated, unsupported or failing routes fall back automatically; nothing is substituted, and global models, accounts and credentials are never changed. `python3 $R probe` shows availability without model calls.
- `reused` means the same candidate was already reviewed/planned: don't repeat it. `handoff` (harness without a CLI adapter): run the printed prompt in a fresh read-only native subagent, then `python3 $R record`. Never review your own work in this session to satisfy a required review.
- Implementation, merging, conflict resolution, fixes and cleanup stay in this session. Evidence lives in the run dir; put the selected model, fallbacks with reasons, verdicts and the gate result in the report.

Finish with a short report: what was integrated, what was discarded, and what was deleted, plus planning/review routing (model used, fallbacks and why, verdicts) and the gate result.
