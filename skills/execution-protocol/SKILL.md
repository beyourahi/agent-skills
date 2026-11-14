---
name: execution-protocol
description: 'Execution protocol: 100% end-to-end completion, completion over presentation, Opus 5.5 conducts, plans and gates (`scripts/gate.sh`); DeepSeek V4.1 Flash High implements through `scripts/implement.sh`, Sonnet 5.5 only as its fallback; risk tiers, Opus-owned plans, head-SHA review gates, PR + CI delivery, cleanup. Always on (Rahi 2026-11-09): apply it to every development task in every session, whether or not the user invokes it.'
---

## Execution protocol (applies to the task it accompanies, before or after it)

**Precedence:** safety/system rules > repo AGENTS.md/CLAUDE.md, branch protection and CI > this protocol. If a step is impossible in this harness, say so and use the closest safe alternative; never fake execution or results.

**Goal: 100% end-to-end completion.** Finish the whole task: implementation, fixes, tests, verification, finalization. Keep its full scope and intent; never shrink, defer or leave parts pending. Resolve blockers and follow-up work yourself. Report a blocker only for a kind listed under "Valid reasons to end a turn" once every route is exhausted, then finish the rest.

**Completion over presentation (Rahi 2026-11-11, strict, MUST).** The user must never have to type "continue". Never stop to present progress while executable work remains.
- **Valid reasons to end a turn, only these:** every item in scope is done and verified; every remaining item is blocked by a genuinely external or physically impossible step (e.g. a passkey touch, a credential no agent can reach) or by a gate blocked as step 1 or 7 defines, recorded with its reason; or the user said stop.
- **Never a reason to stop:** a finished lane, PR or wave; a long turn or long context (checkpoint to `PROGRESS.md` and keep going; context compaction handles length); gate cost or time; "a good checkpoint"; wanting to report, summarize or confirm direction; uncertainty you can resolve yourself.
- **No interim reports.** A status update ending with "Next action: X" (or "▶ next") while X is executable is a violation: do X. "Next" belongs only in the final report at a valid stop.
- **Throughput:** run independent lanes in parallel up to the allowed concurrency; no extra rounds or evidence beyond the gates.
- **Before ending any turn (MUST):** list every task item with its status; if any is executable now, continue. The report may say "Incomplete" only when every open item has a recorded blocker of a kind listed above. If the user said stop, say so and mark each open item not done.

