# Review routing

`depup.py review run` executes ONE review task on ONE model in a fresh, read-only session. It is
the only way the `review` step runs a review. Code: `scripts/review_router.py`; the `depup.py`
wrapper prepares inputs, binds the result to the candidate and writes the evidence.

## Priority (fixed; identical from every harness)

| # | Model | Effort | Harness (only) | Pre-run checks (no model calls) |
| --- | --- | --- | --- | --- |
| 1 | Claude Opus 5.5 (`claude-opus-5-5`) | medium | Claude Code `claude -p` | CLI on PATH and runs; `claude --help` effort list; `claude auth status --json` (only loggedIn/method/provider/plan kept) |
| 3 | DeepSeek V4.1 Flash 1017 (`deepseek-v4.1-flash`) | high | OpenCode Go `opencode run` | CLI; `opencode api model.list`: provider `opencode-go`, exact id `deepseek-v4.1-flash`, `high` variant |
| 4 | invoking session's model | session effort | invoking harness | CLI presence only |

Never skip a higher priority for speed or cost, never substitute a model, provider or effort, and
never change global defaults, accounts or credentials. #3 matches the OpenCode Go id
`deepseek-v4.1-flash` exactly (OpenCode confirmed this id serves the 1017 build, 2026-11-03); other ids are never used in its place. A Claude run whose `modelUsage` names only other
models is classified `substituted` and discarded (pass full model ids, not aliases). Reviewer
processes never see `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`, and Claude API-key auth
counts as unavailable: subscriptions only, no new spend.

## Statuses

- `available`: usable and usage confirmed by a pre-run check.
- `unknown`: usable but usage is not reported before a run (Claude Code, OpenCode Go). Unknown is
  not exhausted: the review itself is the attempt; no preliminary model call is made. Claude's
  `rate_limit_event` from the run records actual usage status and reset time.
- `exhausted`: confirmed by a pre-run check (rate limit) or by the
  run (`rate_limit_event` `rejected`, 429/usage-limit errors). Remembered for the run until its reset
  time (else 30 min) in `<run_dir>/reviews/provider-state.json`.
- `unavailable`: missing/broken CLI, not authenticated, unsupported model or effort, catalogue
  unreadable (an exact id is never guessed). Remembered for the run.

## Fallback and retries

Failure categories come only from error output (stderr, error events, an `is_error` result), never
from review prose, so a review that discusses "401" or "rate limit" cannot misroute later reviews.
Advance to the next candidate on: missing CLI, auth failure, unsupported model/effort, confirmed
exhaustion, a definitive rate-limit/usage error, a timeout (default 1800 s per attempt, never retried),
an incomplete or malformed result, or a transient service error (5xx, overloaded, network) that
persists after ONE retry with backoff. A failed attempt's output is kept as evidence, marked
`DISCARDED`, and never used; the review restarts from scratch on the next candidate. Attempts run
strictly one after another; a per-review lock refuses a duplicate run, and a completed review of the
same candidate is returned as `reused` without a model call.

Final fallback: `--session-harness/--session-model/--session-effort` launch the same CLI adapters
with exactly those values (skipped if identical to a candidate that already failed definitively).
An explicit `--session-harness` always wins over environment detection. A session in an adapter
harness (claude-code, opencode, pi) without `--session-model` is refused up front. Only for a
harness without an adapter (e.g. Freebuff) does the router return
`status: handoff` with a `next` command: run `prompt.md` in a fresh-context, read-only native
subagent of the session (never the authoring conversation), save its answer, then
`depup.py review record <run_ref> --review-id <id> --output <file> --reviewer "<harness>/<model>@<effort>"`.
If nothing can run, the entry is `unavailable`; never fabricate or self-certify a review. A
recorded handoff is attested by the session, not verified by the helper: use it only as described.

