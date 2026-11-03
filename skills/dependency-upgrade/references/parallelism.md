# Parallel work, isolation and integration

Default: **serial**, one agent. Parallelize only when the harness has subagents, the
machine has headroom, the user's policy allows it, and the pieces are truly independent.
Follow the user's global concurrency limits (they win over this file). Typical safe split:

| Work | Parallel? | Notes |
| --- | --- | --- |
| Changelog/migration-guide research per package family | yes, read-only | each worker returns cited findings, no edits |
| Fixture or test design | yes | separate scratch dirs |
| Code migrations in disjoint packages/directories | yes, carefully | each in its own worktree + branch |
| Lockfile / manifest version changes | **no** | single integration owner |
| Installs, builds, test suites | limited | respect machine load; separate ports/caches |

## Rules for mutating workers
1. The lead creates the run and the integration worktree (`run worktree`). Each worker
   gets its own owned worktree from the same base with `depup.py run worker <run_ref>
   --domain <name>` (branch `depup/<run-id>-<domain>`, path `<run_dir>/workers/<domain>`).
   The journal records it, so `status` and `rollback` cover it. Never create worker
   worktrees by hand.
2. Each brief names: objective, absolute worktree path, owned files/directories, files
   it must not touch (always: lockfiles and root manifests), base commit, checks to run,
   deliverable (commits on its branch + summary), and "no push/merge".
3. Workers never run installs that rewrite the lockfile. They may install from the
   integration branch's lockfile in their own worktree (frozen) for testing.
4. Separate ports, temp dirs and caches per worker (`PORT`, `TMPDIR`, tool cache env vars).
5. Only the lead integrates: one cherry-pick/merge at a time into the integration branch,
   resolving conflicts semantically, re-running the quick gate after each.

## Integration and consolidation
- Deterministic consolidation: list every worker branch, `git log <base>..<branch>`,
  integrate in dependency order, verify each, record the result per branch.
- Do **not** route integration through a general "consolidate everything into main" skill
  that deletes branches/worktrees or pushes to the default branch; it conflicts with this
  skill's no-delete/no-push rules.
- Worker worktrees are removed only when their commits are integrated and they are clean
  (`git worktree remove` without `--force`); otherwise keep and report them.

## Review
Review the integrated candidate only, through the review router
([review-routing.md](review-routing.md)): one model per review task, tasks run one at a time, never
a parallel committee or a duplicate review of the same candidate. Fix findings on the integration
branch and re-verify. If the router reports a required review `unavailable`, do not merge.
