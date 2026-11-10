# agent-skills

My AI-agent skills, kept in one place: development, automation, code quality and workflow management.

Five skills I use across Claude Code, Codex CLI, OpenCode and Pi. They live here so they survive a
reinstall, stay reviewable, and move to another machine in one copy. Each folder is a standard
Agent Skill: a `SKILL.md` plus the scripts, references and tests it needs. This is a personal
workflow repository, not a supported product.

## Skills

| Skill                                                        | What it does                                                                                                                                                                                         |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`consolidate`](skills/consolidate/)                         | Gets every unfinished branch, worktree, clone, stash and PR into `main`, verifies the result, then removes the rest once you approve.                                                                |
| [`dependency-upgrade`](skills/dependency-upgrade/)           | Upgrades a repository's dependencies end to end in an isolated worktree: research, plan, migrate, test, review, verify, deliver.                                                                     |
| [`execution-protocol`](skills/execution-protocol/)           | Applies an always-on protocol to every development task: strict two-model routing (DeepSeek conducts and implements, Opus 5.5 authors every Medium and High plan and reviews via `claude -p`), merge bound to the head SHA. |
| [`frontend-ui-ux-overhaul`](skills/frontend-ui-ux-overhaul/) | Audits and implements repository-wide frontend, responsive, accessibility, motion and design-system improvements.                                                                                    |
| [`sync-agent-instructions`](skills/sync-agent-instructions/) | Rewrites a repository's `AGENTS.md` from past sessions, git history and the code, keeping `CLAUDE.md` as an `@AGENTS.md` import.                                                                     |

## Layout

```text
skills/
├── consolidate/               SKILL.md, routing.json
├── dependency-upgrade/        SKILL.md, README.md, scripts/, references/, tests/, admin/, agents/
├── execution-protocol/        SKILL.md, opus-gate-brief.md, plan-schema.json, review-schema.json, agents/
├── frontend-ui-ux-overhaul/   SKILL.md, routing.json
└── sync-agent-instructions/   SKILL.md, scripts/collect_sessions.py
```

`consolidate` and `frontend-ui-ux-overhaul` delegate their planning and review tasks to
`dependency-upgrade/scripts/routed_task.py` and read their prompt profiles from their own
`routing.json`. The router resolves each profile as `<skills-root>/<skill>/routing.json`, so keep
those three routing folders (`dependency-upgrade`, `consolidate`, `frontend-ui-ux-overhaul`) as
siblings under one skills root; splitting or renaming them breaks routing. The other skills do not
take part in routing.

## Install

Each harness discovers skills from a global root: `~/.agents/skills` for Codex, OpenCode and Pi,
and `~/.claude/skills` for Claude Code. This repository mirrors that layout under `skills/`:

```sh
git clone https://github.com/beyourahi/agent-skills.git
cp -R agent-skills/skills/* ~/.agents/skills/
```

Claude Code reads only `~/.claude/skills`, so link each skill there once:

```sh
cd ~/.claude/skills
for s in consolidate dependency-upgrade execution-protocol frontend-ui-ux-overhaul sync-agent-instructions; do
  [ -e "$s" ] || ln -s ../../.agents/skills/$s $s
done
```

`dependency-upgrade` also ships an idempotent installer for that link, with a doctor check:

```sh
python3 ~/.agents/skills/dependency-upgrade/admin/depup-admin.py install
python3 ~/.agents/skills/dependency-upgrade/admin/depup-admin.py doctor
```

Requirements: git and Python 3.9+ (`sync-agent-instructions` needs Python 3 only).
`execution-protocol` also needs the `opencode` CLI (OpenCode Go, model
`opencode-go/deepseek-v4.1-flash#high`) and the official `claude` CLI, signed in.

## Usage

| Skill                     | Invoke                                                                          |
| ------------------------- | ------------------------------------------------------------------------------- |
| `consolidate`             | `consolidate`                                                                   |
| `dependency-upgrade`      | `/dependency-upgrade [upgrade\|plan\|verify\|resume\|rollback\|status] [scope]` |
| `execution-protocol`      | Always on (part of every development task)                                      |
| `frontend-ui-ux-overhaul` | `Run frontend-ui-ux-overhaul.`                                                  |
| `sync-agent-instructions` | `sync AGENTS.md`                                                                |

`dependency-upgrade` runs only when named explicitly; a casual "update my dependencies" gets a
read-only plan instead. Details and harness notes are in its
[README](skills/dependency-upgrade/README.md).

## Tests

```sh
python3 -m unittest discover -s skills/dependency-upgrade/tests -t skills/dependency-upgrade
```

## License

MIT — see [LICENSE](./LICENSE).