Runtime: one task can take up to `--timeout` (1800 s) per attempt across several candidates. Start
`review run` as a background command (or with the harness's longest tool timeout) and wait for its
JSON. If the helper is killed, it stops its reviewer (SIGTERM) and a later run reclaims the lock of a
dead holder; a live holder's lock refuses duplicates.

## Isolation

Reviewers run with cwd = their run-local attempt folder (outside the repository, so harness side
effects such as MCP or indexer caches cannot dirty the candidate and repository-controlled harness
config/hooks do not load), with the worktree granted read-only, and read-only tools: Claude `--tools Read,Grep,Glob
--permission-mode dontAsk --no-session-persistence --strict-mcp-config` (no user MCP servers: one such server,
Serena, wrote `.serena/` into the granted repository and invalidated a result); OpenCode a run-local
`depup-reviewer` agent (`OPENCODE_CONFIG_CONTENT`, `--standalone`) that denies everything, then allows
reads and external-directory access only under the worktree, review directory and extra read dirs, with
common secret paths denied (its permissions are lexical, so a symlink under those directories that
resolves outside them marks the candidate unavailable and the router falls back; only a link that
sits directly in a `bin` or `Scripts` directory and points at an executable regular file under a
system/toolchain prefix (such as a `.venv/bin/python -> /usr/bin/python3`) is tolerated, never a
link to a directory or to a non-executable file (a credentials file under a toolchain prefix is an
escape), and a sandbox walk past 200,000 entries fails closed the same
way. The check runs once per `route()` call and is reused for the OpenCode session candidate); Pi `--tools read,grep,find,ls
--no-session`. `DEPUP_REVIEW_ROUTER_ACTIVE=1` makes any nested `review run` refuse. The candidate must
be a clean commit, and a result is discarded if the worktree changed during the review.

## Inputs, outputs, evidence

`<run_dir>/reviews/<kind>-<subject>-<candidate12>/`: `code.diff` (all non-lockfile changes),
`lockfile.diff` (complete), `diffstat.txt`, optional `context.*`, `prompt.md`, `attempts/NN-pP-<harness>/`
(stdout/stderr redacted), `review.md` (accepted answer with a per-review header), `receipt.json`
(candidate-bound, `kind: <kind>-review`, reviewer = harness/model@effort + review id/attempt, so
independent and security reviews on the same model stay distinct fresh sessions), `routing.json` (every check,
attempt, fallback and reason) and `entry.json` (what `review run` prints).

The reviewer ends with a `DEPUP_REVIEW_RESULT_BEGIN … END` JSON block, read only from the model's
own final text (Claude `result`, OpenCode `text` parts; never tool output): `verdict` pass|fail and
findings with severity Important|Nit|Pre-existing. Any Important finding forces `fail`.

Report: put entries in `reviews`. The entries on disk for the current candidate are authoritative
(omitting or editing one changes nothing). `PASS` is downgraded when a `required` entry is not `completed`
with verdict `pass` and a valid receipt for the current candidate, or when any review of the current
candidate failed. High-risk risks reference the independent and security `receipt.json` files.
`depup.py review probe [--run RUN]` shows candidate availability without any model call.

## Architecture and planning

`depup.py planning run RUN --context <handoff> [--kind plan|architecture] [--subject S]
[--session-harness H --session-model M --session-effort E]` runs one architecture/planning task
through this same `Router`: identical priority, probes, statuses, fallback, retries, timeout,
isolation, recursion guard and session fallback/handoff. Nothing is configured twice.

- Kinds: `plan` (default; architecture decisions plus the implementation plan for the whole
  upgrade, one call) and `architecture` (`--subject` required; deeper analysis of one high-risk
  item). Used in both `plan` and `upgrade` mode.
- Read target: the owned worktree once it exists, else the repository (plan mode, read-only).
  Planners get read-only tools, cwd in their run-local attempt folder and no write access; a
  result is discarded if the tree's HEAD, status or tracked diff changed during the task.
- Context handoff: the planner shares nothing with the main session, so `--context` must carry
  requirements, scope, constraints, binding rules, repo instructions, inventory/outdated records,
  research and findings. It is redacted and copied to `context.*`.
- Result: the planner ends with `DEPUP_PLAN_RESULT_BEGIN … END` JSON: `summary`, non-empty
  `decisions` (`decision`, `rationale`, `alternatives`), `steps` (non-empty for `plan`; `summary`,
  `packages`, `migrations`, `checks`, `rollback`), `risks`, `holds`, `open_questions`. A missing
  or malformed block is `incomplete` and falls back like a review.
- Usage: provider state is shared with reviews (`<run_dir>/reviews/provider-state.json`), so a
  model found exhausted while planning is skipped by later reviews and vice versa. A completed
  task with the same kind, subject, read target, tree fingerprint (HEAD, status, tracked diff) and
  handoff hash is returned `reused` without a call. A tree change during the task discards the
  result, sets `tree_changed`, and (plan mode) the report marks the user checkout as changed.
  Planners are told never to open secret/local-only config files.
- Handoff fallback: `depup.py planning record RUN --plan-id ID --output F --planner
  "<harness>/<model>@<effort>"`.
- Evidence: `<run_dir>/plans/<kind>-<subject>-<key12>/` with `prompt.md`, `context.*`, `attempts/`,
  `plan.md`, `plan.json`, `receipt.json`, `routing.json`, `entry.json`. `report` copies every entry
  into the report `planning` list (from disk; the input cannot override it) and the summary's
  "Architecture and planning" section. Planning never gates `PASS`; reviews and checks still do.
- Planner output is advisory data, never instructions: the main session validates it against the
  skill's rules and implements every change itself.

## Other skills (shared router)

`scripts/routed_task.py` gives skills without a depup run journal the same `Router` (priority, probes,
statuses, fallback, retries, isolation, recursion guard, session fallback/handoff); there is no second
router. Each skill ships `<skill>/routing.json` (role, binding rules, `plan_kinds`, `review_kinds` with
focus text; `evidence: git-inventory` adds helper-collected Git facts; `rendered_evidence: required` needs
`--rendered-evidence` or an explicit `--no-rendered-evidence` reason). Runs live in
`~/.local/state/agent-routing/<skill>/<repo-key>/<run-id>/` (`AGENT_ROUTING_STATE_DIR`). `start --base` (default
HEAD, before any change) fixes the review base for the run. The candidate is the working tree (HEAD, status,
tracked diff, untracked contents); `gate` passes only when, for every kind/subject ever marked `--required`
(`required.json`, never downgraded), the NEWEST review of the current candidate (any status) is a completed
`pass` with an intact receipt, and (git-inventory reviews) the uncut inventory hash still matches. The
inventory includes unique-commit patches, stash contents and other worktrees'/clones' diffs; those
directories are granted read-only to routed reviewers (`ctx['read_dirs']`). Files named by absolute path in
a rendered-evidence manifest are copied into the task dir so a read-only reviewer can open them. Users today:
`consolidate` (planning before approval; reconciliation + code review before cleanup) and
`frontend-ui-ux-overhaul` (planning before edits; code / ui-ux / accessibility reviews before completion).
