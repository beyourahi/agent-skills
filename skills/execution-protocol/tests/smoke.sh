#!/usr/bin/env bash
# tests/smoke.sh - fakes-only smoke tests for scripts/gate.sh and scripts/premerge-and-merge.sh.
# Temp PATH/HOME/XDG_STATE_HOME, local bare origin, no network, no real claude/cswap/gh/opencode.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d "${TMPDIR:-/tmp}/gate-smoke.XXXXXX")
export T HOME="$T/home" XDG_STATE_HOME="$T/state" PATH="$T/bin:$PATH"
mkdir -p "$HOME" "$XDG_STATE_HOME" "$T/bin"
: > "$T/calls.log"; : > "$T/body.txt"
FAILED=0 GRC=0 PRC=0
trap 'rm -rf "$T"' EXIT
ok() { printf 'ok   %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; FAILED=1; }
chk() { local d=$1; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
calls() { grep -c -E "$1" "$T/calls.log" 2>/dev/null || true; }
reset() { : > "$T/calls.log"; rm -f "$T"/claude.*.json "$T"/cn "$T"/oc.out.jsonl "$T"/on; unset FAKE_CSWAP_EXIT FAKE_CHECKS_EXIT FAKE_CHECKS_MSG LOOP_SCENARIO; export FAKE_PR_STATE=OPEN; : > "$T/body.txt"; }
cat > "$T/bin/claude" <<'FAKE'
#!/usr/bin/env bash
printf 'claude %s\n' "$*" >> "$T/calls.log"
cat >/dev/null
n=$(( $(cat "$T/cn" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$T/cn"
cat "$T/claude.$n.json" 2>/dev/null || cat "$T/claude.default.json"
FAKE
cat > "$T/bin/cswap" <<'FAKE'
#!/usr/bin/env bash
printf 'cswap %s\n' "$*" >> "$T/calls.log"
case "$1" in status) printf '{\n "active": {"number": 3, "email": "leak@example.com"}\n}\n' ;; *) echo '{"active":{"number":2,"email":"leak@example.com"}}' ;; esac
exit "${FAKE_CSWAP_EXIT:-0}"
FAKE
cat > "$T/bin/opencode" <<'FAKE'
#!/usr/bin/env bash
printf 'opencode %s\n' "$*" >> "$T/calls.log"
if [ -n "${LOOP_SCENARIO:-}" ]; then
  n=$(( $(cat "$T/on" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$T/on"
  case "$LOOP_SCENARIO" in
    done2) [ "$n" -ge 2 ] && printf 'STATUS: done\n' > "$EVID/PROGRESS.md" ;;
    blocked) printf 'STATUS: blocked external - x\n' > "$EVID/PROGRESS.md" ;;
  esac
  echo '{}'
else cat "$T/oc.out.jsonl"; fi
FAKE
cat > "$T/bin/gh" <<'FAKE'
#!/usr/bin/env bash
printf 'gh %s\n' "$*" >> "$T/calls.log"
case "$1 $2" in
  "pr checks") [ -n "${FAKE_CHECKS_MSG:-}" ] && echo "$FAKE_CHECKS_MSG"; exit "${FAKE_CHECKS_EXIT:-0}" ;;
  "pr merge") exit 0 ;;
  "pr edit") while [ $# -gt 0 ]; do [ "$1" = --body-file ] && cp "$2" "$T/body.txt"; shift; done; exit 0 ;;
  "pr view") python3 - "$T" <<'PY'
import json, os, sys
t = sys.argv[1]
head = open(t + "/gh.head").read().strip() if os.path.exists(t + "/gh.head") else ""
body = open(t + "/body.txt").read() if os.path.exists(t + "/body.txt") else ""
print(json.dumps({"headRefOid": head, "baseRefName": "main", "state": os.environ.get("FAKE_PR_STATE", "OPEN"), "body": body}))
PY
  ;;
esac
FAKE
chmod +x "$T/bin"/*
unset GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM; export GIT_CONFIG_NOSYSTEM=1
git config --global user.email smoke@example.com; git config --global user.name Smoke; git config --global init.defaultBranch main
R=$T/repo; git init -q --bare "$T/origin.git"; git init -q "$R"
printf 'a\n' > "$R/f.txt"; git -C "$R" add f.txt; git -C "$R" commit -qm base
git -C "$R" remote add origin "$T/origin.git"; git -C "$R" push -q origin main; git -C "$R" remote set-head origin main
printf 'feat\n' > "$R/g.txt"; git -C "$R" add g.txt; git -C "$R" commit -qm feat
H=$(git -C "$R" rev-parse HEAD)
claude_out() { cat > "$T/claude.default.json" <<JSON
{"is_error":false,"session_id":"sess-1","structured_output":{"gate":"$1","head_sha":"$2","verdict":"$3","summary":"ok","findings":[]},"modelUsage":{"claude-opus-5-5-20260101":{"inputTokens":11,"outputTokens":22}},"total_cost_usd":0.05}
JSON
}
oc_out() { python3 - "$T/oc.out.jsonl" "$1" "$2" <<'PY'
import json, sys
o = {"gate": sys.argv[2], "head_sha": sys.argv[3], "verdict": "pass", "summary": "ok", "findings": []}
t = "Here is the result:\n```json\n%s\n```\n" % json.dumps(o)
open(sys.argv[1], "w").write(json.dumps({"type": "text", "part": {"text": t}}) + "\n")
PY
}
cmp_extract() { python3 - "$1" "$2" <<'PY'
import json, sys
text = "".join((json.loads(l).get("part") or {}).get("text", "") for l in open(sys.argv[1]) if l.strip() and json.loads(l).get("type") == "text")
dec = json.JSONDecoder(); best = None
for i, c in enumerate(text):
    if c == "{":
        try: o, j = dec.raw_decode(text, i)
        except Exception: continue
        if isinstance(o, dict) and (not best or j > best[1]): best = (i, j)
assert best and json.load(open(sys.argv[2])) == json.loads(text[best[0]:best[1]])
PY
}
pkt() { local b; b=$(git -C "$R" merge-base "${3:-origin/main}" HEAD)
  { printf 'Gate: %s\nhead: %s\nbase: %s\n' "$1" "$2" "$b"; git -C "$R" diff --no-color "$b" HEAD; } > "$E/packet-$1.md"; }
gatefile() { printf '{"structured_output":{"gate":"%s","head_sha":"%s","verdict":"pass","summary":"ok","findings":[]}}\n' "$2" "$3" > "$1/$2-$3.json"; }
# logline EVID gate key tier route file [status] [repo-dir [base-ref]]: a repo dir adds base_ref/base_sha/diff_sha256 bound to key.
logline() { local br="" bs="" ds=""
  if [ -n "${8:-}" ]; then br=${9:-origin/main}; bs=$(git -C "$8" merge-base "$br" "$3")
    ds=$(git -C "$8" diff --no-color --full-index "$bs" "$3" | shasum -a 256 | awk '{print $1}'); fi
  python3 - "$1/gates.log" "$2" "$3" "$4" "$5" "$6" "${7:-ok}" "$br" "$bs" "$ds" <<'PY'
import hashlib, json, os, sys
path, gate, key, tier, route, fname, status, br, bs, ds = sys.argv[1:11]
row = {"ts": "2026-11-11T00:00:00Z", "gate": gate, "tier": tier, "round": 1, "key": key, "route": route,
       "model": "m", "effort": "e", "session_id": "s", "account": "1", "input_tokens": 1, "output_tokens": 1,
       "cost_usd": 0.0, "verdict": os.environ.get("LOGV", "pass"), "status": status, "reason": "", "file": fname, "file_sha256": ""}
if br: row.update(base_ref=br, base_sha=bs, diff_sha256=ds)
p = os.path.join(os.path.dirname(path), fname)
if fname and os.path.exists(p): row["file_sha256"] = hashlib.sha256(open(p, "rb").read()).hexdigest()
open(path, "a").write(json.dumps(row) + "\n")
PY
}
# editlog EVID field del|VALUE: drop or overwrite one field on every review line.
editlog() { python3 - "$1/gates.log" "$2" "$3" <<'PY'
import json, sys
p, f, v = sys.argv[1:4]; rows = [json.loads(l) for l in open(p) if l.strip()]
for d in rows:
    if d.get("gate") == "review": d.pop(f, None) if v == "del" else d.update({f: v})
open(p, "w").write("".join(json.dumps(d) + "\n" for d in rows))
PY
}
gaterun() { ( cd "$R" && EVID="$1" "$ROOT/scripts/gate.sh" "$2" "$3" "${@:4}" >"$T/gate.out" 2>&1 ); GRC=$?; }
# branch NAME FILE...: fresh clone of origin with one commit per FILE; sets R and BH.
branch() { R=$T/$1; shift; rm -rf "$R"; git clone -q "$T/origin.git" "$R"
  for f; do mkdir -p "$(dirname "$R/$f")"; printf '%s\n' "$f" > "$R/$f"; git -C "$R" add "$f"; git -C "$R" commit -qm "$f"; done; BH=$(git -C "$R" rev-parse HEAD); }
pmrun() { ( cd "$2" && EVID="$1" "$ROOT/scripts/premerge-and-merge.sh" "${@:3}" >"$T/pm.out" 2>&1 ); PRC=$?; }
new_ev() { E=$T/$1; rm -rf "$E"; mkdir -p "$E"; }
for f in scripts/gate.sh scripts/premerge-and-merge.sh tests/smoke.sh; do chk "bash -n $f" bash -n "$ROOT/$f"; done
chk "delta-round rule in SKILL and brief" bash -c "grep -q 'Changes since the last round' '$ROOT/SKILL.md' && grep -q 'Changes since the last round' '$ROOT/opus-gate-brief.md'"
chk "cap epoch needs authorized line" grep -q '"status": "authorized"' "$ROOT/SKILL.md"
chk "help (bash)" bash -c "bash '$ROOT/scripts/gate.sh' --help && bash '$ROOT/scripts/premerge-and-merge.sh' --help"
if command -v zsh >/dev/null; then chk "help (zsh)" zsh -c "'$ROOT/scripts/gate.sh' --help && '$ROOT/scripts/premerge-and-merge.sh' --help"; fi
chk "no home paths" bash -c "! grep -rqE '/Use[r]s/' '$ROOT'"
chk "SKILL paths" bash -c "grep -q 'scripts/gate.sh' '$ROOT/SKILL.md' && grep -q 'scripts/premerge-and-merge.sh' '$ROOT/SKILL.md'"
chk "SKILL round line" grep -qF 'Rounds are counted per gate from EVID/gates.log across all sessions' "$ROOT/SKILL.md"
chk "line limits" bash -c "for f in scripts/gate.sh scripts/premerge-and-merge.sh; do [ \$(wc -l < '$ROOT/'\"\$f\") -le 200 ] || exit 1; done; [ \$(wc -l < '$ROOT/tests/smoke.sh') -le 350 ]"
# --- gate: happy path (medium review) ---
reset; claude_out review "$H" pass; new_ev g1; pkt review "$H"; gaterun "$E" review medium
chk "happy exit+file" bash -c "[ $GRC = 0 ] && test -s '$E/review-$H.json'"
chk "opus argv" bash -c "grep -q -- '--model claude-opus-5-5' '$T/calls.log' && grep -q -- '--effort medium' '$T/calls.log' && grep -q -- '--strict-mcp-config' '$T/calls.log' && grep -q 'WebFetch,WebSearch' '$T/calls.log' && grep -q -- '--json-schema' '$T/calls.log'"
chk "log ok+account+sha" bash -c "grep -q '\"status\": \"ok\"' '$E/gates.log' && grep -q '\"account\": \"2\"' '$E/gates.log' && grep -q '\"session_id\": \"sess-1\"' '$E/gates.log' && grep -q '\"cost_usd\": 0.05' '$E/gates.log' && grep -q '\"file_sha256\": \"[0-9a-f]' '$E/gates.log'"
chk "no email leak" bash -c "! grep -rq leak@example.com '$E'"
# --- gate: high + --trigger effort ---
reset; claude_out review "$H" pass; new_ev g2; pkt review "$H"; gaterun "$E" review high --trigger
chk "high exit+effort" bash -c "[ $GRC = 0 ] && grep -q -- '--effort high' '$T/calls.log'"
# --- gate: fallback when cswap exit 3 ---
reset; export FAKE_CSWAP_EXIT=3; oc_out review "$H"; new_ev g3; pkt review "$H"; gaterun "$E" review medium
chk "fallback exit+no claude" bash -c "[ $GRC = 0 ] && [ \$(grep -c '^claude ' '$T/calls.log') = 0 ]"
chk "fallback argv" bash -c "grep -q -- '--standalone --agent gate' '$T/calls.log' && grep -q 'deepseek-v4.1-flash#max' '$T/calls.log' && grep -q -- ' -f ' '$T/calls.log'"
chk "fallback no -s" bash -c "! grep -qE 'opencode .* -s ' '$T/calls.log'"
chk "raw kept" test -s "$E/review-$H.raw.jsonl"
cmp_extract "$E/review-$H.raw.jsonl" "$E/review-$H.json"; chk "saved equals extracted" test $? = 0
chk "fallback logged" bash -c "grep -q '\"route\": \"fallback\"' '$E/gates.log' && grep -q 'opus unavailable' '$E/gates.log'"
# --- gate: one usage limit -> one cswap switch, two claude calls ---
reset; new_ev g4; pkt review "$H"
printf '%s' '{"is_error":true,"result":"usage limit reached"}' > "$T/claude.1.json"; claude_out review "$H" pass; cp "$T/claude.default.json" "$T/claude.2.json"
gaterun "$E" review medium
chk "limit one switch+two claude" bash -c "[ $GRC = 0 ] && [ \$(grep -c '^cswap switch' '$T/calls.log') = 1 ] && [ \$(grep -c '^claude ' '$T/calls.log') = 2 ]"
chk "account after switch logged" bash -c "head -1 '$E/gates.log' | grep -q '\"account\": \"2\"' && tail -1 '$E/gates.log' | grep -q '\"account\": \"3\"' && ! grep -q leak '$E/gates.log'"
mv "$T/bin/cswap" "$T/cswap.off"; reset; new_ev g4b; pkt review "$H"; printf '%s' '{"is_error":true,"result":"usage limit reached"}' > "$T/claude.1.json"; claude_out review "$H" pass; cp "$T/claude.default.json" "$T/claude.2.json"
P0=$PATH; PATH="$T/bin:$(dirname "$(command -v python3)"):$(dirname "$(command -v git)"):/usr/bin:/bin"; command -v cswap >/dev/null && exit 9; gaterun "$E" review medium; PATH=$P0; mv "$T/cswap.off" "$T/bin/cswap"
chk "no cswap + limit reruns, exit 0" bash -c "[ $GRC = 0 ] && [ \$(grep -c '^claude ' '$T/calls.log') = 2 ] && tail -1 '$E/gates.log' | grep -q '\"account\": \"signed-in\"'"
# --- gate: verdict fail ---
reset; claude_out review "$H" fail; new_ev g5; pkt review "$H"; gaterun "$E" review medium
chk "fail exit 1 + no opencode" bash -c "[ $GRC = 1 ] && [ \$(grep -c '^opencode ' '$T/calls.log') = 0 ]"
# --- gate: fallback error twice -> blocked ---
reset; export FAKE_CSWAP_EXIT=3; new_ev g6; pkt review "$H"; printf '%s\n' '{"type":"error","error":"boom"}' > "$T/oc.out.jsonl"
gaterun "$E" review medium
chk "blocked exit+log+file+calls" bash -c "[ $GRC = 3 ] && grep -q '\"status\": \"blocked\"' '$E/gates.log' && test ! -e '$E/review-$H.json' && [ \$(grep -c '^opencode ' '$T/calls.log') = 2 ]"
# --- gate: refusals (exit 2, no model) ---
reset; new_ev g7; printf 'Gate: review\nhead: <head>\n' > "$E/packet-review.md"; gaterun "$E" review medium
chk "placeholder exit2 no model" bash -c "[ $GRC = 2 ] && [ \$(grep -cE '^(claude|opencode) ' '$T/calls.log') = 0 ]"
new_ev g7; pkt review "$H"; printf 'b\n' >> "$R/f.txt"; gaterun "$E" review medium
chk "dirty exit2 no model" bash -c "[ $GRC = 2 ] && [ \$(grep -cE '^(claude|opencode) ' '$T/calls.log') = 0 ]"; git -C "$R" checkout -q -- f.txt
new_ev g7; pkt security "$H"; gaterun "$E" security low
chk "security low exit2 no model" bash -c "[ $GRC = 2 ] && [ \$(grep -cE '^(claude|opencode) ' '$T/calls.log') = 0 ]"
new_ev g8; printf 'Gate: review\nhead: %s\n+stale diff\n' "$H" > "$E/packet-review.md"; gaterun "$E" review medium
chk "stale packet diff exit2 no model" bash -c "[ $GRC = 2 ] && [ \$(grep -cE '^(claude|opencode) ' '$T/calls.log') = 0 ]"
# --- gate: cap at 3 ok rounds, reset by an ok plan ---
reset; claude_out review "$H" pass; new_ev g9; pkt review "$H"
for i in 1 2 3; do logline "$E" review "$H" medium opus ""; done; gaterun "$E" review medium
chk "cap exit4+log+no model" bash -c "[ $GRC = 4 ] && grep -q '\"status\": \"cap\"' '$E/gates.log' && [ \$(grep -cE '^(claude|opencode) ' '$T/calls.log') = 0 ]"
logline "$E" plan "$H" medium opus ""; gaterun "$E" review medium
chk "after plan exit 0" test "$GRC" = 0
# --- gate: low review via opencode plan agent ---
reset; oc_out review "$H"; new_ev g10; pkt review "$H"; gaterun "$E" review low
chk "low exit+argv+file" bash -c "[ $GRC = 0 ] && grep -q -- '--agent plan' '$T/calls.log' && grep -q 'deepseek-v4.1-flash#high' '$T/calls.log' && ! grep -qE 'opencode .* -s ' '$T/calls.log' && test -s '$E/review-$H.json'"
# --- gate: --base must be origin/<ref> ---
for b in main HEAD~1; do reset; claude_out review "$H" pass; new_ev gb; pkt review "$H"; gaterun "$E" review medium --base "$b"
  chk "--base $b exit2 no model" bash -c "[ $GRC = 2 ] && [ \$(grep -cE '^(claude|opencode) ' '$T/calls.log') = 0 ]"; done
# --- gate: round cap after re-plan (6 ok total) ---
reset; claude_out review "$H" pass; new_ev g12; pkt review "$H"
for i in 1 2 3 4 5 6; do logline "$E" review "$H" medium opus ""; [ "$i" != 3 ] || logline "$E" plan "$H" medium opus ""; done
gaterun "$E" review medium
chk "6 rounds exit3 blocked no model" bash -c "[ $GRC = 3 ] && tail -1 '$E/gates.log' | grep -q '\"status\": \"blocked\"' && tail -1 '$E/gates.log' | grep -q 'round cap after re-plan' && [ \$(grep -cE '^(claude|opencode) ' '$T/calls.log') = 0 ]"
logline "$E" plan "$H" medium opus ""; gaterun "$E" review medium
chk "6 rounds + plan still exit3" bash -c "[ $GRC = 3 ] && [ \$(grep -cE '^(claude|opencode) ' '$T/calls.log') = 0 ]"
# --- gate: classify never crashes (AC18) ---
reset; new_ev g13; pkt review "$H"; printf '[]\n' > "$T/claude.1.json"
printf '%s\n' '{"structured_output": "x", "modelUsage": 5}' > "$T/claude.2.json"; cp "$T/claude.2.json" "$T/claude.default.json"; oc_out review "$H"
gaterun "$E" review medium
chk "odd claude json -> 2 invalid + fallback" bash -c "[ $GRC = 0 ] && [ \$(grep -c '\"status\": \"invalid\"' '$E/gates.log') = 2 ] && grep -q '\"route\": \"fallback\"' '$E/gates.log' && [ \$(grep -c '^opencode ' '$T/calls.log') -ge 1 ] && python3 -c 'import json,sys;[json.loads(l) for l in open(sys.argv[1])]' '$E/gates.log'"
reset; export FAKE_CSWAP_EXIT=3; new_ev g14; pkt review "$H"; printf '[1, 2]\n' > "$T/oc.out.jsonl"; gaterun "$E" review medium
chk "non-dict opencode -> invalid+reason, blocked, 1 line/attempt" bash -c "[ $GRC = 3 ] && [ \$(grep -c '^opencode ' '$T/calls.log') = 2 ] && [ \$(wc -l < '$E/gates.log') = 2 ] && grep '\"status\": \"invalid\"' '$E/gates.log' | grep -qv '\"reason\": \"\"' && tail -1 '$E/gates.log' | grep -q '\"status\": \"blocked\"'"
# --- gate: repo-controlled opencode config (AC17) ---
R0=$R H0=$H
reset; branch oc1 .opencode/agent/plan.md; H=$BH; oc_out review "$H"; new_ev g15; pkt review "$H"; gaterun "$E" review low
chk "opencode config low exit2 no opencode" bash -c "[ $GRC = 2 ] && grep -q 'agent config changed' '$T/gate.out' && [ \$(grep -c '^opencode ' '$T/calls.log') = 0 ]"
reset; many=""; for i in $(seq 1 400); do many="$many z/$(printf '%0200d' $i).txt"; done; branch oc3 .opencode/a.md $many; H=$BH; oc_out review "$H"; new_ev g15b; pkt review "$H"; gaterun "$E" review low
chk "opencode config found in large diff" bash -c "[ $GRC = 2 ] && grep -q 'agent config changed' '$T/gate.out'"
reset; branch ga1 x.txt; printf 'x.txt -diff\n' > "$R/.gitattributes"; printf 'secret change\n' >> "$R/x.txt"; git -C "$R" add -A; git -C "$R" commit -qm ga; H=$(git -C "$R" rev-parse HEAD); new_ev g15c; pkt review "$H"; gaterun "$E" review medium
chk "text hidden as binary exit2 no model" bash -c "[ $GRC = 2 ] && grep -q 'Binary files differ' '$T/gate.out' && [ \$(grep -c '^claude ' '$T/calls.log') = 0 ]"
reset; printf 'a\000b' > "$T/real.bin"; branch gb1 y.txt; cp "$T/real.bin" "$R/real.bin"; git -C "$R" add -A; git -C "$R" commit -qm bin; H=$(git -C "$R" rev-parse HEAD); oc_out review "$H"; new_ev g15d; pkt review "$H"; gaterun "$E" review low
chk "real binary still gated" test "$GRC" = 0
reset; branch oc4 ".opencode/plugin/\303\244.ts"; H=$BH; oc_out review "$H"; new_ev g15e; pkt review "$H"; gaterun "$E" review low
chk "non-ascii .opencode path found" bash -c "[ $GRC = 2 ] && grep -q 'agent config changed' '$T/gate.out'"
reset; branch oc6 docs/agents.md; H=$BH; oc_out review "$H"; new_ev g15h; pkt review "$H"; gaterun "$E" review low
chk "lower-case agents.md never low" test "$GRC" = 2
reset; branch oc7 .OpenCode/p.ts; H=$BH; oc_out review "$H"; new_ev g15i; pkt review "$H"; gaterun "$E" review low
chk "mixed-case .OpenCode never low" test "$GRC" = 2
reset; branch oc5 docs/AGENTS.md; H=$BH; oc_out review "$H"; new_ev g15f; pkt review "$H"; gaterun "$E" review low
chk "AGENTS.md change never low" bash -c "[ $GRC = 2 ] && [ \$(grep -c '^opencode ' '$T/calls.log') = 0 ]"
reset; branch rf1 r.txt; H=$BH; new_ev g15g; pkt review "$H"; gaterun "$E" review medium --base origin/nope
chk "refusal after key is logged" bash -c "[ $GRC = 2 ] && tail -1 '$E/gates.log' | grep -q '\"status\": \"invalid\"' && grep -q 'no merge-base' '$E/gates.log'"
reset; export FAKE_CSWAP_EXIT=3; branch oc2 sub/opencode.json; H=$BH; oc_out review "$H"; new_ev g16; pkt review "$H"; gaterun "$E" review medium
chk "opencode config fallback blocked no opencode" bash -c "[ $GRC = 3 ] && tail -1 '$E/gates.log' | grep -q '\"status\": \"blocked\"' && grep -q 'agent config in diff' '$E/gates.log' && [ \$(grep -c '^opencode ' '$T/calls.log') = 0 ]"
R=$R0 H=$H0; reset
# --- premerge ---
pm_meta() { E=$T/$1; rm -rf "$E"; mkdir -p "$E"; printf '# PLAN\n\nTier: %s\n' "$2" > "$E/PLAN.md"
  printf '# recheck\n\nTier: %s\n' "$3" > "$E/tier-recheck-$H.md"; printf '{"gate":"plan","tier":"%s"}' "$2" > "$E/plan-$H.json"
  logline "$E" plan "$H" "$2" opus "plan-$H.json"; }
pm_reset() { : > "$T/body.txt"; echo "$H" > "$T/gh.head"; : > "$T/calls.log"; }
mk_pm() { pm_meta "$@"; gatefile "$E" review "$H"; logline "$E" review "$H" "$3" opus "review-$H.json" ok "$R"; pm_reset; }
refuse() { local n=$1; mk_pm "$n" "${2:-medium}" "${3:-medium}"; eval "${4:-:}"; pmrun "$E" "$R" 1
  chk "refuse:$n exit1+no edit/merge" bash -c "[ $PRC = 1 ] && ! grep -qE 'pr (edit|merge)' '$T/calls.log'"; }
mk_pm p1 medium medium; pmrun "$E" "$R" 1
chk "pm happy exit+txt" bash -c "[ $PRC = 0 ] && test -s '$E/premerge-$H.txt'"
chk "pm body block+head" bash -c "grep -q '<!-- premerge:start -->' '$T/body.txt' && grep -q '$H' '$T/body.txt'"
chk "pm merge argv" bash -c "grep -q 'pr merge 1 --squash --match-head-commit $H' '$T/calls.log' && ! grep -qE -- '--admin|--auto|--delete-branch' '$T/calls.log'"
chk "pm merged log" grep -q '"status": "merged"' "$E/gates.log"
refuse unlogged medium medium 'grep -v "\"gate\": \"review\"" "$E/gates.log" > "$E/l"; mv "$E/l" "$E/gates.log"'
refuse edited medium medium 'printf x >> "$E/review-$H.json"'
refuse noplan medium medium 'grep -v "\"gate\": \"plan\"" "$E/gates.log" > "$E/l"; mv "$E/l" "$E/gates.log"'
refuse planhigh medium medium 'printf "Tier: high\n" >> "$E/PLAN.md"'
refuse nosec high high
refuse lowroute medium medium 'sed "s/\"route\": \"opus\"/\"route\": \"deepseek-low\"/" "$E/gates.log" > "$E/l"; mv "$E/l" "$E/gates.log"'
refuse rounds4 medium medium 'logline "$E" review "$H" medium opus ""; logline "$E" review "$H" medium opus ""; logline "$E" review "$H" medium opus ""'
refuse blocked medium medium 'logline "$E" review "$H" medium opus "" blocked'
refuse checksfail medium medium 'export FAKE_CHECKS_EXIT=1'
refuse headneq medium medium 'echo deadbeef > "$T/gh.head"'
refuse plancaps medium medium 'printf "# PLAN (x) — Tier: High\n" > "$E/PLAN.md"'
refuse lowtier medium medium 'sed "/\"gate\": \"review\"/s/\"tier\": \"medium\"/\"tier\": \"low\"/" "$E/gates.log" > "$E/l"; mv "$E/l" "$E/gates.log"'
refuse notier medium medium 'printf "# recheck\n" > "$E/tier-recheck-$H.md"'
refuse noci_noflag medium medium 'export FAKE_CHECKS_EXIT=1 FAKE_CHECKS_MSG="no checks reported"'
refuse nobasesha medium medium 'editlog "$E" base_sha del'
refuse diffsha medium medium 'editlog "$E" diff_sha256 0000000000000000000000000000000000000000000000000000000000000000'
refuse baseref medium medium 'editlog "$E" base_ref origin/other'
refuse rounds7 medium medium 'mv "$E/gates.log" "$E/l"; for i in 1 2 3 4 5 6; do logline "$E" review "$H" medium opus ""; [ "$i" != 3 ] || logline "$E" plan "$H" medium opus ""; done; logline "$E" plan "$H" medium opus ""; cat "$E/l" >> "$E/gates.log"'
reset; mk_pm unblk medium medium; logline "$E" review "$H" medium fallback "" blocked; logline "$E" review "$H" medium opus "review-$H.json" ok "$R"; pmrun "$E" "$R" 1
chk "pm later ok clears blocked" bash -c "[ $PRC = 0 ]"
reset; export FAKE_CHECKS_EXIT=1 FAKE_CHECKS_MSG="no checks reported on the 'b' branch"; mk_pm nci medium medium; pmrun "$E" "$R" 1 --no-ci
chk "pm no CI configured passes" bash -c "[ $PRC = 0 ] && grep -q 'CI: none (--no-ci)' '$T/body.txt'"
ML=$(printf "ci failed\nno checks reported on the 'b' branch"); reset; export FAKE_CHECKS_EXIT=1 FAKE_CHECKS_MSG="$ML"; mk_pm ciml medium medium; pmrun "$E" "$R" 1 --no-ci
chk "--no-ci needs the exact one-line message" bash -c "[ $PRC = 1 ] && ! grep -qE 'pr (edit|merge)' '$T/calls.log'"
O=$T/ci; git clone -q "$T/origin.git" "$O"; mkdir -p "$O/.github/workflows"; echo x > "$O/.github/workflows/ci.yml"; git -C "$O" add -A; git -C "$O" commit -qm ci; git -C "$O" push -q origin main
reset; export FAKE_CHECKS_EXIT=1 FAKE_CHECKS_MSG="no checks reported on the 'b' branch"; mk_pm cisub medium medium; mkdir -p "$R/sub"; pmrun "$E" "$R/sub" 1 --no-ci
chk "--no-ci from a subdir sees base workflows" bash -c "[ $PRC = 1 ] && ! grep -qE 'pr (edit|merge)' '$T/calls.log'"; git -C "$O" rm -rq .github; git -C "$O" commit -qm noci; git -C "$O" push -q origin main; rm -rf "$O"
mkdir -p "$R/.github/workflows"; reset; export FAKE_CHECKS_EXIT=1 FAKE_CHECKS_MSG="no checks reported"; mk_pm ciwf medium medium; pmrun "$E" "$R" 1 --no-ci
chk "refuse:ciwf exit1+no edit/merge" bash -c "[ $PRC = 1 ] && ! grep -qE 'pr (edit|merge)' '$T/calls.log'"; rm -rf "$R/.github"; reset
# --- partial diff: gates bound to origin/tmp (C1) must not satisfy premerge; default base does ---
R0=$R H0=$H
for m in partial full; do reset; branch "pd-$m" c1.txt c2.txt; H=$BH; git -C "$R" update-ref refs/remotes/origin/tmp "$(git -C "$R" rev-parse HEAD~1)"
  pm_meta "pd-$m-ev" high high; bo=""; [ "$m" = full ] || bo="--base origin/tmp"
  for g in review security; do claude_out "$g" "$H" pass; pkt "$g" "$H" "${bo#--base }"; gaterun "$E" "$g" high $bo; eval "GRC_$g=$GRC"; done
  pm_reset; pmrun "$E" "$R" 1
  if [ "$m" = partial ]; then chk "partial gates pass" bash -c "[ $GRC_review = 0 ] && [ $GRC_security = 0 ]"
    chk "refuse:partial diff exit1+msg+no edit/merge" bash -c "[ $PRC = 1 ] && grep -q 'bound to a partial or different diff' '$T/pm.out' && ! grep -qE 'pr (edit|merge)' '$T/calls.log'"
  else chk "full-base gates pass+merge" bash -c "[ $GRC_review = 0 ] && [ $GRC_security = 0 ] && [ $PRC = 0 ] && grep -q 'pr merge 1' '$T/calls.log'"; fi; done
R=$R0 H=$H0; reset
claude_out review "$H" '$(touch '"$T"'/pwned)'; new_ev g11; pkt review "$H"; gaterun "$E" review medium
chk "no eval of model output" bash -c "[ ! -e '$T/pwned' ] && python3 -c 'import json,sys;[json.loads(l) for l in open(sys.argv[1])]' '$E/gates.log'"
# --- carry-forward ---
git clone -q "$T/origin.git" "$T/other"
advance_main() { git -C "$T/other" fetch -q origin main; git -C "$T/other" reset -q --hard origin/main
  printf 'm\n' > "$T/other/$1"; git -C "$T/other" add "$1"; git -C "$T/other" commit -qm "main $1"; git -C "$T/other" push -q origin main; }
carry_case() { local n=$1; local c=$2; local v=$3; local d=$T/$n; local br=origin/main; unset FAKE_CHECKS_EXIT FAKE_CSWAP_EXIT; export FAKE_PR_STATE=OPEN
  rm -rf "$d"; git clone -q "$T/origin.git" "$d"
  if [ "${4:-}" = partial ]; then br=origin/tmp; printf 'p0\n' > "$d/p0.txt"; git -C "$d" add p0.txt; git -C "$d" commit -qm p0; git -C "$d" update-ref refs/remotes/origin/tmp HEAD; fi
  [ "$v" = bin ] && printf 'a\000b' > "$d/bin.dat"; printf '%s\n' "$c" > "$d/pr.txt"; git -C "$d" add -A; git -C "$d" commit -qm X; local x=$(git -C "$d" rev-parse HEAD)
  advance_main "$n.txt"; git -C "$d" fetch -q origin main; git -C "$d" merge -q -m "merge main" origin/main
  case "$v" in ws) printf '%s  \n' "$c" > "$d/pr.txt"; git -C "$d" commit -qam ws ;; content) printf 'changed\n' > "$d/pr.txt"; git -C "$d" commit -qam ch ;; bin) printf 'a\000c' > "$d/bin.dat"; git -C "$d" commit -qam bin ;; esac
  local h=$(git -C "$d" rev-parse HEAD); local e=$T/$n-ev; rm -rf "$e"; mkdir -p "$e"
  printf 'Tier: medium\n' > "$e/PLAN.md"; printf 'Tier: medium\n' > "$e/tier-recheck-$h.md"
  printf '{"gate":"plan","tier":"medium"}' > "$e/plan-$x.json"; logline "$e" plan "$x" medium opus "plan-$x.json"
  gatefile "$e" review "$x"; logline "$e" review "$x" medium opus "review-$x.json" ok "$d" "$br"
  : > "$T/body.txt"; echo "$h" > "$T/gh.head"; : > "$T/calls.log"; CASE_E=$e; CASE_D=$d; CASE_H=$h; CASE_X=$x; }
carry_case carry "one" ""; pmrun "$CASE_E" "$CASE_D" 1
chk "carry exit+printed+logged" bash -c "[ $PRC = 0 ] && grep -q 'CARRY review $CASE_X -> $CASE_H' '$T/pm.out' && grep -q '\"status\": \"carry\"' '$CASE_E/gates.log'"
chk "carry no new file+merged" bash -c "test ! -e '$CASE_E/review-$CASE_H.json' && grep -q '\"status\": \"merged\"' '$CASE_E/gates.log'"
carry_case carry_ws "two" ws; pmrun "$CASE_E" "$CASE_D" 1
chk "carry whitespace exit1+no merge" bash -c "[ $PRC = 1 ] && ! grep -q 'pr merge' '$T/calls.log'"
carry_case carry_ch "three" content; pmrun "$CASE_E" "$CASE_D" 1
chk "carry content exit 1" test "$PRC" = 1
carry_case carry_bin "six" bin; pmrun "$CASE_E" "$CASE_D" 1
chk "binary change after X never carried" bash -c "[ $PRC = 1 ] && ! grep -q CARRY '$T/pm.out' && ! grep -q 'pr merge' '$T/calls.log'"
carry_case carry_part "five" "" partial; pmrun "$CASE_E" "$CASE_D" 1
chk "partial-base source never carried" bash -c "[ $PRC = 1 ] && ! grep -q CARRY '$T/pm.out' && ! grep -q 'pr merge' '$T/calls.log'"
carry_case carry_xf "seven" ""; y=$CASE_H; printf '{"structured_output":{"gate":"review","head_sha":"%s","verdict":"fail","summary":"x","findings":[]}}\n' "$y" > "$CASE_E/review-$y.json"
LOGV=fail logline "$CASE_E" review "$y" medium opus "review-$y.json" ok "$CASE_D" origin/main; advance_main xf2.txt
git -C "$CASE_D" fetch -q origin main; git -C "$CASE_D" merge -q -m m2 origin/main; h2=$(git -C "$CASE_D" rev-parse HEAD); printf 'Tier: medium\n' > "$CASE_E/tier-recheck-$h2.md"; echo "$h2" > "$T/gh.head"
pmrun "$CASE_E" "$CASE_D" 1
chk "fail on same diff at other head blocks carry" bash -c "[ $PRC = 1 ] && ! grep -q CARRY '$T/pm.out' && grep -q 'failed on this exact diff' '$T/pm.out'"
carry_case carry_bad "eight" ""; printf '{"structured_output":{"gate":"review","head_sha":"%s","verdict":"pass","summary":"x","findings":["x"]}}\n' "$CASE_H" > "$CASE_E/review-$CASE_H.json"
logline "$CASE_E" review "$CASE_H" medium opus "review-$CASE_H.json" ok "$CASE_D" origin/main; pmrun "$CASE_E" "$CASE_D" 1
chk "malformed gate json prints FAIL" bash -c "[ $PRC = 1 ] && grep -q '^FAIL' '$T/pm.out' && ! grep -q Traceback '$T/pm.out'"
carry_case carry_fail "four" ""; printf '{"structured_output":{"gate":"review","head_sha":"%s","verdict":"fail","summary":"x","findings":[]}}\n' "$CASE_H" > "$CASE_E/review-$CASE_H.json"
logline "$CASE_E" review "$CASE_H" medium opus "review-$CASE_H.json" ok "$CASE_D"; pmrun "$CASE_E" "$CASE_D" 1
chk "fail at head is never carried over" bash -c "[ $PRC = 1 ] && ! grep -q CARRY '$T/pm.out'"
# --- outside mode, no gh ---
reset; new_ev o1; printf 'diff\n' > "$T/change.diff"; K=$(shasum -a 256 "$T/change.diff" | awk '{print $1}')
printf 'Tier: low\n' > "$E/tier-recheck-$K.md"; printf 'Tier: low\n' > "$E/PLAN.md"; gatefile "$E" review "$K"; logline "$E" review "$K" low deepseek-low "review-$K.json"
pmrun "$E" "$R" --diff "$T/change.diff"
chk "outside exit0+no gh" bash -c "[ $PRC = 0 ] && [ \$(grep -c '^gh ' '$T/calls.log') = 0 ]"
# --- outer loop snippet ---
python3 - "$ROOT/SKILL.md" "$T/loop.sh" <<'PY'
import re, sys
t = open(sys.argv[1]).read()
open(sys.argv[2], "w").write(re.search(r"### Outer loop.*?```(?:bash|sh)\n(.*?)```", t, re.S).group(1))
PY
reset; new_ev l1; printf 'STATUS: in-progress - x\n' > "$E/PROGRESS.md"; export LOOP_SCENARIO=done2 SID=s1 EVID="$E"
( bash "$T/loop.sh" > "$T/loop.out" 2>&1 ); LRC=$?
chk "loop done2 exit+calls" bash -c "[ $LRC = 0 ] && [ \$(grep -c '^opencode ' '$T/calls.log') = 2 ]"
chk "loop -s + high model" bash -c "grep -q -- '-s s1' '$T/calls.log' && grep -q 'deepseek-v4.1-flash#high' '$T/calls.log'"
reset; new_ev l2; printf 'STATUS: blocked external - x\n' > "$E/PROGRESS.md"; export LOOP_SCENARIO=blocked SID=s1 EVID="$E"
( bash "$T/loop.sh" > "$T/loop.out" 2>&1 ); LRC=$?
chk "loop blocked 3 calls+reason" bash -c "[ $LRC = 0 ] && [ \$(grep -c '^opencode ' '$T/calls.log') = 3 ] && grep -q 'unchanged' '$T/loop.out'"
reset; new_ev l3; printf 'STATUS: in-progress - x\n' > "$E/PROGRESS.md"; export LOOP_SCENARIO=noop SID=s1 EVID="$E"
( bash "$T/loop.sh" > "$T/loop.out" 2>&1 ); LRC=$?
chk "loop unchanged from baseline stops after 3" bash -c "[ $LRC = 0 ] && [ \$(grep -c '^opencode ' '$T/calls.log') = 3 ] && grep -q 'unchanged' '$T/loop.out'"
printf '\n%s\n' "$([ $FAILED = 0 ] && echo ALL TESTS PASSED || echo TESTS FAILED)"
exit $FAILED
