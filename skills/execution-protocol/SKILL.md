---
name: execution-protocol
description: 'Execution protocol to apply to the task it is attached to - 100% end-to-end completion, strict two-model routing (DeepSeek V4.1 Flash High conducts and implements in OpenCode; Opus 5.5 only at hard gates via claude -p; if Opus is unavailable, DeepSeek V4.1 Flash Max runs the gate in a fresh session), completion over presentation (never stop while executable work remains), risk tiers, Opus-owned plans for every Medium and High task, test-first, local checks, mandatory review gates bound to the head SHA, PR + CI delivery, cleanup, brief report. Always on (Rahi 2026-11-09): apply it to every development task in every session, whether or not the user invokes it.'
---

## Execution protocol (applies to the task it accompanies, before or after it)

**Precedence:** safety/system rules > repo AGENTS.md/CLAUDE.md, branch protection and CI > this protocol. If a step is impossible in this harness, say so and use the closest safe alternative. Never fake execution or results.

**Goal: 100% end-to-end completion.** Finish the whole original task, prompt or plan: implementation, fixes, tests, verification and finalization. Keep its full scope and intent; never shrink, defer or leave parts pending for the user. Investigate and resolve blockers and follow-up work yourself, without waiting to be asked again. Report a blocker only when it is a kind listed under "Valid reasons to end a turn" (genuinely external, physically impossible, or a gate blocked as step 1 or step 7 defines) and every reasonable route is exhausted, then finish everything it doesn't block.

**Completion over presentation (Rahi 2026-11-11, strict, MUST).** The user must never have to type "continue". Never stop to present progress while executable work remains.
- **Valid reasons to end a turn, only these:** every item in scope is done and verified; every remaining item is blocked by a genuinely external or physically impossible step (e.g. a passkey touch, a credential no agent can reach) or by a gate blocked as step 1 or step 7 defines, recorded with its reason; or the user told you to stop.
- **Never a reason to stop:** a finished lane, PR or wave; a long turn or long context (checkpoint to `PROGRESS.md` and keep going; context compaction handles length); gate cost or time; "a good checkpoint"; wanting to report, summarize or confirm direction; uncertainty you can resolve yourself.
- **No interim reports.** A status update that ends with "Next action: X" (or "▶ next") while X is executable is a violation. Do X instead. A "next" line belongs only in the final report at a valid stop.
- **Throughput:** run independent lanes in parallel up to the allowed concurrency instead of one at a time; don't spend capacity on extra review rounds or evidence beyond what the gates require.
- **Before ending any turn (MUST):** list every item of the original task against its status. If any item is executable now, do not end the turn; continue with it. The final report may say "Incomplete" only when every open item has a recorded blocker of a kind listed under "Valid reasons to end a turn" (external, physically impossible, or a gate blocked as step 1 or step 7 defines). If the user told you to stop, say so instead and list each open item as not done.

**MUST / NEVER below are hard gates.** A gate that did not pass is a stop for that step, never a judgment call. No agent may skip, reorder, weaken or self-approve a gate, and none may change its own model.

**Progress status (MUST).** The last line of `PROGRESS.md` is always `STATUS: in-progress | done | blocked <external|impossible|gate|user-stop> — <reason>`. A `blocked external` or `blocked impossible` line also needs a `Routes tried:` line listing every route actually attempted.

### Outer loop

The user or a parent agent — never the conductor for its own session — can restart the conductor until it reports a valid stop. Save the snippet to a file and run it with `bash`; its `exit` lines would close an interactive shell:

```bash
SID=${SID:?set SID to the conductor session id}; EVID=${EVID:?set EVID}
last=$(shasum "$EVID/PROGRESS.md" 2>/dev/null | awk '{print $1}'); last=${last:-none}; same=0
for i in $(seq 1 30); do
  opencode run -s "$SID" --model 'opencode-go/deepseek-v4.1-flash#high' --format json \
    "Continue this task under execution-protocol; read $EVID/PROGRESS.md and keep going." >/dev/null
  st=$(grep -E '^STATUS:' "$EVID/PROGRESS.md" | tail -1)
  case "$st" in *"STATUS: done"*) echo "stop: done"; exit 0;; esac
  case "$st" in *"STATUS: blocked gate"*|*"STATUS: blocked user-stop"*) echo "stop: $st"; exit 0;; esac
  case "$st" in *"STATUS: blocked external"*|*"STATUS: blocked impossible"*)
    grep -qE '^Routes tried: *[^ ]' "$EVID/PROGRESS.md" && { echo "stop: $st"; exit 0; };; esac
  h=$(shasum "$EVID/PROGRESS.md" | awk '{print $1}')
  if [ "$h" = "$last" ]; then same=$((same + 1)); else same=0; last=$h; fi
  if [ "$same" -ge 3 ]; then echo "stop: PROGRESS.md unchanged for 3 runs"; exit 0; fi
done
echo "stop: 30 runs"; exit 0
```

## 1. Model routing (Rahi 2026-11-10, strict: exactly two models)

**Harness-agnostic.** This skill works the same in any harness or agent that can run shell commands (OpenCode, Claude Code, Pi or anything else). Routing binds models to roles, not to the harness that loaded the skill. Each model is reached through its own official CLI, so the calling harness never matters.

| Role | Model | Reached via |
|---|---|---|
| Conductor + implementer | DeepSeek V4.1 Flash 1017 High: `opencode-go/deepseek-v4.1-flash#high` | `opencode` (OpenCode Go) |
| Gate reviewer + risky-plan author | Claude Opus 5.5: `claude-opus-5-5` (Medium; High for High tier) | `claude -p` (official binary only) |
| Opus fallback (gates only) | DeepSeek V4.1 Flash 1017 Max: `opencode-go/deepseek-v4.1-flash#max` | `opencode` (OpenCode Go), fresh session |

- **No other model, ever:** no Sonnet, Haiku, Codex/GPT, `deepseek-v4-pro`, `-free` variants, `opencode/` (Zen) routes or Claude-native helper agents. No fallback to any of them. The Opus fallback below is the only fallback.
- **DeepSeek** does everything except the Opus gates: investigation, Low/Medium plans, all code, tests, debugging, fixes, conflict resolution, git, PR, CI, merge, cleanup.
- **Opus** never writes or edits code, tests or fixes, never runs git mutations, never merges. It returns a plan or findings only.
- **Start check (MUST):** identify your own model. If you are DeepSeek V4.1 Flash High, you are the conductor: follow every step yourself. If you are any other model (including Opus) in any harness, you are not the conductor: never write code, tests or fixes yourself; run each implementation step as DeepSeek with `opencode run --model opencode-go/deepseek-v4.1-flash#high --format json` in the task worktree (continue its session with `-s <id>`), and run Opus gates with the step 7 command. If you cannot run either CLI, stop and say so.
- **Unavailable models:** DeepSeek down or out of quota → the task is blocked; report it (Opus never implements).
- **Opus fallback (Rahi 2026-11-11, strict, MUST):** when Opus 5.5 (Medium or High) is unavailable for any gate (`plan`, `review` or `security`) after the step 7 rotation and retry, run that same gate on DeepSeek V4.1 Flash Max instead. Never skip or downgrade the gate.
  1. Always a **new OpenCode session** with fresh context: never `-s`/`--continue`, never the conductor's or implementer's session, never a previous gate's session. It runs with the same isolation as the Opus gate: a private server and the skill's `gate` agent (`opencode-gate.json`: read, grep and glob only; no shell, edits, web, MCP or skills). The prompt goes in as an attached file, so a large diff never hits the argument limit. From the task worktree:
     ```sh
     SKILL=~/.agents/skills/execution-protocol
     { cat "$SKILL/opus-gate-brief.md"; echo; cat "$EVID/packet-<gate>.md"; echo
       echo 'Return one final JSON object matching this schema:'; cat "$SKILL/<plan|review>-schema.json"
     } > "$EVID/fallback-prompt-<gate>.md"
     OPENCODE_CONFIG_CONTENT="$(cat "$SKILL/opencode-gate.json")" \
       opencode run --standalone --agent gate --model 'opencode-go/deepseek-v4.1-flash#max' --format json \
       -f "$EVID/fallback-prompt-<gate>.md" "Run the gate in the attached file." > "$EVID/<gate>-<head-sha>.raw.jsonl"
     ```
  2. Same packet, same rules, same output: save the final JSON object alone, unedited, to `EVID/<gate>-<head-sha>.json`; keep the `.raw.jsonl` beside it as proof. High still needs `review` and `security` as two separate fresh sessions. Verdict rules, rounds and the merge check are unchanged; a delta re-review after a fix is another fresh session with the full packet plus the new diff. `gate.sh` in step 7 runs this fallback automatically when Opus is unavailable; the commands here are its reference.
  3. **Return to the original session.** The fallback session only returns the plan or findings; the conductor then continues the task in its own original session (DeepSeek conductor: `-s <original session id>`), saving the result as step 3 or step 7 says. No work continues in the fallback session.
  4. Log `fallback: deepseek-v4.1-flash#max`, the reason Opus was unavailable, and the session id in `EVID/gates.log`, and name it in the report. Retry Opus first on each new gate; use the fallback again only if Opus is still unavailable.
  5. If the fallback also fails (error, no valid JSON after one retry), the gate is blocked: record it in `PROGRESS.md`, finish all other work and never merge past it. A gate blocked here, or after the re-planned approach in step 7, counts as a recorded blocker for ending the turn.
