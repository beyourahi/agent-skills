# dependency-upgrade (global Agent Skill)

Runs a complete, safe dependency upgrade from one invocation (analyze, research, plan,
upgrade, migrate, test, review, fix, verify, deliver) in any repository, from any supported
coding harness. One canonical copy:
`~/.agents/skills/dependency-upgrade/`.

## Invoke

| Harness | How it finds the skill | Explicit invocation |
| --- | --- | --- |
| Claude Code | `~/.claude/skills/dependency-upgrade` → relative symlink to the canonical dir | `/dependency-upgrade` |
| Codex CLI | native `~/.agents/skills` | `$dependency-upgrade` (or `/skills`) |
| OpenCode | native `~/.agents/skills` (also reads `~/.claude/skills`; same target, loaded once) | ask: "use the dependency-upgrade skill" |
| Pi | native `~/.agents/skills` | `/skill:dependency-upgrade` |

Modes: `upgrade` (default: the full pipeline, no further input), `plan` (read-only), `verify`,
`resume`, `rollback`, `status`. Add a scope, e.g. `/dependency-upgrade react family only`.
Scope profiles: `security-only`, `compatible`, or `all-stable` (default). Prereleases require
an explicit scope. Delivery follows the repository's delivery mode (`direct-PR`: push the owned
branch, PR, merge when green and authorized; otherwise `local-only`: a verified local branch).
The `rollback` mode cleans only empty owned state; undo committed upgrade batches with verified
compensating `git revert` commits, never by deleting or resetting their branch.

Implicit loading is off where the harness supports it (`disable-model-invocation: true`
for Claude Code and Pi, `agents/openai.yaml` `allow_implicit_invocation: false` for Codex).
OpenCode has no per-skill flag; the skill itself runs read-only `plan` when it
was loaded without the user naming it. Claude Code pre-approves the package-manager, Git and
test commands the pipeline needs via `allowed-tools`, so `dontAsk` sessions do not deny them.

## Layout

```
SKILL.md                entry workflow (progressive disclosure)
agents/openai.yaml      Codex explicit-only policy
references/             workflow, safety, javascript, ecosystems, parallelism,
                        verification, report-schema
scripts/depup.py        deterministic helper (stdlib, Python 3.9+), JSON output
scripts/review_router.py one-model-per-task routing (reviews, architecture, planning) with usage-aware fallback
scripts/routed_task.py  the same router for other skills (consolidate, frontend-ui-ux-overhaul):
                        run dirs, skill profiles (<skill>/routing.json), review gate
admin/depup-admin.py    installer maintenance: doctor / install / update / uninstall
tests/                  unit, integration and offline end-to-end tests
```

Run state (journals, owned worktrees, logs, reports) lives outside repositories in
`~/.local/state/dependency-upgrade/<repo-key>/<run-id>/` (override `DEPUP_STATE_DIR`;
`XDG_STATE_HOME` and Windows `%LOCALAPPDATA%` are honored).

## Reviews, architecture and planning

Every review task runs on one model in a fresh read-only session, by priority with usage-aware
fallback: Claude Opus 5.5 medium (Claude Code) → DeepSeek V4.1 Flash
1017 high (OpenCode Go) → the invoking session's model and effort. Same order from every harness;
no substitution, no global setting changes. `depup.py review probe` shows availability without
model calls. Architecture and planning (`depup.py planning run`) use the same router, priority
and fallback in fresh read-only sessions and return advisory plans; implementation stays in the
main session. Details: `references/review-routing.md`.

## Maintenance

```sh
python3 ~/.agents/skills/dependency-upgrade/admin/depup-admin.py doctor [--run-tests]
python3 ~/.agents/skills/dependency-upgrade/admin/depup-admin.py install   # idempotent
python3 ~/.agents/skills/dependency-upgrade/admin/depup-admin.py update    # install + doctor
python3 ~/.agents/skills/dependency-upgrade/admin/depup-admin.py uninstall [--remove-canonical]
python3 -m unittest discover -s ~/.agents/skills/dependency-upgrade/tests -t ~/.agents/skills/dependency-upgrade
```

- Edit the canonical files directly; there is no second copy to sync. `update`
  re-validates the skill, repairs the Claude link if it is missing, and refreshes the
  machine's `universal-skills` catalogue (`reconcile` + `check`).
- `install` records only what it creates in
  `~/.local/state/dependency-upgrade/install-manifest.json`. It refuses (exit 3) if a
  different `dependency-upgrade` already occupies the Claude path.
- `uninstall` removes only manifest-owned links that still point where the installer
  left them; pre-existing or user-changed links stay. `--remove-canonical` moves the skill
  folder to the Trash (reversible).
- No shell profile, harness config, policy file or global toolchain is modified.

## Adding a harness (adapter contract)

1. Find the harness's documented global skill roots. If it reads `~/.agents/skills` (or
   `~/.claude/skills`), no adapter is needed.
2. Otherwise add one per-skill relative symlink in its root (never link a whole skills
   directory), record it in the install manifest, and add the root to
   `~/.agents/integrations.json` `skill_roots` so `doctor` and `agent-parity` see it.
3. Onboarding test (all must hold before claiming support):
   - `doctor` shows `<harness> discovery (static)` PASS and no conflicting definition.
   - In a disposable fixture repo with a local bare `origin`, an explicit invocation with no
     mode runs the whole pipeline: owned worktree from `origin`'s default branch, migrated and
     verified commit, delivery per the fixture's stated mode, a report in the state dir, and
     `git status` in the fixture unchanged.
   - Invoke without naming the skill ("update my dependencies"): the harness must not
     start an upgrade.

## Safety summary

Never edits the user's checkout; upgrades happen in an owned worktree/branch based on the
fetched upstream default branch. Push/PR/merge only for the owned branch and only per the
delivery mode; no force operations, bypass merges or branch deletion with unmerged work. High-risk migrations (data, auth,
payments, secrets, deploy config) require documented impact research, alternatives, controls,
recovery, independent review and a separate security review. The skill does not ask for approval;
it proceeds when evidence and controls support the safest option, and blocks only an unsafe or
physically human-only step. `PASS` requires recorded checks and complete high-risk evidence; the
report helper downgrades unsupported claims.