**Pre-authorized (Rahi 2026-11-13, MUST).** Rahi grants every approval, permission and choice this protocol would otherwise ask him for, in advance and for every task: never ask, never wait. A decision that would go to him goes to Opus 5.5 through `gate.sh` at the task's tier: as an open question in `facts.md` (`plan` gate; a Low task with one is planned as Medium), or through the `epoch` gate (step 7). Opus decides with its best judgment on Rahi's behalf, and its logged decision is the authorization.
- **Never approvable, by any agent or gate:** new spend, purchases, subscriptions, Extra Usage or plan upgrades; sending email or outbound messages under Rahi's identity; exposing secrets or credentials; disabling security, or bypassing or weakening a failed gate; destructive production-data operations without verified recovery; deleting or rewriting legal agreements or acceptance records; changing the model routing or the conductor's own model (only Rahi's own explicit instruction changes routing, as on 2026-11-14); a physically impossible step (passkey touch).
- A step that needs one is skipped, not asked about: record `blocked external` (or `blocked impossible`), name the boundary on `Routes tried:`, and finish all other work. "The user said stop" still ends work.

**MUST / NEVER below are hard gates.** A gate that did not pass is a stop for that step, never a judgment call. No agent may skip, reorder, weaken or self-approve a gate, and none may change its own model.

**Progress status (MUST).** The last line of `PROGRESS.md` is `STATUS: in-progress | done | blocked <external|impossible|gate|user-stop> — <reason>`; `blocked external` or `blocked impossible` also needs a `Routes tried:` line listing every route attempted.

## 1. Model routing (Rahi 2026-11-14, strict)

| Role | Model | Reached via |
|---|---|---|
| Conductor | Claude Opus 5.5: `claude-opus-5-5`, High | this Claude Code session |
| Plan author (every tier, Low included) | Opus 5.5, effort high | `scripts/gate.sh plan` |
| Gate reviewer | Opus 5.5: review medium (high for a High tier or trigger), security high, epoch medium (high for High) | `scripts/gate.sh` (fresh read-only session) |
| Implementer | DeepSeek V4.1 Flash 1017: `opencode-go/deepseek-v4.1-flash#high`, High only | `scripts/implement.sh` (OpenCode Go inside `scripts/implementer.sb`) |
| Implementer fallback | Sonnet 5.5: `claude-sonnet-5-5`, High only | only inside `scripts/implement.sh`, after DeepSeek was logged `unavailable` for that step and round |

- **No other model or route:** Sonnet 5.5 is never used for any other role or scenario (conductor, planner, gate, helper agent, lookup). No Haiku, Codex/GPT, Kimi, `opencode/` (Zen) routes, OpenCode Go free models, other DeepSeek variants, or the Sonnet helper agents in `~/.claude/agents`. No gate fallback.
- **Effort:** Opus only medium or high: gates per `gate.sh`; the conductor high, set by `modelSettings` `claude-opus-5-5` `effortLevel` in `~/.claude/settings.json`, changed only on Rahi's instruction. DeepSeek high only. Sonnet high only, never medium (Rahi 2026-11-14).
- **The conductor** does everything except plans, gates and implementation rounds: investigation, tier, `facts.md`, the bootstrap command, dispatching each step through `implement.sh` (in the background), reading each round's full diff before running anything from it, re-running lint, types, tests and build itself before any gate, every commit, conflict resolution, git, PR, CI, merge, cleanup, and escalation answers (`--notes` within the plan, or a re-plan through the plan gate). It writes code only after `implement.sh` exits 3, 4 or 6 (it implements the step itself or re-plans) and for outside-repo changes. Self-review is not a gate, so gates stay independent.
- **A gate session** never writes code, tests or fixes, runs no git mutations, never merges; it returns a plan, a decision or findings only.
- **Start check (MUST):** Opus 5.5 in Claude Code is the conductor: follow every step. A DeepSeek or Sonnet session is never the conductor: it implements only its brief and runs no gate.sh, premerge, git mutation or PR. Any other model stops and says so. Every gate goes through `scripts/gate.sh` (step 7).
- **Opus unavailable for a gate:** `gate.sh` logs `blocked` and exits 3. Record it in `PROGRESS.md`, finish all other work, never merge past it, and retry Opus at the next gate.
- **Claude accounts (Rahi 2026-11-10):** Opus gates may rotate between Rahi's Claude Pro accounts only through `gate.sh` (`cswap` mechanics: `reference.md`); the Sonnet fallback never rotates. Never add, remove or edit accounts or settings, never use API-key accounts, extra usage or new spend, never configure OpenCode's Anthropic provider with subscription OAuth; without `cswap`, skip rotation.
- **Opus unavailable** means every account out of quota (`cswap` exit 3), down, or a step 7 run still failing after one retry.

## 2. Risk tier (MUST, before planning; rules decide, not judgment)

The conductor sets the tier from the request and the code it will touch, writing the tier and every matched trigger to `PLAN.md` (step 3). When in doubt, pick the higher tier.

**Tier re-check (MUST, before any review gate and again before merge):** re-apply the same rules to the actual diff (`git diff --stat` plus changed paths and content), not the request. If the diff touches a High area, changes behavior in a task marked Low, or matches a trigger not in `PLAN.md`, raise the tier, record why in `PLAN.md`, and run every gate the new tier requires (a fresh `facts.md` Opus plan included, code realigned) before merging. A tier never goes down after planning. Write each re-check to `EVID/tier-recheck-<head-sha>.md` (full 40-character SHA): the `git diff --stat`, every changed path, each tier rule with its result, and the resulting tier; it ends `Tier: <low|medium|high>`.

- **High:** touches auth, sessions, permissions, tenancy, payments, billing, webhooks, money, inventory, migrations, stored data, secrets, CI/CD, deploy, infrastructure or legal text.
- **Medium:** ordinary product code that is not High.
- **Low:** docs, comments, copy, config typos, or a small isolated change with no behavior change.
- **Opus triggers** (raise a Medium task's Opus review effort to `high`): a new dependency; a public API, schema or contract change; a plan touching more than ~8 files or more than one package; review round 2 still disputed.

## 3. Plan (Opus, every tier)

Evidence folder per task: `~/.local/state/dev-evidence/<task>/` (`EVID` below), holding `PLAN.md` and `PROGRESS.md`.

**Opus owns every plan (Rahi 2026-11-14), Low included,** blind to any conductor plan.
1. The conductor writes `EVID/facts.md`: the request verbatim, repo rules that apply, relevant files with `file:line`, current behavior, existing patterns, constraints, open questions (including any decision the user would otherwise make). Facts only: no proposed solution, design or preference.
2. Opus gate `plan` via `gate.sh plan <low|medium|high>` reads the packet (`EVID/packet-plan.md`) plus the repo and returns acceptance criteria, design, file list (exact repo-relative paths; a directory ends in '/'), tests, risks, rollback.
3. The conductor saves it as `PLAN.md` and follows it exactly; `gate.sh` writes `EVID/plan-<sha256 of facts.md>.json`, which `implement.sh` turns into the brief. A needed deviation goes back to Opus before any code: append the reason and the current `PLAN.md` to `facts.md`, rerun the `plan` gate (a new plan restarts the round counts).
- Acceptance criteria are fixed before code; later changes go through the plan gate. No task code or commit exists before the plan gate passes (premerge checks commit dates for Medium/High).

## 4. Current docs and tools

Check the latest official docs for every command, config, API and version; don't trust memory. Use the installed MCP servers, tools and skills that genuinely help, per their docs; skip redundant ones.

## 5. Isolate

Never commit to main. One branch and worktree per workstream, cut from the latest default branch; one owner per file set. Run independent work in parallel, scaled to quota and machine; dependent work in order. Read-only research needs no worktree.

**Lanes.** Run at most 6 concurrent lanes. An independent workstream with a disjoint file set gets its own worktree and EVID (`<task>-<lane>`); its steps run through `implement.sh` in that worktree, conducted by the conductor. In-session subagents only for bounded read-only lookups; never two sessions on one file set.

**Changes outside a repository, and work with no code diff** (global or user settings, permissions and allow-rules, hooks, policy and instruction files, skills, MCP or harness config, shell profiles, credential config; and no-code tasks such as Lighthouse/PSI runs, production checks, evals, canaries, tool upgrades): the same tiers and gates apply, no PR.
- Tier them by the step 2 rules. Permissions, allow-rules, hooks, routing/policy, secrets and credential config are always **High**.
- A skill installed from a source repository (agent-skills: `~/.agents/skills/<name>` is a copy of its `skills/<name>/`) is changed in that repository through the normal PR flow; installing the merged version is the outside change, and the installed copy is never edited first.
- Never edit the live file first. Back it up to `EVID/backup/`, write the change to a copy, save `EVID/outside-<name>.diff` (backup → copy), and gate it as step 7 says (the usual packet, keyed by the diff's sha256 in place of base/head SHA).
- No-diff work is specified before it runs: `EVID/op-<name>.md` holds the exact commands, targets and pass criteria; gate it with `scripts/gate.sh <gate> <tier> --diff EVID/op-<name>.md`, check it with `scripts/premerge-and-merge.sh --diff EVID/op-<name>.md`, and run the work only after that script exits 0. Results go to `PROGRESS.md` and the report, never in that file: editing it changes its sha and needs a new gate. Tiers and Medium/High plans apply unchanged.
- Apply the copy only after the gate passes, then verify the tool still loads (its doctor/validate command); on a failed gate or broken load, restore the backup.
- List every outside change, its tier, gate file and verification in the step 10 report.

## 6. Implement through implement.sh, test-first, and verify locally

RED → GREEN → REFACTOR for behavior changes, plus a regression test for every bug fix. Match repo conventions; no unrelated changes. Run the repo's lint, types, tests and build, plus E2E/UI checks when affected; all MUST pass before any review gate. Never skip hooks, delete tests or loosen gates. For a bug-fix step the conductor confirms RED itself: in a scratch detached worktree at the step's base with only the step's test files applied, it runs the regression test and records `RED confirmed: <cmd> -> <failing line>` in `PROGRESS.md`; it must fail on the bug, not on setup, or the round is rejected (`implement.sh --discard`, rerun with `--notes`). The implementer's own RED claim never counts.

**Implementation (MUST).** Each step runs from the root of its clean, linked task worktree:

```sh
EVID=~/.local/state/dev-evidence/<task> "$SKILL/scripts/implement.sh" <low|medium|high> --bootstrap '<setup cmd>|none' [--step NAME] [--notes FILE] [--trigger]
```

The brief comes from the last logged plan; attempts log to `EVID/impl.log`. Exit 0 done: read the full diff (`git status`, `git diff HEAD`) before running anything from it, re-run the bootstrap and the repo's checks yourself, and commit each accepted round before the next. 1 failed round (reason in `impl.log`; change saved to `impl-<tag>-<route>-discarded.patch`, tree reset): next round with `--notes`, at most 3 per step and plan. 2 usage or precondition. 3 no implementer available: implement the step yourself and record route `conductor` in `PROGRESS.md`. 4 round cap: re-plan through the plan gate, or implement the step yourself. 5 escalate (`EVID/escalation-<tag>.md`): answer within the plan through `--notes`, or re-plan. **Exit 6** (confined processes survived): read, run, bootstrap or commit nothing from that worktree; from the main checkout run `git worktree remove --force <wt>`, never reuse the path, continue in a fresh worktree from the last accepted commit, and record the PIDs in `PROGRESS.md` and the report. After a killed run, `implement.sh --discard --bootstrap <cmd|none>` resets the worktree. Take the bootstrap from the repo's AGENTS.md/CLAUDE.md/README (or `none`) and record it in `PROGRESS.md`; resets wipe ignored files, so it must recreate them. Run nothing else in the worktree during a run: it tries to stop every process in it or sandboxed to it (exit 6 if one survives).

**Residual risks (accepted; see `reference.md`):** run no command-accepting local service during a round.

## 7. Review gates (MUST pass before merge)

| Tier | Gate(s) | Reviewer |
|---|---|---|
| Low | `review` | Opus, `--effort medium` |
| Medium | `review` | Opus, `--effort medium` (`high` if a trigger matched) |
| High | `review` and `security` (separate fresh sessions) | Opus, `--effort high` |

**Every gate runs through `scripts/gate.sh` (MUST),** including a program-level or multi-task plan (in its own EVID), re-reviews and the `epoch` decision. Never run `claude -p` yourself; a gate run any other way did not happen. From the task worktree:

```sh
SKILL=~/.agents/skills/execution-protocol
EVID=~/.local/state/dev-evidence/<task> "$SKILL/scripts/gate.sh" <plan|review|security|epoch> <low|medium|high> [--trigger] [--diff FILE] [--base REF]
```

It refuses a packet lacking the key, `Gate: <gate>` or the exact diff (`--diff FILE`, or `git diff` from the merge-base with `--base`, default `origin/HEAD`, to HEAD; a `--base` not of `origin/<branch>`; a text file shown only as "Binary files differ"). Exit 0 pass or a valid `plan`; 1 `fail`; 2 usage or precondition, no model; 3 blocked; 4 round cap, re-plan required; 5 lock timeout, rerun. Rounds: 3 per plan, 6 total per gate per epoch. The log records the merge-base and diff hash; the merge script accepts a gate only if they match the full PR diff. A diff touching agent config (`opencode.json`, `.opencode/`, `.claude/`, `.agents/`, `AGENTS.md`, `CLAUDE.md`) is never Low. Refusals after the key log `invalid`.

**Packet** (`EVID/packet-<gate>.md`): gate name, claimed tier and triggers, the original request verbatim, the acceptance criteria from `PLAN.md`, for review and security the logged plan's `risks` verbatim under `## Plan risks` (check each in addition to the whole diff), the full 40-character base and head SHA (or the diff's sha256), and the full diff. NEVER include the implementer's summary, rationale or self-assessment (no `impl-*`, `escalation-*` or session exports), and never secrets or credentials: `gate.sh` needs the exact diff, so a secret in it is removed from the branch (and rotated), never redacted.

Every reviewer, Low included, checks the claimed tier against the diff; an under-classified tier is `important` and re-enters at the correct tier.

**Rotation and pass conditions** are inside `gate.sh` (see `reference.md`); never resume a gate session. A `fail` verdict is a valid run, never a switch reason; a malformed command is fixed and rerun, never unavailability.

**Verdict rules:**
- A gate passes only with `verdict: "pass"` and zero `important` findings, bound to the exact head SHA in the file name and `head_sha` field (full 40-character SHA, every tier, every round; never `review-r2.json`-style names); outside-repo gates use the diff's 64-character sha256.
- `important` findings are fixed through `implement.sh --step fix-<gate>-r<n> --notes <findings>` (exit 3 or 4: by the conductor), re-verified (step 6), then re-reviewed by `gate.sh` in a fresh session with the full packet plus a section headed exactly `## Changes since the last round`: the last reviewed key (one this task's gate logged `ok`), the diff from it to now (repo: `git diff --no-color <last reviewed head> H`; outside: `diff -u` of the previous and current diff files, saved as `EVID/diff-<key>.diff`), and each earlier finding with its fix or written reason. Round 1 is always a full review; a packet without this section gets one. Later rounds allow a new `important` finding only for that section's code, behavior it alters, an unresolved fix, an under-classified tier, or a concrete exploitable security issue anywhere; other unchanged-code issues are `pre-existing`. A finding drops only if the reviewer accepts the reason.
- Any new commit after a pass invalidates it; re-review the delta for the new head.
- At most 3 rounds per gate. After round 3, rethink: a new plan from the plan author (step 3), then build and gate it; a Low task at the cap becomes Medium. Not a reason to end the turn. If that also fails 3 rounds, run the epoch rule below; a fail there records the gate blocked in `PROGRESS.md` and you finish other work.
- **Epoch rule (Rahi 2026-11-13).** A new plan epoch needs a passing `epoch` gate. After a `review` or `security` gate is blocked at the round cap, write `EVID/epoch.md`, facts only: the request verbatim, the blocked gate and key, its gates.log rows, every round's gate file, and `PLAN.md`; no proposed approach. Write `EVID/packet-epoch.md` (`Gate: epoch`, the tier, the sha256 of `epoch.md`, its full text) and run `gate.sh epoch <medium|high>`; the tier cannot be below the blocked gate's. Pass authorizes one new epoch in this EVID: round totals restart and a new Opus plan (step 3) comes before any review; `fail` is final, recorded as `blocked gate`. At most 2 authorized epochs per EVID, then `gate.sh` logs `blocked` and exits 3. Never open a new EVID to get around a block.
- **Legacy fallback rows (logged before 2026-11-13).** On High, a route `fallback` pass on `review` or `security` is provisional: it cannot merge until Opus passes that gate on the same key. That Opus run replaces it and is not a round.
- Rounds are counted per gate from EVID/gates.log across all sessions, and a blocker counts only after every route listed by the task or user has actually been tried and recorded under "Routes tried:" in PROGRESS.md.

## 8. Deliver through PRs (merge gate, MUST)

Commit, push and open one PR per workstream; the PR body states the tier, triggers and links each passing gate file. Merge only when all hold:

1. The tier re-check (step 2) ran on the final diff, and every gate the resulting tier requires has a passing file named for the current head SHA (`git rev-parse HEAD` equals it), or the merge script carries it from an earlier gated head with the exact same PR diff (never after a failed run on it). On High, a legacy `fallback` pass needs an Opus pass on this head.
2. CI is green on that same SHA.
3. Repo rules and branch protection are satisfied.
4. **`premerge-and-merge.sh` exits 0.** Run it from the task worktree; merging by any other means is NEVER allowed:

```sh
SKILL=~/.agents/skills/execution-protocol
EVID=~/.local/state/dev-evidence/<task> "$SKILL/scripts/premerge-and-merge.sh" <PR-number> [--no-ci]
```

It runs the checks listed in `reference.md`. It writes the output into the PR body and squash-merges with `--match-head-commit`; outside a repo, `--diff FILE` runs the same checks on the diff's sha256, no `gh`. Both `PLAN.md` and the re-check need a `Tier:` line. `--no-ci` only for a repo with no CI: it passes only when `gh pr checks` reports no checks and neither the worktree nor `origin/<base>` has `.github/workflows`. Exit 1 means no merge; never write or rename a gate file by hand.

**Update from main before the tier re-check and final gate.** If main moves after the final gate, the script carries an earlier gate only when the PR diff is byte-identical (`CARRY <gate> <old> -> <new>`, logged); otherwise re-gate the new head.

Combined work: integrate onto the latest main, retest and re-gate the combined diff before merging; if the repo auto-deploys, check production health.

## 9. Clean up

Remove only branches, worktrees and processes you created and verified as merged. Never touch unrelated or unmerged work. If lasting lessons came up, run `sync-agent-instructions`; run `consolidate` only for repo-wide reconciliation of stray work.

## 10. Report in a few lines

What's done, tier and triggers, evidence (commands and results, PR and CI links), each gate's verdict, the implementer route for each step (DeepSeek, Sonnet fallback or conductor), Opus session id and usage, any blocked gate or epoch decision, blockers. Confirm nothing in scope is left undone; never call unverified work done. Write it only at a valid stop.
