# Execution protocol: reference

Script internals and accepted-risk documentation that `SKILL.md` refers to; every rule the conductor acts on stays in `SKILL.md`.

## Claude accounts: `cswap` rotation

Rotation runs inside `gate.sh`, exactly as follows:

- A machine-wide Opus lock serializes Opus calls; a wait timeout logs `invalid` and exits 5.
- Before the gate's Opus calls, `cswap auto --once --model Opus --json` runs: exit 1 waits `GATE_CSWAP_RETRY` (default 30 s) and retries once, then continues; exit 3 logs `blocked`, exits 3 and makes no `claude` call; any other exit continues and logs the active account number (never the email).
- After a usage-limit result, `cswap switch --strategy next-available --json` runs and the call is rerun once; a second limit logs `blocked` with `usage limit after account switch`.
- An invalid run is retried once, then logs `blocked` with `opus run invalid after retry`.
- Without `cswap`, the account is logged as `signed-in` and a usage limit still gets one rerun.

**Pass conditions.** `plan` uses `plan-schema.json`; the rest use `review-schema.json`. An Opus run counts only if it exited 0, `is_error` is false, `structured_output` is present and `modelUsage` names `claude-opus-5-5`.

## Step 6 implementer runs: residual risks

**Residual risks (accepted):** `sandbox-exec` is deprecated (a failure makes DeepSeek unavailable, never unconfined); reads outside its deny list and HTTPS egress remain, including DeepSeek's own Go credential; OpenCode's database, logs and tool output and the per-user temp and cache dirs stay writable, so a hostile run could alter OpenCode data or forge its own export; the profile blocks LaunchServices, AppleEvents, launchctl and unix sockets outside the worktree and run dir, but localhost TCP and Mach services of running apps stay reachable, so run no command-accepting local service during a round; for the Sonnet route these routes and the reaper's detection are unverified; a confined process that outlives the reaper gives exit 6; implementer code runs unconfined in the conductor's checks, hooks and CI after the conductor has read it.

## Step 8 delivery: premerge checks

It checks the clean tree; the tier re-check (final tier is the highest `Tier:` line across it and `PLAN.md`; lower fails); a logged Opus plan for Medium/High; that every gate JSON in EVID came from `gate.sh` and a Medium/High branch's first commit does not predate the first logged plan unless first gated Low; each required gate file bound to the current head, with a logged ok round, route and tier; the round counts, epoch authorizations (max 2, none denied) and blocked entries; and that every `impl.log` row is a pinned implementer route (`implement.sh --check`), then squash-merges with `--match-head-commit`.

## Gate eval set

The review-gate eval set lives in `~/.local/state/dev-evidence/sp-apply-1114/eval/` (`README.md` there: fixtures, rerun, scoring). Rescore it after any change to `opus-gate-brief.md`, and record one row per variant in `~/.local/state/review-metrics/evals.jsonl`.
