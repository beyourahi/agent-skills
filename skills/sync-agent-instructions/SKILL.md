---
name: sync-agent-instructions
description: Update the current repo's AGENTS.md (and CLAUDE.md) from past agent sessions on this machine, git history, and the actual codebase, keeping CLAUDE.md as an @AGENTS.md import. Use when asked to update, refresh, or sync AGENTS.md or CLAUDE.md.
license: MIT
metadata:
  version: "2.0"
---

# Sync Agent Instructions

**Execution mode: fully autonomous.** Do not ask for approval, show diffs for confirmation, or pause between steps. Edit files directly and finish in one pass. Every change is in git, so the user reviews afterwards.

## Model

- `AGENTS.md` at the repo root is the single source of truth, read natively by most agents.
- `CLAUDE.md` is a thin bridge: `@AGENTS.md` on the first line, optionally followed by a `## Claude Code` section for instructions only that tool needs. Nothing shared is duplicated there.
- Repo root = `git rev-parse --show-toplevel` (else the current directory). Nested `AGENTS.md` files in subdirectories are in scope too; each covers its own subtree.

## Step 1: Gather evidence (cheaply)

1. Read `AGENTS.md`, `CLAUDE.md`, and any nested `AGENTS.md`.
2. Run the bundled collector instead of opening raw transcripts:
   ```
   python3 <skill-dir>/scripts/collect_sessions.py --repo <root> --days 60
   ```
   It scans local session stores (Claude Code, Codex CLI, Gemini CLI), keeps only sessions run inside this repo, redacts secrets, and prints user messages and failed tool calls, with corrections marked ⚑. Use `--extra-dir` for other agents' history folders and `--days` to widen or narrow the window. If `python3` is unavailable, skip this and rely on the current session.
3. Add what you learned in the current session.
4. Run `git log --oneline -30` and `git log --since="60 days ago" --name-only --format= | sort | uniq -c | sort -rn | head -20` to see what changed and where work concentrates.

## Step 2: Extract learnings

Keep a candidate only if it is durable and backed by evidence:
- a user correction (⚑), or the same issue in 2+ sessions,
- a command or failure confirmed in the code or by running it,
- a convention visible across the codebase.

Good candidates: exact commands with flags, things agents repeatedly got wrong, non-obvious setup or environment requirements, boundaries the user enforced, where key code lives. Drop one-off fixes, task-specific chatter, personal data, and anything secret.

## Step 3: Rewrite AGENTS.md

Follow current AGENTS.md practice:
- **Commands first**: build, test (including single-test), lint, format, run — exact and copy-pasteable.
- **Specific, not generic**: "Use `Decimal` for money" beats "write clean code". Delete generic advice the model already follows.
- **Boundaries**: short Always / Ask first / Never lists for risky areas (migrations, secrets, deploys, generated files).
- **Gotchas**: the concrete mistakes agents make here, paired with the right approach.
- **Map, not manual**: point to docs and key directories instead of describing code the agent can read. Prefer links to existing docs over inlining them.
- **Size**: aim under ~150 lines. If a subdirectory needs many rules of its own, move them to a nested `AGENTS.md` there.
- **Accurate**: verify every path, command, script, and version against `package.json`, `Makefile`, `pyproject.toml`, lockfiles, and CI. Fix or delete what doesn't match.
- **Vendor-neutral**: never name Claude, Anthropic, or any other specific AI product; say "the agent". Tool-specific instructions go in that tool's file.
- Keep the file's existing heading order and style unless it is clearly broken. Tighten wording; one line per idea.
- If a rule must hold on every edit (formatting, lint), note that it belongs in a hook, pre-commit, or CI check, and keep the line short.

## Step 4: Fix CLAUDE.md

- Already `@AGENTS.md` (plus optional Claude-only section): keep the import first; update the Claude-only section only with learnings specific to Claude Code (slash commands, hooks, plan mode, permissions).
- Contains full duplicated content: move shared content into `AGENTS.md` (deduplicated), then reduce `CLAUDE.md` to `@AGENTS.md` plus any genuinely Claude-only lines.
- Symlinked to `AGENTS.md`: leave it.
- Missing: create it with `@AGENTS.md`.
- If `AGENTS.md` is missing but `CLAUDE.md` exists: create `AGENTS.md` from the shared content (made vendor-neutral) and convert `CLAUDE.md` to the import.

## Step 5: Verify and report

- Re-read both files. Check that `@AGENTS.md` is on the first line of `CLAUDE.md`, not in backticks, and that `AGENTS.md` contains no vendor names.
- Spot-check two or three commands from the Commands section (e.g. `--help` or a dry run) where safe and fast.
- Report briefly: sessions analysed (count, date range, agents), lines added / removed / changed per file, and the most important new rules with the evidence behind each.
