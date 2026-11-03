# agent-skills

My AI-agent skills, kept in one place: development, automation, code quality and workflow management.

Four skills I use across Claude Code, Codex CLI, OpenCode and Pi. They live here so they survive a
reinstall, stay reviewable, and move to another machine in one copy. Each folder is a standard
Agent Skill: a `SKILL.md` plus the scripts, references and tests it needs. This is a personal
workflow repository, not a supported product.

## Skills

| Skill                                                         | What it does                                                                                                                               |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| [`consolidate`](skills/consolidate/)                          | Gets every unfinished branch, worktree, clone, stash and PR into `main`, verifies the result, then removes the rest.                        |
| [`dependency-upgrade`](skills/dependency-upgrade/)            | Upgrades a repository's dependencies end to end in an isolated worktree: research, plan, migrate, test, review, verify, deliver.            |
| [`frontend-ui-ux-overhaul`](skills/frontend-ui-ux-overhaul/)  | Audits and implements repository-wide frontend, responsive, accessibility, motion and design-system improvements.                            |
| [`sync-agent-instructions`](skills/sync-agent-instructions/)  | Rewrites a repository's `AGENTS.md` from past sessions, git history and the code, keeping `CLAUDE.md` as an `@AGENTS.md` import.             |

## Layout

```text
skills/
├── consolidate/               SKILL.md, routing.json
├── dependency-upgrade/        SKILL.md, scripts/, references/, tests/, admin/
├── frontend-ui-ux-overhaul/   SKILL.md, routing.json
└── sync-agent-instructions/   SKILL.md, scripts/collect_sessions.py
```

`consolidate` and `frontend-ui-ux-overhaul` delegate their planning and review tasks to
`dependency-upgrade/scripts/routed_task.py` and read their prompt profiles from their own
`routing.json`. Keep the four folders as siblings under one skills root; the shared router
resolves `../<skill>/routing.json` from its own location, so splitting or renaming them breaks
routing.

## Install

Each harness discovers skills from a global root: `~/.agents/skills` for Codex, OpenCode and Pi,
and `~/.claude/skills` for Claude Code. This repository mirrors that layout under `skills/`:

```sh
git clone https://github.com/beyourahi/agent-skills.git
cp -R agent-skills/skills/* ~/.agents/skills/
```

Claude Code reads only `~/.claude/skills`, so link each skill there once (skip any that already
exist):

```sh
cd ~/.claude/skills
for s in consolidate dependency-upgrade frontend-ui-ux-overhaul sync-agent-instructions; do
  ln -s ../../.agents/skills/$s $s
done
```

`dependency-upgrade` also ships an idempotent installer for that link, with a doctor check:

```sh
python3 ~/.agents/skills/dependency-upgrade/admin/depup-admin.py install
python3 ~/.agents/skills/dependency-upgrade/admin/depup-admin.py doctor
```

Requirements: git and Python 3.9+ (`sync-agent-instructions` needs Python 3 only).

## Usage

| Skill                      | Invoke                                                                      |
| -------------------------- | --------------------------------------------------------------------------- |
| `consolidate`              | `consolidate`                                                               |
| `dependency-upgrade`       | `/dependency-upgrade [upgrade\|plan\|verify\|resume\|rollback\|status] [scope]` |
| `frontend-ui-ux-overhaul`  | `Run frontend-ui-ux-overhaul.`                                              |
| `sync-agent-instructions`  | `sync AGENTS.md`                                                            |

`dependency-upgrade` runs only when named explicitly; a casual "update my dependencies" gets a
read-only plan instead. Details and harness notes are in its
[README](skills/dependency-upgrade/README.md).

## Tests

```sh
python3 -m unittest discover -s skills/dependency-upgrade/tests -t skills/dependency-upgrade
```

## License

MIT — see [LICENSE](./LICENSE).
