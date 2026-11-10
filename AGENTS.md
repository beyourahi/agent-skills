# agent-skills — notes for agents

Repository of personal AI-agent skills. Each directory under `skills/` is an installable Agent
Skill; this repository is the source of truth, and `~/.agents/skills/<name>` is a copy of it.

## Commands

```sh
python3 -m unittest discover -s skills/dependency-upgrade/tests -t skills/dependency-upgrade  # all tests
rsync -a --exclude '__pycache__/' --exclude 'graft/.cache/' skills/<name>/ ~/.agents/skills/<name>/  # sync one skill
```

## Layout

```text
skills/consolidate/               SKILL.md, routing.json (plan/review prompt profiles)
skills/dependency-upgrade/        SKILL.md, README.md, scripts/, references/, tests/, admin/, agents/
skills/execution-protocol/        SKILL.md, opus-gate-brief.md, plan-schema.json, review-schema.json, agents/
skills/frontend-ui-ux-overhaul/   SKILL.md, routing.json
skills/sync-agent-instructions/   SKILL.md, scripts/collect_sessions.py
```

## Invariants

- `skills/dependency-upgrade/scripts/routed_task.py` resolves sibling skills as
  `<parent>/<skill>/routing.json`. `consolidate` and `frontend-ui-ux-overhaul` depend on that, so
  the four folders stay siblings and keep their names.
- `SKILL.md` frontmatter keeps `name` and `description`; `dependency-upgrade` also declares
  `allowed-tools`, `license` and `disable-model-invocation`.
- Python helpers are stdlib-only and must run on 3.9+.
- Scripts keep their executable bit (`scripts/depup.py`, `scripts/collect_sessions.py`).

## Always

- Run the test suite after touching `dependency-upgrade` scripts, tests or any `routing.json`.
- Keep documentation accurate to the code: file names, commands and flags.

## Never

- Commit `__pycache__/`, `graft/.cache/`, run state, transcripts, secrets or home-directory paths.
- Rename or move a skill folder, or link whole skills directories into a harness root.
- Add a dependency, a package manifest, or a build step.
