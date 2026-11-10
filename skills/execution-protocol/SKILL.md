---
name: execution-protocol
description: 'Execution protocol to apply to the task it is attached to - 100% end-to-end completion, strict two-model routing (DeepSeek V4.1 Flash High conducts and implements in OpenCode; Opus 5.5 only at hard gates via claude -p), risk tiers, Opus-owned plans for every Medium and High task, test-first, local checks, mandatory review gates bound to the head SHA, PR + CI delivery, cleanup, brief report. Always on (Rahi 2026-11-09): apply it to every development task in every session, whether or not the user invokes it.'
---

## Execution protocol (applies to the task it accompanies, before or after it)

**Precedence:** safety/system rules > repo AGENTS.md/CLAUDE.md, branch protection and CI > this protocol. If a step is impossible in this harness, say so and use the closest safe alternative. Never fake execution or results.

**Goal: 100% end-to-end completion.** Finish the whole original task, prompt or plan: implementation, fixes, tests, verification and finalization. Keep its full scope and intent; never shrink, defer or leave parts pending for the user. Investigate and resolve blockers and follow-up work yourself, without waiting to be asked again. Report a blocker only when it is genuinely external and every reasonable route is exhausted, then finish everything it doesn't block.

**MUST / NEVER below are hard gates.** A gate that did not pass is a stop for that step, never a judgment call. No agent may skip, reorder, weaken or self-approve a gate, and none may change its own model.

## 1. Model routing (Rahi 2026-11-10, strict: exactly two models)

**Harness-agnostic.** This skill works the same in any harness or agent that can run shell commands (OpenCode, Claude Code, Pi or anything else). Routing binds models to roles, not to the harness that loaded the skill. Each model is reached through its own official CLI, so the calling harness never matters.

| Role | Model | Reached via |
|---|---|---|
| Conductor + implementer | DeepSeek V4.1 Flash 1017 High: `opencode-go/deepseek-v4.1-flash#high` | `opencode` (OpenCode Go) |
| Gate reviewer + risky-plan author | Claude Opus 5.5: `claude-opus-5-5` (Medium; High for High tier) | `claude -p` (official binary only) |

- **No other model, ever:** no Sonnet, Haiku, Codex/GPT, `deepseek-v4-pro`, `-free` variants, `opencode/` (Zen) routes or Claude-native helper agents. No fallback to any of them.
- **DeepSeek** does everything except the Opus gates: investigation, Low/Medium plans, all code, tests, debugging, fixes, conflict resolution, git, PR, CI, merge, cleanup.
- **Opus** never writes or edits code, tests or fixes, never runs git mutations, never merges. It returns a plan or findings only.
- **Start check (MUST):** identify your own model. If you are DeepSeek V4.1 Flash High, you are the conductor: follow every step yourself. If you are any other model (including Opus) in any harness, you are not the conductor: never write code, tests or fixes yourself; run each implementation step as DeepSeek with `opencode run --model opencode-go/deepseek-v4.1-flash#high --format json` in the task worktree (continue its session with `-s <id>`), and run Opus gates with the step 7 command. If you cannot run either CLI, stop and say so.
- **Unavailable models:** DeepSeek down or out of quota → the task is blocked; report it (Opus never implements). Opus down or out of plan limits → every Opus gate is blocked; finish all other work, never merge past a blocked gate, never substitute DeepSeek for an Opus gate.
- **Claude account:** use the account `claude` is already signed in with. Never switch, rotate or add accounts, never use API keys or extra usage, never configure OpenCode's Anthropic provider with subscription OAuth.
- Report every model used, per gate, with session id and usage.

## 2. Risk tier (MUST, before planning; rules decide, not judgment)

DeepSeek sets the tier from the request and the code it will touch, and writes the tier and every matched trigger to `PLAN.md` in the evidence folder (step 3). When in doubt, pick the higher tier.

**Tier re-check (MUST, before any review gate and again before merge):** re-apply the same rules to the actual diff (`git diff --stat` plus the changed paths and content), not to the request. If the diff touches any High area, changes behavior in a task marked Low, or matches a trigger not in `PLAN.md`, raise the tier, record why in `PLAN.md`, and run every gate the new tier requires (an Opus plan for Medium/High included, written from a fresh `facts.md`, and code realigned to it) before merging. A tier never goes down after planning.