- **Claude accounts (Rahi 2026-11-10):** Opus gates may rotate between Rahi's Claude Pro accounts with the installed claude-swap (`cswap`), only as step 7 says; it uses Rahi's own `cswap` settings (thresholds, priorities, disabled accounts). Never add, remove or edit accounts or `cswap` settings, never use API-key accounts (`--include-api-key-accounts`), extra usage or new spend, and never configure OpenCode's Anthropic provider with subscription OAuth. If `cswap` is missing, skip rotation and use the signed-in account.
- **Opus out of quota** means every account is exhausted (`cswap` exit 3, or the retry below also hits a limit). **Opus unavailable** means out of quota, down, or a run that still fails the step 7 pass conditions after one retry.
- Report every model used, per gate, with session id and usage.

## 2. Risk tier (MUST, before planning; rules decide, not judgment)

DeepSeek sets the tier from the request and the code it will touch, and writes the tier and every matched trigger to `PLAN.md` in the evidence folder (step 3). When in doubt, pick the higher tier.

**Tier re-check (MUST, before any review gate and again before merge):** re-apply the same rules to the actual diff (`git diff --stat` plus the changed paths and content), not to the request. If the diff touches any High area, changes behavior in a task marked Low, or matches a trigger not in `PLAN.md`, raise the tier, record why in `PLAN.md`, and run every gate the new tier requires (an Opus plan for Medium/High included, written from a fresh `facts.md`, and code realigned to it) before merging. A tier never goes down after planning. Write each re-check to `EVID/tier-recheck-<head-sha>.md` (full 40-character SHA): the `git diff --stat`, every changed path, each tier rule checked with its result, and the resulting tier. The file ends with a line `Tier: <low|medium|high>`.

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
  3. DeepSeek saves it as `PLAN.md` and follows it exactly; the plan gate runs through `gate.sh`, which writes `EVID/plan-<sha256 of facts.md>.json`. A needed deviation goes back to Opus before any code changes: append the reason and the current `PLAN.md` to `facts.md` and run the `plan` gate again (a new plan restarts the round counts).
- Acceptance criteria are fixed before code is written. Later changes to them need the same author (DeepSeek for Low, Opus for Medium and High).

## 4. Current docs and tools

Check the latest official docs for every command, config, API and version you rely on; don't trust memory. Discover and use the installed MCP servers, tools and skills that genuinely help, following their documented workflows; skip redundant ones.

## 5. Isolate

Never commit to main. One branch and worktree per workstream, cut from the latest default branch. One owner per file set. Run independent work in parallel as separate processes, scaled to quota and machine; dependent work in order. Read-only research needs no worktree.

