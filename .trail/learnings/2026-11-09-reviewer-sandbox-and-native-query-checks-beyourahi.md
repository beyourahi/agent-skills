---
title: "Reviewer sandbox and native-query checks fail closed, because OpenCode path rules are lexical and regex config checks are bypassable"
author: Beyourahi
date: 2026-11-09
branch: main
id: l-49a321b9
scope: repo
status: active
src: agent
touches:
  - skills/dependency-upgrade/scripts/depup.py
---

## Decided
- Repo configs that could run code (cargo config, rust-toolchain) are parsed with the real TOML parser; anything not parseable, oversized or on Python without tomllib is a hazard and the native query is skipped.
- The OpenCode reviewer gets a per-review deny-all permission list with allows only for its own dirs; a symlink resolving outside makes OpenCode unavailable (fallback), except executable toolchain files linked from bin/ or Scripts/ (venv interpreters).
- Anthropic credentials are stripped from every reviewer process, not just Claude.

## Tried and ruled out
- Line regexes on TOML: dotted keys, inline tables and quoted keys bypassed them.
- Allowing any regular file under toolchain prefixes: lets a repo link docs/x to a credential file like uv's store.
- COREPACK_ENABLE_DOWNLOAD_PROMPT: only hides a prompt; COREPACK_ENABLE_NETWORK=0 actually blocks downloads.

## Watch out
- OpenCode permission schema (V2 ordered list, last match wins) was verified live only on 2.2.6; re-run the sandbox smoke after OpenCode upgrades.
- pnpm configs are still regex-checked; JSON unicode escapes can hide keys.

In the code: skills/dependency-upgrade/scripts/depup.py (_read_config, _cargo_settings_hazard, PNPM_CONFIG_HAZARD_RE, NPM_SAFE_ENV); scripts/review_router.py (opencode_agent, symlink_escape, _toolchain_target, child_env). Tests must pass on both python3 and /usr/bin/python3 (3.9).
