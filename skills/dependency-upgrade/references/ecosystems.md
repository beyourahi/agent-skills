# Other ecosystems

Support levels used by `depup.py inventory`:
- **full**: inventory, automated outdated query, documented apply/verify path, covered by
  the skill's fixture tests (JS/TS).
- **adapter**: inventory + automated read-only outdated query + documented commands;
  parser/command fixtures are hermetic tests, and native-tool integration is claimed only when it
  actually ran with that toolchain.
- **advisory**: detected and inventoried; the agent follows the official tool docs below.
  No automated query; report what could not be checked.

Always confirm the tool exists and its version. Use the repository's wrapper (`./gradlew`,
`./mvnw`) or a per-invocation runner where one exists; a missing toolchain is a skipped check
and a held item, never a global install.

## Rust / Cargo (adapter)
- Query: `cargo update --dry-run --verbose` (compatible lock updates + "Unchanged …
  (available: X)" for semver-incompatible ones). `cargo tree -i <crate>` for who uses it.
- Apply: `cargo update -p <crate>` (within range); edit `Cargo.toml` (or
  `[workspace.dependencies]`) for majors, then `cargo update -p <crate>`.
- Respect `rust-version` (MSRV), `rust-toolchain.toml`, edition, `[patch]` sections.
- Verify without rewriting the lockfile: `cargo fmt --all --check`,
  `cargo clippy --workspace --all-targets --locked -- -D warnings` (if the repo uses it),
  `cargo test --workspace --locked`, and `cargo build --workspace --locked` where relevant.
  `--locked` fails if dependency resolution would change `Cargo.lock`. Use `cargo audit`/
  `cargo deny check` if installed. [Cargo update reference](https://doc.rust-lang.org/cargo/commands/cargo-update.html)

## Go modules (adapter)
- Query: `go list -m -u -json all` (fills the module cache outside the repo).
- Apply: `go get <module>@<version>` then `go mod tidy`. A major ≥ 2 changes the import
  path (`/v2`): rewrite imports, it is a code migration.
- Respect `go`/`toolchain` directives and `replace`. Verify with
  `GOFLAGS=-mod=readonly GOTOOLCHAIN=local go build ./...`, `go vet ./...`, and `go test ./...`;
  if the repo intentionally uses vendoring, use its vendor mode and verify `vendor/modules.txt`
  instead. Compare `go.mod`/`go.sum` before and after every check. A v2+ module path usually needs
  the `/vN` suffix; inspect the target's declared module path and migration guide before changing
  imports (`+incompatible` legacy modules are an exception). Use `govulncheck ./...` if installed.
  [Go major-version guidance](https://go.dev/doc/modules/major-version) ·
  [Go module reference](https://go.dev/ref/mod)

## Python (adapter for uv; advisory for others)
- uv: query `uv tree --outdated --frozen --depth 1`; apply `uv lock --upgrade-package
  <pkg>` or edit the constraint and `uv lock`; sync `uv sync --frozen`.
- Poetry: `poetry show --outdated`, `poetry update <pkg>`, `poetry add <pkg>@^X`.
- pip/requirements: inside the project's venv only, `pip list --outdated`; edit pins;
  pip-tools `pip-compile --upgrade-package <pkg>` if used.
- Respect `requires-python`, `.python-version`. Verify lock consistency with `uv sync --locked`;
  `uv sync --frozen` is only for intentionally testing strictly from a possibly stale lockfile.
  Run the repo's pytest/mypy/ruff and `pip-audit` if installed.
  [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)

## Java (advisory)
- Maven: `mvn versions:display-dependency-updates` / `display-plugin-updates`; edit
  `pom.xml`/BOM versions. Gradle: version catalogs (`gradle/libs.versions.toml`), the
  `dependencyUpdates` plugin if the repo has it, `./gradlew dependencies`. Use the
  wrapper (`./mvnw`, `./gradlew`), never a global install. Respect toolchain/JDK targets.

## Ruby / Bundler (advisory)
- `bundle outdated`, `bundle update <gem> --conservative`; respect `.ruby-version`.
  Verify with the repo's rspec/minitest, `bundle audit` if installed.

## PHP / Composer (advisory)
- `composer outdated --direct --format=json`, `composer update <pkg> --with-dependencies`,
  `composer audit`. Respect `config.platform.php`.

## .NET / NuGet (advisory)
- `dotnet list package --outdated --format json`, `dotnet add package <id> --version X`;
  central management in `Directory.Packages.props`; `dotnet list package --vulnerable`.
  Respect `global.json` SDK pin and target frameworks.

## Swift / SwiftPM (advisory)
- `swift package show-dependencies`, edit `Package.swift` requirements, `swift package
  update`; `Package.resolved` is the lockfile. On a Mac with only Command Line Tools,
  SwiftPM builds may fail to link: report as blocked, don't claim a pass.

## Others (advisory)
Deno (`deno outdated`), Dart/Flutter (`dart pub outdated`), Elixir (`mix hex.outdated`),
CocoaPods (`pod outdated`). Follow the official docs and report the support level as
advisory in the report.
