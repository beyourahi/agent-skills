# Verification gates

## Quick gate (after every batch)
1. Native locked/immutable install from the batch's manifest and lockfile succeeds; use `--frozen`
   only when the ecosystem's `--locked`/immutable mode is unavailable and separately verify the
   lock matches its manifests.
2. Typecheck/compile.
3. Unit tests touching the changed area (or the full fast suite if cheap).

## Full gate (before reporting PASS)
| Check | How | Notes |
| --- | --- | --- |
| Deterministic install | locked/immutable install in a clean worktree | prove manifests and lock agree; lockfile must not change |
| Lockfile diff | `git diff <base> -- <lockfiles>` summary | new packages, sources, integrity |
| Build | repo build script | writes build output (say so) |
| Typecheck / lint | repo scripts | no new suppressions |
| Tests | unit, integration, e2e that exist | compare with baseline |
| Security | ecosystem audit; `npm audit signatures` where supported | before vs after |
| Deprecated APIs | compiler/runtime warnings, deprecation logs | list remaining |
| CI parity | run the same commands CI runs, same runtime version | note platform gaps |
| Runtime smoke | start the app/CLI locally, hit critical paths | browser checks only with a browser tool |
| Metrics | only repo-provided measurements | baseline vs after, same machine/command |

Web apps: SSR build, preview start, critical routes, console errors, hydration warnings,
accessibility checks that the repo already runs. Native/desktop: packaging, signing,
OS/arch targets as relevant; simulators/devices only if present.

## Result rules
- Record each check as `{"check", "command", "result": "pass|fail|skipped|baseline-fail",
  "log", "evidence"}` in the report `verification` list. Keep the output log in the run
  directory. Add a separate JSON receipt beside it containing the run ID, clean candidate commit,
  exact command as `subject`, `kind: "verification"`, result, reviewer/runner identity, artifact
  path and SHA-256 of the log. The helper rejects missing, stale, mismatched or modified receipts.
  Inspect the log and bind its result to the exact candidate and environment before recording it.
- Baseline failures stay `baseline-fail`; they are not caused or fixed by the upgrade
  unless the upgrade fixed them (say so with evidence). A `baseline-fail` entry keeps the run at
  `PARTIAL` by design: the candidate is still delivered when every other check passes and the
  failure is identical to the baseline, but it is never merged as `PASS`.
- Flaky test: rerun once, record both results, never call it pass silently.
- `PASS` needs: at least one recorded check, every check `pass`, no open journal steps, the
  `baseline`, `verify` and `review` steps done,
  a complete high-risk dossier and both required reviews for each high-risk item, every
  `required` routed review completed and passed for the current candidate, no blocked
  risk disposition, no `skipped_checks` entry with `required: true`, and no upgrade with
  status `held`, `blocked` or `failed`. High-risk work never waits for user approval. The
  `report` command enforces these evidence requirements and downgrades unsupported `PASS` to
  `PARTIAL`.
- A failing or skipped required check means `PARTIAL` (work done, gaps listed),
  `BLOCKED` (a required external or physically human-only action remains) or `FAIL` (upgrade abandoned).
- Never claim "no regressions" without measurements; say "not measured".
