---
name: frontend-ui-ux-overhaul
description: Comprehensive repository-wide frontend, UI, UX, interaction, accessibility, responsiveness, motion, design-system, and visual-quality audit plus implementation workflow.
---

# Frontend UI/UX overhaul

Use this skill when the user asks for a frontend, UI, UX, responsive, accessibility, motion, design-system, or visual-quality overhaul. A normal invocation such as `Run frontend-ui-ux-overhaul.` means audit and implement validated improvements, then verify them. Do not stop at an audit unless the user explicitly requests audit-only.

This is a router, not a replacement for the specialist skills. Read only the specialists needed for the current surface and phase. Their advice is advisory and must be reconciled with the repository.

## Authority order

Resolve conflicts in this order:

1. Current explicit user instruction.
2. Repository `AGENTS.md`, `CLAUDE.md`, or equivalent.
3. Current accepted product requirements.
4. Canonical shared design-system and component contracts.
5. Repository `DESIGN.md` or equivalent.
6. Existing intentional product identity and patterns.
7. This skill.
8. The installed Emil specialist skills.
9. Dynamically selected UI-Skills specialists.
10. Generic design conventions.

External skills must never blindly override the project architecture, brand, design system, accepted direction, or safety constraints. Never make a product look like Apple merely because `apple-design` is active.

## Baseline constraints

Unless a newer repository authority says otherwise:

- Do not add decorative visible borders, outlines, or rings for ordinary UI separation; use shade, contrast, spacing, hierarchy, material, and depth.
- Preserve a clearly visible accessible keyboard focus state.
- Do not use hover-scale effects on buttons.
- Preserve the canonical primary/CTA treatment.
- Prefer canonical design-system components and established animated-icon systems over local clones.
- Respect `prefers-reduced-motion` and avoid gratuitous animation.
- Check nested parent/child surface contrast, overflow, focus visibility, and target sizes.

## Dynamic UI-Skills registry

`ui-skills-root` is the registry router. For a concrete subtask, prefer one current UI-Skill; use two only when they cover distinct expertise; use three only for broad multidisciplinary work. Never load more than three UI-Skills for one concrete subtask.

Use the portable CLI, not a permanently copied catalog:

```bash
npx ui-skills start
npx ui-skills categories
npx ui-skills list --category <category>
npx ui-skills get <slug>
```

Fetch a specialist only when the current task needs it, read its returned guidance, and apply it against repository truth. Candidate names such as `accessibility`, `harden`, `fixing-motion-performance`, `polish`, `web-design-guidelines`, `audit`, and `frontend-ui-engineering` are discovery hints, not a permanent install list. If the CLI or network is unavailable, continue with installed specialists and native verification rather than inventing a copy of the catalog.

## Runtime workflow

### 1. Establish repository truth

Before editing, inspect the repository root, `git status`, branch, instruction files, framework, architecture, design system, component primitives, styling, animation infrastructure, tests, browser tooling, and representative routes/components. Preserve unrelated user work. If Graft is installed and a graph already exists, use its read-only map/grep/callers/blast tools for orientation or impact; do not create or rewrite a repository graph unless that mutation is in scope and safe.

### 2. Understand the rendered product

Inspect the running/rendered product where tooling permits; source-only judgments are insufficient for visual work. Create or update `DESIGN.md` only when it adds durable project value, and only after the step 10a plan. Existing project tokens, patterns, and product identity win.

### 3. Broad design review

Read `emil-design-eng` and `apple-design` as the initial design-craft lenses. Review layout, hierarchy, spacing, typography, visual consistency, components, states, interaction feedback, navigation, forms, and information architecture. Translate the findings into the project's existing design language.

### 4. Mobile and responsive review

Read `mobile-native` and select one narrow UI-Skill when useful. Check touch targets, safe areas, viewport behavior, mobile navigation, keyboard/input behavior, drawers/sheets, overflow, responsive layout, and pointer-versus-touch behavior.

### 5. Accessibility review