**Lanes.** Each independent workstream with a disjoint file set gets its own worktree, its own EVID (`<task>-<lane>`) and its own fresh session, started in that worktree with `opencode run --model 'opencode-go/deepseek-v4.1-flash#high' --format json --title <lane> "<lane prompt>"`; save the first event's `sessionID` in the lane EVID and continue it with `-s`. Lanes prepare only: investigate, `facts.md`, code, tests and local commits. The supervising session runs `gate.sh`, push, PR, `premerge-and-merge.sh`, and all network, production and email work. Start small, max 6 concurrent. A lane whose session grows too long gets a new fresh session told to read its `PROGRESS.md`, `PLAN.md` and git log. In-session subagents only for bounded read-only lookups. Never two sessions on one file set.

**Changes outside a repository** (global or user settings, permissions and allow-rules, hooks, policy and instruction files, skills, MCP or harness config, shell profiles, credential config): the same tiers and gates apply; there is just no PR.
- Tier them by the step 2 rules. Permissions, allow-rules, hooks, routing/policy, secrets and credential config are always **High**.
- Never edit the live file first. Back it up to `EVID/backup/`, write the change to a copy, save `EVID/outside-<name>.diff` (backup → copy), and gate that diff exactly as step 7 says (the usual packet, with the diff's sha256 in place of base/head SHA; the file name uses the diff's `shasum -a 256` in place of a head SHA).
- Apply the copy only after the gate passes, then verify the tool still loads it (e.g. its own doctor/validate command). On a failed gate or a broken load, restore the backup.
- List every outside change, its tier, gate file and verification in the step 10 report.

## 6. Build test-first and verify locally

RED → GREEN → REFACTOR for behavior changes, plus a regression test for every bug fix. Match repo conventions; no unrelated changes. Then run the repo's own lint, type check, tests and build, plus E2E/UI checks when affected. All MUST pass before any review gate. Never skip hooks, delete tests or loosen gates.

## 7. Review gates (MUST pass before merge)