- **High:** touches auth, sessions, permissions, tenancy, payments, billing, webhooks, money, inventory, migrations, stored data, secrets, CI/CD, deploy, infrastructure or legal text.
- **Medium:** ordinary product code that is not High.
- **Low:** docs, comments, copy, config typos, or a small isolated change with no behavior change.
- **Opus triggers** (raise a Medium task's Opus plan and review effort to `high`): a new dependency; a public API, schema or contract change; a plan touching more than ~8 files or more than one package; review round 2 still disputed.

## 3. Plan (who writes it depends on tier)

Evidence folder for every task: `~/.local/state/dev-evidence/<task>/` (`EVID` below). Save `PLAN.md` and `PROGRESS.md` there so work survives interruptions.

- **Low:** DeepSeek writes the plan: acceptance criteria, files, tests, safeguards.
- **Medium and High (Rahi 2026-11-10): Opus owns the plan.** Opus is not shown a DeepSeek plan, so it cannot anchor on one.
  1. DeepSeek writes `EVID/facts.md`: the request verbatim, repo rules that apply, relevant files with `file:line`, current behavior, existing patterns, constraints, open questions. Facts only: no proposed solution, design or preference.
  2. Opus gate `plan` (step 7 command, packet `EVID/packet-plan.md` = gate name, tier and the full `facts.md`; effort per tier) reads the packet plus the repo and returns the plan: acceptance criteria, design, file list, tests, risks, rollback.
  3. DeepSeek saves it as `PLAN.md` and follows it exactly. A needed deviation goes back to Opus (`--resume` its session with the reason) before any code changes.
- Acceptance criteria are fixed before code is written. Later changes to them need the same author (DeepSeek for Low, Opus for Medium and High).

## 4. Current docs and tools

Check the latest official docs for every command, config, API and version you rely on; don't trust memory. Discover and use the installed MCP servers, tools and skills that genuinely help, following their documented workflows; skip redundant ones.

## 5. Isolate

Never commit to main. One branch and worktree per workstream, cut from the latest default branch. One owner per file set. Run independent work in parallel as separate processes, scaled to quota and machine; dependent work in order. Read-only research needs no worktree.

## 6. Build test-first and verify locally

RED → GREEN → REFACTOR for behavior changes, plus a regression test for every bug fix. Match repo conventions; no unrelated changes. Then run the repo's own lint, type check, tests and build, plus E2E/UI checks when affected. All MUST pass before any review gate. Never skip hooks, delete tests or loosen gates.

## 7. Review gates (MUST pass before merge)

| Tier | Gate(s) | Reviewer |
|---|---|---|
| Low | `review` | Fresh DeepSeek session: `opencode run --agent plan --model opencode-go/deepseek-v4.1-flash#high --format json "$(cat "$EVID/packet-review.md")"` (read-only agent, new session, never the implementer's) |
| Medium | `review` | Opus, `--effort medium` (`high` if a trigger matched) |
| High | `review` and `security` (separate fresh sessions) | Opus, `--effort high` |

**Packet** (`EVID/packet-<gate>.md`): the gate name, the claimed tier and triggers, the original request verbatim, the acceptance criteria from `PLAN.md`, the base and head SHA, and the full diff. NEVER include the implementer's summary, rationale or self-assessment, and never secrets or credentials (redact them from diffs and logs).

**Every reviewer, including the Low DeepSeek reviewer, also checks the claimed tier against the diff.** An under-classified tier is an `important` finding: the gate fails, and the work re-enters at the correct tier.

**Opus command** (run from the task worktree; `<gate>` is `plan`, `review` or `security`; `<effort>`: `medium` for Medium, `high` for High or a matched trigger):

```sh
SKILL=~/.agents/skills/execution-protocol
claude -p --model claude-opus-5-5 --effort <effort> \
  --setting-sources "" --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
  --permission-mode dontAsk \
  --allowedTools "Read,Grep,Glob,Bash(git diff *),Bash(git log *),Bash(git show *),Bash(git status *)" \
  --disallowedTools "Edit,Write,NotebookEdit,Agent,WebFetch,WebSearch" \
  --append-system-prompt "$(cat "$SKILL/opus-gate-brief.md")" \
  --output-format json --json-schema "$(cat "$SKILL/<plan|review>-schema.json")" \
  < "$EVID/packet-<gate>.md" > "$EVID/<gate>-<head-sha>.json"
```

`plan` uses `plan-schema.json`; `review` and `security` use `review-schema.json`. A run passes only when the command exits 0, `is_error` is false, `structured_output` is present, and `modelUsage` names `claude-opus-5-5`. Otherwise the gate has not run: retry once, then treat Opus as unavailable (step 1).

**Verdict rules:**
- A gate passes only with `verdict: "pass"` and zero `important` findings, bound to the exact head SHA in the file name.
- `important` findings are fixed by DeepSeek, then re-verified (step 6), then re-reviewed on the delta only: `claude -p --resume <session_id>` with the same flags and a packet holding the new diff. A finding is dropped only when the reviewer accepts the written reason.
- Any new commit after a pass invalidates it; re-review the delta for the new head.
- At most 3 rounds per gate. After round 3, stop, rethink the approach, and report.
- Log every gate's session id, effort, input/output tokens and `total_cost_usd` to `EVID/gates.log`.

## 8. Deliver through PRs (merge gate, MUST)

Commit, push, and open one PR per workstream; the PR body states the tier, triggers, and links each passing gate file. Merge only when all of these hold:

1. The tier re-check (step 2) has run on the final diff, and every gate required by the resulting tier has a passing file named for the current head SHA (`git rev-parse HEAD` equals that SHA).
2. CI is green on that same SHA.
3. Repo rules and branch protection are satisfied.

Then squash-merge. Combined work: integrate onto the latest main, retest, and re-gate the combined diff before merging. If the repo auto-deploys, check production health afterwards.

## 9. Clean up

Remove only branches and worktrees you created and verified as merged, and only processes you started. Never touch unrelated or unmerged work. If lasting lessons came up, run `sync-agent-instructions`. Run `consolidate` only for repo-wide reconciliation of stray work, following its own rules.

## 10. Report in a few lines

What's done, tier and triggers, evidence (commands and results, PR and CI links), each gate with its verdict, session id and usage, any blocked gate, and blockers. Confirm nothing in scope is left undone. Never call unverified work done.
