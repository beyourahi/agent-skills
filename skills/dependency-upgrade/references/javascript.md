# JavaScript / TypeScript (npm, pnpm, yarn, bun)

Check the installed tool version (`<pm> --version`) and the repo pin (`packageManager`,
`engines`, `.nvmrc`/`.node-version`/`.tool-versions`, `volta`) before using any command
below; flags differ between majors. Use the live `--help` when unsure.

## Detect
- `packageManager` field wins; otherwise the lockfile: `package-lock.json`/
  `npm-shrinkwrap.json` → npm, `pnpm-lock.yaml` → pnpm, `yarn.lock` → yarn
  (`.yarnrc.yml` → Berry ≥ 2, else Classic 1), `bun.lock`/`bun.lockb` → bun.
- Workspaces: `workspaces` in package.json (npm/yarn/bun), `pnpm-workspace.yaml` (pnpm,
  may define `catalog:`/`catalogs:`). Keep versions aligned across workspaces unless the
  repo intentionally differs.
- Pinned manager missing or at the wrong version: run it per invocation through Corepack
  (`corepack yarn@<pinned> install --immutable`, `corepack pnpm@<pinned> ...`) without
  `corepack enable` or any global install. No Corepack and no matching binary → hold that
  project and report it. `npx`/`bunx` one-offs of codemods are fine inside the worktree.

## Read-only queries
| Need | npm | pnpm | yarn (Berry) | bun |
| --- | --- | --- | --- | --- |
| outdated | `npm outdated --json --long [-ws]` | `pnpm outdated --format json [-r]` | `yarn upgrade-interactive` (interactive; use registry view) | `bun outdated` |
| why | `npm explain <pkg>` | `pnpm why <pkg>` | `yarn why <pkg>` | `bun pm why <pkg>` (if available) |
| audit | `npm audit --json` | `pnpm audit --json` | `yarn npm audit --json` | `bun audit` |
| metadata | `npm view <pkg>@<v> peerDependencies engines deprecated dist-tags time --json` (registry; works for every manager) |

`depup.py outdated` uses npm/pnpm natively and a registry fallback for yarn/bun.

## Apply versions (inside the owned worktree only)
- npm: `npm install <pkg>@<version>` (`-D` for dev; `-w <ws>` per workspace). Keeps
  range style from `save-prefix`.
- pnpm: `pnpm add <pkg>@<version>` (`-D`, `--filter <ws>`), or edit the catalog entry
  for `catalog:` deps, then `pnpm install`.
- yarn Berry: `yarn up <pkg>@<version>`; Classic: `yarn upgrade <pkg>@<version>`.
- bun: `bun add <pkg>@<version>` (`-d` for dev); `bun update <pkg>` within range.
- Coupled families move in one command so peer ranges resolve together.
- Overrides/resolutions/`pnpm.overrides`: after the upgrade, remove each override that
  is no longer needed; keep one only with a written reason.
- Patches (`patchedDependencies`, patch-package): re-check every patch applies to the
  new version; port or drop it explicitly.

## Frozen installs (lockfile consistency)
- npm `npm ci`; pnpm `pnpm install --frozen-lockfile`; yarn Berry `yarn install
  --immutable`; Classic `yarn install --frozen-lockfile`; bun `bun install
  --frozen-lockfile`.
- After the upgrade, a clean frozen install from the committed lockfile must succeed.
  Review the lockfile diff: unexpected new packages, registry URL changes, removed
  integrity fields, git/tarball sources.

## Peer and engine conflicts
Treat `ERESOLVE`, unmet peer warnings and engine mismatches as real. Resolve by choosing
compatible versions, upgrading the family together, or holding the item back. Never
`--force`, `--legacy-peer-deps`, `strict-peer-dependencies=false`, or a blanket override to
silence them; if the repo already uses one, report it as a pre-existing risk.

## Codemods and migrations
- Prefer the official codemod named in the migration guide (for example
  `npx @next/codemod`, `npx @angular/cli update`, `npx sv migrate`, `npx
  @vue/compat`-style guides, `npx typescript-eslint` flat-config tooling). Run it in the
  worktree, review the diff, then fix the rest by compiler/test errors.
- Typecheck errors are the migration worklist; don't silence them with `any`, `// @ts-ignore`
  or loosened `tsconfig` unless the upstream guide says so.
- ESM/CJS changes (packages going ESM-only): check `type`, import style, bundler and test
  runner config; don't add interop hacks without a reason.

## Verification commands
Use the repo's scripts (`<pm> run build|check|typecheck|lint|test`). Typical extras:
`<pm> run build` output size (if printed), `npm audit signatures` (npm ≥ 9), framework
checks (`svelte-check`, `next build`, `tsc --noEmit`). Web apps: SSR build, preview
server smoke with a browser tool if available (otherwise list as skipped), console
errors, hydration warnings, critical routes.