| Tier | Gate(s) | Reviewer |
|---|---|---|
| Low | `review` | Fresh DeepSeek session: `opencode run --agent plan --model opencode-go/deepseek-v4.1-flash#high --format json "$(cat "$EVID/packet-review.md")"` (read-only agent, new session, never the implementer's). The packet ends by asking for one final JSON object matching `review-schema.json`; save that object alone to `EVID/review-<head-sha>.json`. |
| Medium | `review` | Opus, `--effort medium` (`high` if a trigger matched) |
| High | `review` and `security` (separate fresh sessions) | Opus, `--effort high` |

**Run every gate with `scripts/gate.sh`, never by hand.** From the task worktree:

```sh
SKILL=~/.agents/skills/execution-protocol
EVID=~/.local/state/dev-evidence/<task> "$SKILL/scripts/gate.sh" <plan|review|security> <low|medium|high> [--trigger] [--diff FILE] [--base REF]
```

It computes the key, refuses a packet that does not contain the key, `Gate: <gate>` and the exact diff (`--diff FILE`, or `git diff` from the merge-base with `--base`, default `origin/HEAD`, to HEAD), enforces the round cap from `gates.log`, serializes Opus calls with the account lock and cswap rotation, retries once on a usage limit or an invalid run, falls back to DeepSeek Max when Opus is unavailable, writes `EVID/<gate>-<key>.json` and appends one JSON line to `EVID/gates.log`. Exit 0: pass, or a valid `plan`. Exit 1: valid run with verdict `fail`. Exit 2: usage or precondition error, no model called. Exit 3: gate blocked (the fallback failed too). Exit 4: round cap reached, re-plan required. Exit 5: the Opus lock wait timed out; rerun. `--base` must be an `origin/<branch>` ref; the log records the merge-base and diff hash, and the merge script accepts a gate only if they match the full PR diff. Rounds: at most 3 per plan and 6 in total per gate, then the gate is blocked. A diff that touches agent config (`opencode.json`, `.opencode/`, `.claude/`, `.agents/`, `AGENTS.md` or `CLAUDE.md`) is never Low, and the DeepSeek fallback refuses it. Refusals after the key is known are logged as `invalid`. A text file that the diff shows only as "Binary files differ" (a `.gitattributes` `-diff` or `binary` entry) is refused (exit 2); drop that attribute first. The command block below is the reference for what the script runs.

**Packet** (`EVID/packet-<gate>.md`): the gate name, the claimed tier and triggers, the original request verbatim, the acceptance criteria from `PLAN.md`, the full 40-character base and head SHA (or the diff's sha256 for an outside-repo change), and the full diff. NEVER include the implementer's summary, rationale or self-assessment, and never secrets or credentials: `gate.sh` needs the exact diff, so a secret in the diff is removed from the branch (and rotated), never redacted in the packet.

**Every reviewer, including the Low DeepSeek reviewer, also checks the claimed tier against the diff.** An under-classified tier is an `important` finding: the gate fails, and the work re-enters at the correct tier.

**Opus command** (run from the task worktree; `<gate>` is `plan`, `review` or `security`; `<effort>`: `medium` for Medium, `high` for High or a matched trigger):

```sh
SKILL=~/.agents/skills/execution-protocol
claude -p --model claude-opus-5-5 --effort <effort> \
  --setting-sources "" --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
  --permission-mode dontAsk \
  --allowedTools "Read,Grep,Glob" \
  --disallowedTools "Bash,Edit,Write,NotebookEdit,Agent,WebFetch,WebSearch" \
  --append-system-prompt "$(cat "$SKILL/opus-gate-brief.md")" \
  --output-format json --json-schema "$(cat "$SKILL/<plan|review>-schema.json")" \
  < "$EVID/packet-<gate>.md" > "$EVID/<gate>-<head-sha>.json"
```

**Account rotation (around every Opus call):**
1. Before the call: `cswap auto --once --model Opus --json` (skip if `cswap` is absent). Exit 0 = switched, 2 = no change: continue. Exit 1 = error: retry once after ~30 s, then continue on the current account. Exit 3 = all accounts exhausted: Opus is unavailable; use the step 1 fallback.
2. If the call fails on a usage limit (`is_error` true and the result mentions a usage or rate limit): run `cswap switch --strategy next-available --json`, then rerun the same call once. A second limit failure, or no available account, makes Opus unavailable; use the step 1 fallback. Never loop.
3. Run Opus calls one at a time while rotating, so two gates never switch accounts at once (`gate.sh` holds a machine-wide lock). Every gate run is a fresh session with the full packet; never `--resume`.
4. Log the account number (never the email) used for each call in `EVID/gates.log`.

`plan` uses `plan-schema.json`; `review` and `security` use `review-schema.json`. A run passes only when the command exits 0, `is_error` is false, `structured_output` is present, and `modelUsage` names `claude-opus-5-5`. Otherwise the gate has not run: retry once, then treat Opus as unavailable and use the step 1 fallback. A fallback run passes only when it ran the exact step 1 command (isolated `gate` agent, `deepseek-v4.1-flash#max`), exits 0 with no `error` event, and returned one valid JSON object matching the schema. Opus is unavailable only for the reasons in step 1: a `fail` verdict is a valid run, never a reason to switch, and a malformed command is fixed and rerun, never counted as unavailability.

**Verdict rules:**
- A gate passes only with `verdict: "pass"` and zero `important` findings, bound to the exact head SHA in the file name and in its `head_sha` field (full 40-character commit SHA, every tier, every round; never round-numbered names like `review-r2.json`). Outside-repo gates use the diff's 64-character sha256 instead.
- `important` findings are fixed by DeepSeek, then re-verified (step 6), then re-reviewed by `gate.sh` in a fresh session (never `--resume`) with the full packet plus a section headed exactly `## Changes since the last round`. It holds: the last reviewed key (one a gate of this task logged `ok`); the diff from that state to now (repo: `git diff --no-color <last reviewed head> H`; outside: `diff -u` of the previous and current diff files, each saved as `EVID/diff-<key>.diff`); and each earlier finding with its fix or written reason. Round 1 is always a full review, and a packet without this section gets one. In later rounds a new `important` finding is allowed only for code in that section, unchanged code whose behavior it alters, a fix that does not resolve its finding, an under-classified tier, or a concrete exploitable security issue anywhere in the diff; other issues in unchanged code are `pre-existing`. A finding is dropped only when the reviewer accepts the written reason.
- Any new commit after a pass invalidates it; re-review the delta for the new head.
- At most 3 rounds per gate. After round 3, stop that loop and rethink the approach: a new plan from the plan author (step 3), then build and gate it. A Low task that reaches the cap is raised to Medium, so Opus writes the new plan. This is not a reason to end the turn. If the new approach also fails 3 rounds, record that gate as blocked in `PROGRESS.md` and finish all other work.
- After a gate is blocked at the round cap, only the user can authorize another plan epoch. Record it as the first line of the new EVID's gates.log: `{"gate": "note", "status": "authorized", "reason": "<the user's decision>; parent log <path>"}`. A new EVID for the same task without that line is never allowed.
- Rounds are counted per gate from EVID/gates.log across all sessions (a fresh conductor, lane or reviewer session never resets them), and a blocker counts only after every route listed by the task or the user has actually been tried and recorded under "Routes tried:" in PROGRESS.md.
- Log every gate's session id, effort, input/output tokens and `total_cost_usd` to `EVID/gates.log`.

## 8. Deliver through PRs (merge gate, MUST)

Commit, push, and open one PR per workstream; the PR body states the tier, triggers, and links each passing gate file. Merge only when all of these hold:

1. The tier re-check (step 2) has run on the final diff, and every gate required by the resulting tier has a passing file named for the current head SHA (`git rev-parse HEAD` equals that SHA), or the merge script carries it from an earlier gated head with the exact same PR diff (never when any run on that diff failed).
2. CI is green on that same SHA.
3. Repo rules and branch protection are satisfied.
4. **`premerge-and-merge.sh` exits 0.** Run it from the task worktree; merging by any other means is NEVER allowed:

```sh
SKILL=~/.agents/skills/execution-protocol
EVID=~/.local/state/dev-evidence/<task> "$SKILL/scripts/premerge-and-merge.sh" <PR-number> [--no-ci]
```

It checks the clean tree, the tier re-check (final tier is the highest `Tier:` line across the re-check and `PLAN.md`, and a lower re-check fails), a logged Opus plan for Medium/High, each required gate file bound to the current head and to a logged ok round with an adequate route and tier, the round counts and blocked entries; then it writes the check output into the PR body and squash-merges with `--match-head-commit`. Outside a repo, `--diff FILE` runs the same checks on the diff's sha256 and calls no `gh`. Both `PLAN.md` and the re-check must carry a `Tier:` line. Add `--no-ci` only for a repo with no CI at all: it passes the CI step only when `gh pr checks` reports no checks, the worktree has no `.github/workflows` and `origin/<base>` has none either; otherwise checks must pass. It is recorded in the PR body. Exit 1 means no merge; never write or rename a gate file by hand.

**Update the branch from main before the tier re-check and the final gate.** If main moves after the final gate, the script carries an earlier gate only when the PR diff is byte-identical (`CARRY <gate> <old> -> <new>`, logged); otherwise re-gate the new head.

Combined work: integrate onto the latest main, retest, and re-gate the combined diff before merging. If the repo auto-deploys, check production health afterwards.

## 9. Clean up

Remove only branches and worktrees you created and verified as merged, and only processes you started. Never touch unrelated or unmerged work. If lasting lessons came up, run `sync-agent-instructions`. Run `consolidate` only for repo-wide reconciliation of stray work, following its own rules.

## 10. Report in a few lines

What's done, tier and triggers, evidence (commands and results, PR and CI links), each gate with its verdict, model (Opus or the DeepSeek Max fallback, with the reason), session id and usage, any blocked gate, and blockers. Confirm nothing in scope is left undone. Never call unverified work done. Write this report only when a valid reason to end the turn exists (see "Completion over presentation").
