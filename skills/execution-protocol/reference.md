# Execution protocol: reference

Detail moved verbatim from `SKILL.md` (sp-apply-1114, 2026-11-14). `SKILL.md` keeps every rule the conductor acts on; this file holds script internals and accepted-risk documentation.

## Step 1 routing: Claude accounts (from SKILL.md, step 1)

- **Claude accounts (Rahi 2026-11-10):** Opus gates may rotate between Rahi's Claude Pro accounts with the installed claude-swap (`cswap`), only through `gate.sh` and Rahi's own settings; the Sonnet fallback never rotates. Never add, remove or edit accounts or settings, never use API-key accounts, extra usage or new spend, never configure OpenCode's Anthropic provider with subscription OAuth; without `cswap`, skip rotation.

## Step 6 implementer runs: residual risks (from SKILL.md, step 6)

**Residual risks (accepted):** `sandbox-exec` is deprecated (a failure makes DeepSeek unavailable, never unconfined); reads outside its deny list and HTTPS egress remain, including DeepSeek's own Go credential; OpenCode's database, logs and tool output and the per-user temp and cache dirs stay writable, so a hostile run could alter OpenCode data or forge its own export; the profile blocks LaunchServices, AppleEvents, launchctl and unix sockets outside the worktree and run dir, but localhost TCP and Mach services of running apps stay reachable, so run no command-accepting local service during a round; for the Sonnet route these routes and the reaper's detection are unverified; a confined process that outlives the reaper gives exit 6; implementer code runs unconfined in the conductor's checks, hooks and CI after the conductor has read it.

## Step 7 review gates: rotation and pass conditions (from SKILL.md, step 7)

**Rotation and pass conditions (all inside `gate.sh`).** It locks Opus calls machine-wide, rotates accounts with `cswap` per call, switches once and reruns on a usage limit, logs the account number (never the email) and logs `blocked` (exit 3) when Opus is unavailable; never resume a gate session. `plan` uses `plan-schema.json`; the rest use `review-schema.json`. An Opus run counts only if it exited 0, `is_error` is false, `structured_output` is present and `modelUsage` names `claude-opus-5-5`; A `fail` verdict is a valid run, never a switch reason; a malformed command is fixed and rerun, never unavailability.

## Step 8 delivery: premerge checks (from SKILL.md, step 8)

It checks the clean tree; the tier re-check (final tier is the highest `Tier:` line across it and `PLAN.md`; lower fails); a logged Opus plan for Medium/High; that every gate JSON in EVID came from `gate.sh` and a Medium/High branch's first commit does not predate the first logged plan unless first gated Low; each required gate file bound to the current head, with a logged ok round, route and tier; the round counts, epoch authorizations (max 2, none denied) and blocked entries; and that every `impl.log` row is a pinned implementer route (`implement.sh --check`). It writes the output into the PR body and squash-merges with `--match-head-commit`; outside a repo, `--diff FILE` runs the same checks on the diff's sha256, no `gh`. Both `PLAN.md` and the re-check need a `Tier:` line. `--no-ci` only for a repo with no CI: it passes only when `gh pr checks` reports no checks and neither the worktree nor `origin/<base>` has `.github/workflows`. Exit 1 means no merge; never write or rename a gate file by hand.

## Gate eval set

The review-gate eval set lives in `~/.local/state/dev-evidence/sp-apply-1114/eval/` (`README.md` there: fixtures, rerun, scoring). Rescore it after any change to `opus-gate-brief.md`, and record one row per variant in `~/.local/state/review-metrics/evals.jsonl`.