Dynamically select the smallest current accessibility UI-Skill. Verify rendered semantic HTML, keyboard access, focus and focus order, accessible names, dialogs, forms and errors, contrast, reduced motion, target sizes, and screen-reader semantics. Automated checks do not replace manual interaction evidence.

### 6. Resilience and hardening

Use `harden` or the strongest current registry equivalent when useful. Check loading, empty, error, success, extreme content, long labels, large datasets, network failure, permissions, repeated submissions, unusual Unicode, currencies/dates, localization, zoom, and text scaling.

### 7. Component and design-system review

Read `pick-ui-library` and `emil-design-eng` when component choices or primitives are involved. Prefer the existing design system and component primitives; fix systemic/root causes. Avoid unnecessary hand-rolled primitives, duplicate components, and new UI frameworks.

### 8. Existing animation audit

Read `improve-animations` and `apple-design`. Treat `improve-animations` as read-only when it declares an audit contract: it produces a plan; it does not edit source.

### 9. Motion opportunities

Read `find-animation-opportunities` and `animation-vocabulary`. Add motion only when it improves feedback, continuity, orientation, state understanding, or perceived responsiveness. Reject motion that adds noise, delay, distraction, or maintenance cost.

### 10. Prototype only when ambiguity is real

Read `prototype` only when there are materially different viable solutions whose choice cannot be resolved from repository truth. Do not prototype obvious fixes.

### 10a. Architecture and implementation plan (routed, read-only)

Before modifying files, when there is meaningful work (more than a trivial, local fix), write a handoff (accepted requirements, scope, authority order and baseline constraints, repository instructions, design system and component inventory, findings from steps 1-10 with file:line, verification tooling) and run ONE routed `plan` task (see Routed planning and review). Add one `--kind architecture --subject <change>` task only for significant or high-risk changes (shared design-system primitive, layout/navigation shell, checkout/auth UI, animation system, cross-repository contract). The planner is read-only and advisory: check every decision against the authority order and this skill, adopt or reject each with a reason, and implement it yourself. A plan never authorizes destructive operations, policy changes or an unrequested redesign. Replan only when scope or findings change materially. Planning `unavailable`: plan in this session and say so.

### 11. Implement validated improvements

Implement the selected fixes in the existing architecture. Read `animate` for motion implementation and the narrowest relevant specialist for other changes. Fix root causes when safe, preserve intentional product behavior, and do not turn the pass into an unrequested redesign.

### 12. Motion performance

Dynamically select `fixing-motion-performance` or the strongest current equivalent. Check layout thrashing, unnecessary measurements, expensive filters/blur, poor scroll animation, excessive JavaScript animation, avoidable rerenders, misuse of `will-change`, and avoidable main-thread work.

### 13. Animation review

Read `review-animations` after motion changes or when existing motion is material. Fix material findings and preserve reduced-motion behavior.

### 14. Final polish

Select one current polish or web-guidelines specialist when useful. Look for spacing inconsistency, typography, wrapping, alignment, visual noise, responsive discontinuities, overflow/clipping, stacking, and inconsistent states. Stop before unrequested redesign.

### 15. Rendered verification

Inspect representative desktop, tablet/laptop where useful, and mobile views plus important states and flows. Check overflow, clipping, wrapping, spacing, contrast, sticky/fixed collisions, focus, touch, keyboard behavior, animation, and layout shift. Use the repository's browser tooling and isolated sessions by default; use a shared logged-in browser only when the task requires it.

### 16. Repository verification

Run the repository's actual applicable formatting, linting, static analysis, TypeScript checks, unit/component tests, integration/E2E tests, and production build. Fix regressions caused by the work. Never weaken verification to obtain green results.

### 17. Independent review (routed, before completion or delivery)

Review the actual final changes against the run base (the commit recorded by `start` before any edit), each category as its own routed task with `--required`, depth by risk:

- Low (copy, token or local style fix, no interaction change): `code`.
- Normal: `code` + `ui-ux`.
- High (broad overhaul, shared design system, navigation/layout shell, forms/checkout/auth UI, motion system): `code` + `ui-ux` + `accessibility`.

