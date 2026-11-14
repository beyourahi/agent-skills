---
title: "Without the sandbox, a moved HEAD is how a forbidden git commit by the implementer gets caught"
author: Beyourahi
date: 2026-11-14
branch: feat/execution-protocol-no-sandbox
cost: { minutes: 1, tokens: 52002 }
id: l-93c566cf
scope: repo
status: active
src: agent
touches:
  - skills/execution-protocol/opencode/opencode.json
  - skills/execution-protocol/scripts/implement_lib.py
  - skills/execution-protocol/scripts/implement.sh
---

## Decided
- Rahi removed every implementer sandbox on 2026-11-14 (his explicit, risk-accepted decision). Role limits now come from the OpenCode agent permissions in `skills/execution-protocol/opencode/opencode.json`, and `skills/execution-protocol/scripts/implement_lib.py` `sonnetcfg` turns them into matching Claude Code deny rules for the Sonnet fallback, so the two routes cannot drift.
- The sandbox used to block writes to `.git`. Now `skills/execution-protocol/scripts/implement.sh` records BASE before a round, and `final` fails the round with "HEAD moved" when HEAD changed. `discard()` force-detaches back to BASE, checks HEAD and a clean tree, and exits 6 ("restore to BASE failed") if any step fails.

## Watch out
- BASE must start empty with RT/RM/RE, or a BASE exported in the caller's shell leaks into `--discard`.
- The permission rules match command prefixes as text, so `sh -c 'git commit'` gets around them; the HEAD check and the gates are the backstop.

In the code:
- skills/execution-protocol/scripts/implement.sh discard(), BASE
- skills/execution-protocol/scripts/implement_lib.py sonnetcfg, final
