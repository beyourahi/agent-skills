# agent-skills — notes for agents

Repository of personal AI-agent skills. Each directory under `skills/` is an installable Agent
Skill; this repository is the source of truth, and `~/.agents/skills/<name>` is a copy of it.

## Commands

```sh
python3 -m unittest discover -s skills/dependency-upgrade/tests -t skills/dependency-upgrade  # dependency-upgrade tests
bash skills/execution-protocol/tests/smoke.sh  # execution-protocol smoke tests (fake CLIs)
diff -rq -x __pycache__ -x graft skills/<name> ~/.agents/skills/<name>  # drift check: must print nothing
rsync -a --exclude '__pycache__/' --exclude 'graft/.cache/' skills/<name>/ ~/.agents/skills/<name>/  # sync one skill
```

Run the drift check from an up-to-date `main` before starting a change to a skill: it must print
nothing (any difference is local drift; bring it into the repository first). After the change is
merged, sync from the merged `main` with the rsync, then run the drift check again: it must print
nothing. A file deleted in the repository is removed from the installed copy by hand before that
final check.

## Layout

```text
skills/consolidate/               SKILL.md, routing.json (plan/review prompt profiles)
skills/dependency-upgrade/        SKILL.md, README.md, scripts/, references/, tests/, admin/, agents/
skills/execution-protocol/        SKILL.md, reference.md, opus-gate-brief.md, implementer-brief.md, plan-schema.json,
                                  review-schema.json, agents/, opencode/, scripts/ (gate.sh, implement.sh,
                                  implement_lib.py, implementer.sb, premerge-and-merge.sh), tests/ (smoke.sh, implement-smoke.sh)
skills/frontend-ui-ux-overhaul/   SKILL.md, routing.json
skills/sync-agent-instructions/   SKILL.md, scripts/collect_sessions.py
```

## Invariants

- `skills/dependency-upgrade/scripts/routed_task.py` resolves sibling skills as
  `<parent>/<skill>/routing.json`. `consolidate` and `frontend-ui-ux-overhaul` depend on that, so
  the three routing folders (`dependency-upgrade`, `consolidate`, `frontend-ui-ux-overhaul`) stay
  siblings and keep their names. No other skill takes part in routing.
- `SKILL.md` frontmatter keeps `name` and `description`; `dependency-upgrade` also declares
  `allowed-tools`, `license` and `disable-model-invocation`.
- Python helpers are stdlib-only and must run on 3.9+.
- Scripts keep their executable bit (`scripts/depup.py`, `scripts/collect_sessions.py`,
  execution-protocol `scripts/gate.sh`, `scripts/premerge-and-merge.sh`, `scripts/implement.sh`,
  `tests/smoke.sh` and `tests/implement-smoke.sh`).

## Always

- Run the test suite after touching `dependency-upgrade` scripts, tests or any `routing.json`.
- Run `bash skills/execution-protocol/tests/smoke.sh` after touching `execution-protocol` scripts or
  tests.
- Keep documentation accurate to the code: file names, commands and flags.

## Never

- Commit `__pycache__/`, `graft/.cache/`, run state, transcripts, secrets or home-directory paths.
- Rename or move a skill folder, or link whole skills directories into a harness root.
- Add a dependency, a package manifest, or a build step.
