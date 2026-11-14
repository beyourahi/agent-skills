# Contributing

These are personal workflow skills, published so they are versioned, reviewable and portable.
Bug reports and fixes are welcome; there is no roadmap or feature-request process.

## Before you change anything

Read the skill you are touching. `SKILL.md` is the workflow, and for `dependency-upgrade` the
`references/` files carry the detail. Keep the change inside the skill it belongs to.

## Invariants

- **Folder names are the skill names.** `consolidate` and `frontend-ui-ux-overhaul` resolve
  `../dependency-upgrade/scripts/routed_task.py` and their own `routing.json` from their location.
  Renaming or splitting `skills/dependency-upgrade` breaks both.
- **Skills stay self-contained.** A folder must work when copied alone into a global skills root,
  except for the shared routing scripts above.
- **Frontmatter stays valid.** Every `SKILL.md` keeps `name` and `description`; the description
  states what the skill does and when to use it.
- **No machine-specific paths.** Document the canonical install root (`~/.agents/skills/<skill>`,
  `~/.claude/skills/<skill>`); never commit a home directory, transcript, or run state.
- **No new dependencies** without a clear need. The helpers are stdlib-only Python.

## Checks

```sh
python3 -m unittest discover -s skills/dependency-upgrade/tests -t skills/dependency-upgrade
```

Run it after any change to `dependency-upgrade` scripts, tests or `routing.json`, and after editing
`consolidate` or `frontend-ui-ux-overhaul` (their profiles are validated by the routed-task tests).
Also re-read the file you edited: commands, paths and script names must match the repository.

## Installing a change locally

```sh
diff -rq -x __pycache__ -x graft skills/<name> ~/.agents/skills/<name>
rsync -a --exclude '__pycache__/' --exclude 'graft/.cache/' skills/<name>/ ~/.agents/skills/<name>/
diff -rq -x __pycache__ -x graft skills/<name> ~/.agents/skills/<name>
```

Run this from a checkout of the merged `main`. The first drift check must print nothing: any
difference is drift and goes into the repo first. After the rsync, the second drift check must print
nothing as well. A file deleted in the repo is removed from the installed copy by hand, then the
drift check is rerun.

Edit here first, then sync; do not edit only the installed copy.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/): `fix:`, `feat:`, `docs:`, `test:`,
`chore:`. Imperative subject, one logical change per commit, no AI attribution trailers.

## Secrets and private data

Never commit API keys, tokens, `.env` files, session transcripts, personal paths or run state. New
tests use fixtures and temporary directories only, and never call a real model, registry or remote.

## License

The repository and the skills it contains are released under the [MIT License](./LICENSE).