Pass `--context` (requirements, accepted decisions, checks run with results) and, for `ui-ux` and `accessibility`, `--rendered-evidence <manifest>` listing the real screenshots, viewports, states, keyboard and reduced-motion checks from step 15. If rendering was impossible, pass `--no-rendered-evidence "<why>"` instead; never claim visual verification that did not happen. Fix findings in this session, rerun the affected checks and rendered verification, then review the new candidate (`reused` results for an unchanged candidate are not repeated). Finish only when `gate --require code` (plus `--require ui-ux` / `--require accessibility` for the tiers above) exits 0; if a required review cannot run, report it unverified and do not deliver.

## Context discipline

Do not load every specialist at once. Load specialists sequentially by phase and concrete need; a full run may use many specialists over time without putting them all in one context. A mobile sheet might use `apple-design`, `mobile-native`, and one accessibility or interaction UI-Skill. A motion-performance pass might use `review-animations` and `fixing-motion-performance`.

## Parallel work

Parallelize only when the overhaul is large and ownership is non-overlapping. Valid partitions include marketing, dashboard/app, mobile/responsive, accessibility, motion, and the shared design system. Assign one owner per shared file/component; send cross-cutting findings to the appropriate owner. Every delegated prompt must state exact scope, constraints, verification, preservation of unrelated work, unchanged provider/model/thinking-effort routing, and the current registered delivery policy from `~/AGENTS.md` and the orchestration owner. Use repository-owned risk/blast-radius-based validation and the routed independent reviews of step 17; No-Mistakes is permanently retired. Use Codex-backed agents only for parallel implementation workers unless the current user explicitly authorizes an exception; planning and review tasks always go through the shared router. Jev is optional: use it only if it is installed, working, relevant, and concretely useful; never block on it.

## Routed planning and review

Shared with `dependency-upgrade` (one implementation). `R=~/.agents/skills/dependency-upgrade/scripts/routed_task.py`:

- Before the first edit: `python3 $R start --skill frontend-ui-ux-overhaul --repo .` → `run_dir` (records HEAD as the review base); then `python3 $R plan <run_dir> --context <handoff> [--kind architecture --subject S]`, `python3 $R review <run_dir> --kind code|ui-ux|accessibility --required ...`, `python3 $R gate <run_dir>`. Always pass `--session-harness`, `--session-model`, `--session-effort` with this session's exact values. Run tasks one at a time as background commands (up to 30 min each) and wait for the JSON.
- Each task runs on exactly one model in a fresh read-only session outside the repo, by fixed priority with usage-aware fallback: Claude Opus 5.5 medium (Claude Code) → GPT-6.1 Sol high (Codex CLI) → DeepSeek V4.1 Flash 1017 high (OpenCode Go `opencode-go/deepseek-v4.1-flash`) → this session's own model/effort/harness. Unknown quota is attempted, not skipped; exhausted, unauthenticated, unsupported or failing routes fall back automatically; nothing is substituted and no global setting changes. `python3 $R probe` shows availability without model calls.
- `handoff` (harness without a CLI adapter): run the printed prompt in a fresh read-only native subagent, then `python3 $R record`. The implementing session never satisfies a required review itself.
- Implementation, debugging, fixes and verification stay with this session's model, effort and harness. Report the selected models, fallbacks with reasons, verdicts and the gate result.

## Related repositories

When working across related repositories, identify dependencies and the canonical shared design-system owner first. Fix generic issues at the shared source when appropriate, verify the shared contract, then update consumers. Do not let downstream repositories invent conflicting primitives.

## Git and safety

Preserve unrelated branches, worktrees, uncommitted changes, and user-owned files. This skill does not automatically force commits, pushes, merges, or cleanup; perform those only when the invoking user or workflow explicitly requests them. Never overwrite unrelated work. Do not modify provider/model/thinking-effort routing. Follow current global and registered direct-PR/local-only delivery policy; No-Mistakes is permanently retired. Preserve all independent safety and authorization boundaries.
