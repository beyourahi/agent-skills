# Report input (`depup.py report <run_ref> --input findings.json`)

The helper fills `schema`, `generated`, `run_id`, `mode`, `repo_root`, `base_commit`,
`branch`, `worktree`, `rollback` and `steps` from the journal; supplied values for these keys
are ignored. Supply the rest as one JSON object (all keys optional; unknown keys are kept):

```json
{
  "status": "PLAN | PASS | PARTIAL | BLOCKED | FAIL",
  "scope_profile": "security-only | compatible | all-stable",
  "scope": {"requested": "...", "included": ["..."], "excluded": [{"target": "...", "reason": "..."}]},
  "capabilities": {"network": true, "subagents": false, "browser": false, "write": true},
  "upgrades": [{"name": "uuid", "ecosystem": "javascript", "from": "3.4.0", "to": "11.1.0",
                "bump": "major", "risk": "high", "impact_tier": "routine|high",
                "workspace": ".", "status": "done|held|blocked"}],
  "targets": [],
  "migrations": [{"summary": "uuid/v4 deep import -> named import", "files": ["src/id.js"],
                  "method": "codemod|ast|manual", "source": "https://..."}],
  "files_changed": ["package.json", "package-lock.json", "src/id.js"],
  "scripts_executed": [{"command": "npm install uuid@11.1.0", "cwd": "<worktree>", "rc": 0}],
  "risks": [{"name": "database-driver", "summary": "...", "severity": "high",
              "disposition": "proceed|blocked", "analysis": "impact and evidence summary",
              "sources": [{"name": "migration guide", "url": "https://...", "version": "2.0", "retrieved": "2026-11-03"}],
              "alternatives": ["stay on 1.x", "upgrade through 2.x"],
              "controls": ["..."], "recovery": "...",
              "reviews": [{"type": "independent|security", "status": "pass", "evidence": "..."}]}],
  "security": [{"summary": "npm audit: 0 vulnerabilities", "severity": "info"}],
  "verification": [{"check": "tests", "command": "npm test", "result": "pass",
                     "log": "logs/test.log", "evidence": "logs/test-evidence.json"}],
  "skipped_checks": [{"check": "browser smoke", "reason": "no browser automation in this harness", "required": false}],
  "links": [{"name": "uuid changelog", "url": "https://github.com/uuidjs/uuid/blob/main/CHANGELOG.md",
             "version": "11.1.0", "retrieved": "2026-11-02"}],
  "reviews": [{"id": "critical-full-diff-1a2b3c4d5e6f", "kind": "critical|independent|security",
               "subject": "full-diff", "required": true, "status": "completed|unavailable|handoff",
               "verdict": "pass|fail", "candidate_head": "<commit>", "evidence": "reviews/<id>/receipt.json",
               "selected": {"priority": 3, "harness": "opencode", "model": "deepseek-v4.1-flash",
                            "effort": "high", "usage": "confirmed|unknown"},
               "candidates": [{"priority": 1, "status": "exhausted", "reason": "run failed: exhausted",
                               "checks": ["..."], "attempts": [{"category": "exhausted"}]}],
               "finding_counts": {"Important": 0, "Nit": 1, "Pre-existing": 0}}],
  "planning": [{"id": "plan-upgrade-1a2b3c4d-5e6f7a8b9c0d", "kind": "plan|architecture", "subject": "upgrade",
                "status": "completed|unavailable|handoff", "head": "<commit>", "planner": "claude-code/claude-opus-5-5@medium ...",
                "selected": {"priority": 1, "harness": "claude-code", "model": "claude-opus-5-5", "effort": "medium"},
                "candidates": [], "result": "plans/<id>/plan.json", "evidence": "plans/<id>/receipt.json"}],
  "delivery": {"mode": "direct-PR | local-only", "pushed": true, "pr": "https://github.com/...",
               "checks": "green", "merged": false, "reason": "why it stopped where it did"},
  "remaining_actions": ["Only physically human-only steps belong here"],
  "test_matrix": [],
  "baseline": [{"check": "tests", "result": "pass"}],
  "user_checkout_dirty_excluded": []
}
```

For `PASS`, each log and review artifact must have a separate JSON evidence receipt inside the
run directory. Each receipt binds `run_id`, clean `candidate_head`, `kind`, exact `subject`,
`result`, reviewer identity, artifact path, and artifact SHA-256. Verification `subject` is the
exact command; review `subject` is the named risk. Use distinct reviewers and distinct artifacts
for independent and security reviews. The helper emits `candidate_commit` from the clean owned
worktree; if the candidate is dirty or unavailable, it cannot report `PASS`. The receipt records
claims and artifact identity; the agent must inspect the underlying log/review and confirm its
content before recording it.

Receipt shape:

```json
{"run_id":"<run-id>","candidate_head":"<clean-commit>","kind":"verification",
 "subject":"npm test","result":"pass","reviewer":"<runner-id>",
 "artifact":"logs/test.log","artifact_sha256":"<sha256>"}
```

Use `kind: "independent-review"` or `"security-review"` for review receipts and the risk name
as `subject`.

Status rules enforced by the helper: `plan` runs report `PLAN` (or `BLOCKED`); `PASS` is
downgraded to `PARTIAL` unless the `baseline`, `verify` and `review` journal steps are done,
`verification` is non-empty, every entry has a nonempty
`command`, `"result": "pass"`, a nonempty log, and a valid candidate-bound evidence receipt; no
journal step is pending/running/failed/blocked, every `impact_tier: high` upgrade has a matching
high-risk item (risk `severity` `high` or `critical`) with complete analysis, source links, alternatives, controls, recovery path,
independent review and security review with distinct candidate-bound receipts and artifacts,
no high-risk item is blocked, no `skipped_checks` entry has
`required: true`, no upgrade has status `held`/`blocked`/`failed`, every `reviews` entry with
`required: true` is `completed` with verdict `pass` and a valid receipt for the current candidate,
and no review of the current candidate has verdict `fail`. Copy `reviews` entries verbatim from
`depup.py review run` output ([review-routing.md](review-routing.md)). `planning` is written by the
helper from `<run_dir>/plans/*/entry.json` (input values are ignored) and never gates `PASS`. `risk` describes the version
bump; `impact_tier` describes the affected system and consequences. High-risk items do not require
user approval; disposition follows the documented risk protocol. Corrections are listed in
`status_corrections`.
Outputs: `<run_dir>/report.json` and `<run_dir>/summary.md`, outside the repository.
