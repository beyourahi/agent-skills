#!/usr/bin/env bash
# gate.sh - run one execution-protocol gate and log exactly one round.
set -euo pipefail
usage() { cat <<'EOF'
usage: EVID=<dir> gate.sh <plan|review|security> <low|medium|high> [--trigger] [--diff FILE] [--base REF]
  plan: key EVID/facts.md sha256; --trigger: effort high; --diff: key FILE sha256
  review/security: the packet must contain the exact diff (FILE, or git diff merge-base(REF)..HEAD; REF default origin/HEAD)
exit: 0 pass (or valid plan), 1 verdict fail, 2 usage/precondition, 3 blocked, 4 round cap, 5 lock wait timed out (rerun)
EOF
}
die() { printf 'gate.sh: %s\n' "$*" >&2; exit 2; }
if [ "${1:-}" = --help ]; then usage; exit 0; fi
[ -n "${EVID:-}" ] || die "EVID is unset"
[ -d "$EVID" ] || die "EVID is not a directory: $EVID"
[ $# -ge 2 ] || { usage >&2; exit 2; }
SKILL=${SKILL:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
gate=$1; tier=$2; shift 2; trigger=0; diff=""; base=""
while [ $# -gt 0 ]; do case $1 in
  --trigger) trigger=1 ;;
  --diff) shift; [ $# -gt 0 ] || die "--diff needs a file"; diff=$1 ;;
  --base) shift; [ $# -gt 0 ] || die "--base needs a ref"; base=$1 ;;
  *) die "unknown argument: $1" ;; esac; shift; done
case $gate in plan|review|security) ;; *) die "bad gate: $gate" ;; esac
case $tier in low|medium|high) ;; *) die "bad tier: $tier" ;; esac
if [ "$tier" = low ] && [ "$gate" != review ]; then die "low tier is only valid with review"; fi
if [ -n "$diff" ]; then [ -f "$diff" ] || die "missing diff file: $diff"; key=$(shasum -a 256 "$diff" | awk '{print $1}')
elif [ "$gate" = plan ]; then [ -s "$EVID/facts.md" ] || die "missing $EVID/facts.md"; key=$(shasum -a 256 "$EVID/facts.md" | awk '{print $1}')
else git rev-parse --git-dir >/dev/null 2>&1 || die "not a git worktree"
  git diff --quiet HEAD || die "tracked tree is dirty; commit or stash first"; key=$(git rev-parse HEAD); fi
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ); round=1; base=${base:-}; mb=""; dsha=""; out=$EVID/$gate-$key.tmp; errf=$EVID/$gate-$key.err; : > "$errf"
log() { local file=${12} sha=""
  if [ -n "$file" ] && [ -f "$EVID/$file" ]; then sha=$(shasum -a 256 "$EVID/$file" | awk '{print $1}'); fi
  python3 -I -c 'import json, sys
k = "ts gate tier round key base_ref base_sha diff_sha256 status route model effort session_id account input_tokens output_tokens cost_usd verdict reason file file_sha256".split()
d = dict(zip(k, sys.argv[1:])); n = lambda x: (float(x) if "." in x else int(x)) if x.replace(".", "", 1).isdigit() else 0
for f in ("round", "input_tokens", "output_tokens", "cost_usd"): d[f] = n(d.get(f, "0"))
print(json.dumps(d))' "$TS" "$gate" "$tier" "$round" "$key" "$base" "$mb" "$dsha" "$@" "$sha" >> "$EVID/gates.log"; }
refuse() { log invalid "" "" "" "" "" 0 0 0 "" "$1" ""; die "$1"; }
TMPD=$(mktemp -d "${TMPDIR:-/tmp}/gate.XXXXXX"); want=$TMPD/diff; trap 'rm -rf "$TMPD"' EXIT; mb=""; dsha=""; cfgchg=0
if [ "$gate" != plan ]; then if [ -n "$diff" ]; then cp "$diff" "$want"; base=""
  else base=${base:-$(git symbolic-ref -q --short refs/remotes/origin/HEAD || echo origin/main)}
    printf '%s' "$base" | grep -Eq '^origin/[A-Za-z0-9._/-]+$' || refuse "--base must be an origin/<branch> ref: $base"
    mb=$(git merge-base "$base" HEAD) || refuse "no merge-base with $base"; git diff --no-color "$mb" HEAD > "$want"; [ -s "$want" ] || refuse "empty diff against $base"
    dsha=$(git diff --no-color --full-index "$mb" HEAD | shasum -a 256 | awk '{print $1}')
    git -c core.quotePath=false diff --name-only -z "$mb" HEAD | tr '\0' '\n' > "$TMPD/names"
    if grep -Eiq '(^|/)(opencode\.jsonc?$|\.opencode/|\.claude/|\.agents/|AGENTS\.md$|CLAUDE\.md$)' "$TMPD/names"; then cfgchg=1; fi
    python3 -I - "$mb" <<'PY' || refuse "a text file shows only as 'Binary files differ' (.gitattributes -diff/binary); drop that attribute before gating"
import subprocess, sys
def blob(rev, p):
    r = subprocess.run(["git", "cat-file", "blob", rev + ":" + p], capture_output=True)
    return r.stdout[:8000] if r.returncode == 0 else None
for e in subprocess.run(["git", "diff", "--no-renames", "--numstat", "-z", sys.argv[1], "HEAD"], capture_output=True, check=True).stdout.split(b"\0"):
    if e.startswith(b"-\t-\t"):
        p = e[4:].decode("utf-8", "surrogateescape"); sides = [b for b in (blob(sys.argv[1], p), blob("HEAD", p)) if b is not None]
        if sides and all(b"\0" not in b for b in sides): sys.exit(1)
PY
  fi; fi
packet=$EVID/packet-$gate.md
if [ ! -s "$packet" ]; then log invalid "" "" "" "" "" 0 0 0 "" "missing packet" ""; die "missing $packet"; fi
if ! python3 -I - "$packet" "$gate" "$key" "$EVID/facts.md" "$want" "$mb" <<'PY'
import sys
p, g, k, f, w, b = sys.argv[1:7]; t = open(p, encoding="utf-8", errors="replace").read()
if g == "plan": sys.exit(0 if open(f, encoding="utf-8", errors="replace").read().strip() in t else 1)
sys.exit(0 if k in t and b in t and "Gate: " + g in t and open(w, encoding="utf-8", errors="replace").read().strip() in t else 1)
PY
then log invalid "" "" "" "" "" 0 0 0 "" "packet does not bind key, gate and exact diff" ""; die "packet must contain the key, the merge-base, 'Gate: $gate' and the exact diff"; fi
vals=$(python3 -I - "$EVID/gates.log" "$gate" <<'PY'
import json, os, sys
p, g = sys.argv[1:3]; rows = []; s = 0
for l in (open(p, errors="replace") if os.path.exists(p) else []):
    try: d = json.loads(l)
    except Exception: d = None
    if isinstance(d, dict): rows.append(d)
for i, d in enumerate(rows):
    if d.get("gate") == "plan" and d.get("status") == "ok": s = i + 1
n = len([d for d in rows[s:] if d.get("gate") == g and d.get("status") == "ok"])
tot = len([d for d in rows if d.get("gate") == g and d.get("status") == "ok"])
print(n + 1, n, 2 if g != "plan" and tot >= 6 else 1 if n >= 3 else 0)
PY
); read -r round ok cap <<< "$vals"
if [ "$cap" = 2 ]; then log blocked "" "" "" "" "" 0 0 0 "" "round cap after re-plan" ""; exit 3; fi
if [ "$cap" = 1 ]; then log cap "" "" "" "" "" 0 0 0 "" "3 ok rounds reached; re-plan required" ""; exit 4; fi
effort=medium; if [ "$tier" = high ] || [ "$trigger" = 1 ]; then effort=high; fi
if [ "$gate" = plan ]; then schema=plan-schema.json; else schema=review-schema.json; fi
run_claude() { local rc=0; set +e
  claude -p --model claude-opus-5-5 --effort "$1" --setting-sources "" --strict-mcp-config --mcp-config '{"mcpServers":{}}' --permission-mode dontAsk \
    --allowedTools "Read,Grep,Glob" --disallowedTools "Bash,Edit,Write,NotebookEdit,Agent,WebFetch,WebSearch" \
    --append-system-prompt "$(cat "$SKILL/opus-gate-brief.md")" --output-format json --json-schema "$(cat "$SKILL/$schema")" < "$packet" > "$out" 2>>"$errf"
  rc=$?; set -e; return $rc; }
classify() { python3 -I - "$out" "$gate" "$key" "$1" <<'PY'
import json, sys
p, g, k, rc = sys.argv[1:5]
try:
    d = json.load(open(p)); o = d.get("structured_output") or {}; mu = d.get("modelUsage") or {}
    t = next((v for x, v in mu.items() if "claude-opus-5-5" in x), {}) if isinstance(mu, dict) else {}
    e = bool(d.get("is_error")); r = str(d.get("result") or "").lower()
    ok = rc == "0" and not e and isinstance(o, dict) and bool(o) and bool(t) and o.get("gate") == g and (g == "plan" or str(o.get("head_sha", "")) == k)
    v = ["ok" if ok else "limit" if e and ("limit" in r or "usage" in r) else "invalid", d.get("session_id", ""), t.get("inputTokens", 0), t.get("outputTokens", 0), d.get("total_cost_usd", 0), o.get("verdict", "")]
except Exception: v = ["invalid", "", 0, 0, 0, ""]
print("\n".join("%s=%s" % (n, " ".join(str(x).split())) for n, x in zip(["KIND", "SESSION", "INTOK", "OUTOK", "COST", "VERDICT"], v)))
PY
}
attempt() { local rc=0; if run_claude "$1"; then rc=0; else rc=$?; fi; classify "$rc" > "$EVID/$gate-$key.cls"
  KIND=""; SESSION=""; INTOK=0; OUTOK=0; COST=0; VERDICT=""; while IFS='=' read -r k v; do case $k in KIND) KIND=$v;; SESSION) SESSION=$v;; INTOK) INTOK=$v;; OUTOK) OUTOK=$v;; COST) COST=$v;; VERDICT) VERDICT=$v;; esac; done < "$EVID/$gate-$key.cls"; rm -f "$EVID/$gate-$key.cls"
  if [ "$KIND" != ok ]; then log invalid opus claude-opus-5-5 "$1" "$SESSION" "$account" "$INTOK" "$OUTOK" "$COST" "" "opus attempt: $KIND" ""; fi; }
extract() { python3 -I - "$1" "$2" "$gate" "$key" "$SKILL/$schema" <<'PY'
import json, re, sys
raw, out, g, k, sc = sys.argv[1:6]; parts = []; bad = False
for l in open(raw, errors="replace"):
    try: e = json.loads(l)
    except Exception: continue
    if isinstance(e, dict):
        bad = bad or e.get("type") == "error"
        if e.get("type") == "text": parts.append((e.get("part") or {}).get("text") or e.get("text") or "")
t = "\n".join(parts); dec = json.JSONDecoder(); best = None
for m in re.finditer(r"\{", t):
    try: o, j = dec.raw_decode(t, m.start())
    except Exception: continue
    if isinstance(o, dict) and (not best or j > best[1]): best = (m.start(), j)
if bad or not best: sys.exit(1)
s, e2 = best; o = json.loads(t[s:e2])
if [x for x in json.load(open(sc)).get("required", []) if x not in o] or o.get("gate") != g: sys.exit(1)
if g == "plan":
    if o.get("tier") not in ("medium", "high"): sys.exit(1)
elif o.get("verdict") not in ("pass", "fail") or str(o.get("head_sha", "")) != k: sys.exit(1)
open(out, "w").write(t[s:e2])
PY
}
verdict_of() { python3 -I -c 'import json,sys
try: print((json.load(open(sys.argv[1])).get("structured_output") or json.load(open(sys.argv[1]))).get("verdict", ""))
except Exception: print("")' "$1"; }
fallback() { local prompt=$EVID/fallback-prompt-$gate.md raw=$EVID/$gate-$key.raw.jsonl rc=0
  if [ "$cfgchg" = 1 ]; then log blocked fallback deepseek-v4.1-flash#max max "" "$account" 0 0 0 "" "agent config in diff" ""; exit 3; fi
  { cat "$SKILL/opus-gate-brief.md"; echo; cat "$packet"; echo; echo 'Return one final JSON object matching this schema:'; cat "$SKILL/$schema"; } > "$prompt"
  set +e
  OPENCODE_CONFIG_CONTENT="$(cat "$SKILL/opencode-gate.json")" opencode run --standalone --agent gate --model 'opencode-go/deepseek-v4.1-flash#max' --format json -f "$prompt" "Run the gate in the attached file." > "$raw" 2>>"$errf"
  rc=$?; set -e
  if [ $rc = 0 ] && extract "$raw" "$out"; then VERDICT=$(verdict_of "$out"); return 0; fi
  if [ "${1:-}" != last ]; then log invalid fallback deepseek-v4.1-flash#max max "" "$account" 0 0 0 "" "fallback attempt: exit $rc or no valid JSON" ""; fi; return 1; }
finish() { local file=$gate-$key.json; mv -f "$out" "$EVID/$file"
  log ok "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "${9}" "${10}" "$file"
  printf '%s %s route=%s verdict=%s file=%s\n' "$gate" "$key" "$1" "${9}" "$file"
  if [ "$gate" = plan ] || [ "${9}" = pass ]; then exit 0; fi; exit 1; }
if [ "$gate" = review ] && [ "$tier" = low ]; then
  if [ "$cfgchg" = 1 ]; then log invalid deepseek-low "" "" "" "" 0 0 0 "" "agent config in diff" ""; die "agent config changed: tier must be >= medium"; fi
  raw=$EVID/$gate-$key.raw.jsonl
  if ! { opencode run --agent plan --model 'opencode-go/deepseek-v4.1-flash#high' --format json "$(cat "$packet")" > "$raw" 2>>"$errf" && extract "$raw" "$out"; }; then
    log invalid deepseek-low deepseek-v4.1-flash#high high "" "" 0 0 0 "" "low review attempt: no valid JSON" ""
    opencode run --agent plan --model 'opencode-go/deepseek-v4.1-flash#high' --format json "$(cat "$packet")" > "$raw" 2>>"$errf" && extract "$raw" "$out" || { log blocked deepseek-low deepseek-v4.1-flash#high high "" "" 0 0 0 "" "low review returned no valid JSON" ""; exit 3; }
  fi
  finish deepseek-low deepseek-v4.1-flash#high high "" "" 0 0 0 "$(verdict_of "$out")" ""
fi
state_dir=${XDG_STATE_HOME:-$HOME/.local/state}/dev-evidence; mkdir -p "$state_dir"; lock=$state_dir/.opus-gate.lock; locked=0
acquire() { local i=0 pid=""; while [ $i -lt 1800 ]; do
    if mkdir "$lock" 2>/dev/null; then printf '%s\n' $$ > "$lock/pid"; locked=1; return 0; fi
    pid=$(cat "$lock/pid" 2>/dev/null || true)
    age=$(python3 -I -c 'import os,sys,time;print(int(time.time()-os.stat(sys.argv[1]).st_mtime))' "$lock" 2>/dev/null || echo 0)
    if { [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; } || { [ -z "$pid" ] && [ "$age" -gt 60 ]; }; then
      [ "$(cat "$lock/pid" 2>/dev/null || true)" = "$pid" ] && rm -rf "$lock"; continue; fi
    sleep 1; i=$((i + 1)); done; return 1; }
trap 'rm -rf "$TMPD"; if [ "$locked" = 1 ]; then rm -rf "$lock"; fi' EXIT
if ! acquire; then log invalid opus claude-opus-5-5 "$effort" "" "" 0 0 0 "" "opus lock wait timed out; rerun" ""; exit 5; fi
account=signed-in; opus_unavailable=0; reason=""
acct() { printf '%s' "$1" | python3 -I -c 'import json,sys
t = sys.stdin.read(); b = {}
for l in [t] + t.splitlines():
    try: d = json.loads(l)
    except Exception: continue
    if isinstance(d, dict) and isinstance(d.get("active"), dict): b = d["active"]
print(b.get("number", "signed-in"))'; }
if command -v cswap >/dev/null 2>&1; then
  rotate() { local rc=0; CSWAP_RAW=$(cswap auto --once --model Opus --json 2>/dev/null) || rc=$?; return $rc; }
  if rotate; then rc=0; else rc=$?; fi
  if [ "$rc" = 1 ]; then sleep "${GATE_CSWAP_RETRY:-30}"; if rotate; then rc=0; else rc=$?; fi; fi
  if [ "$rc" = 3 ]; then opus_unavailable=1; reason="opus unavailable (cswap exit 3)"; else account=$(acct "${CSWAP_RAW:-}"); fi
fi
if [ "$opus_unavailable" = 1 ]; then
  fallback || fallback last || { log blocked fallback deepseek-v4.1-flash#max max "" "$account" 0 0 0 "" "$reason" ""; exit 3; }
  finish fallback deepseek-v4.1-flash#max max "" "$account" 0 0 0 "$VERDICT" "$reason"
fi
attempt "$effort"
if [ "$KIND" = limit ]; then cswap switch --strategy next-available --json >/dev/null 2>&1 || true
  account=$(acct "$(cswap status --json 2>/dev/null || true)"); attempt "$effort"
  if [ "$KIND" = invalid ]; then attempt "$effort"; fi
elif [ "$KIND" = invalid ]; then attempt "$effort"; fi
case $KIND in limit) reason="usage limit after account switch" ;; invalid) reason="opus run invalid after retry" ;; esac
if [ "$KIND" = ok ]; then finish opus claude-opus-5-5 "$effort" "$SESSION" "$account" "$INTOK" "$OUTOK" "$COST" "$VERDICT" ""; fi
fallback || fallback last || { log blocked fallback deepseek-v4.1-flash#max max "" "$account" 0 0 0 "" "$reason" ""; exit 3; }
finish fallback deepseek-v4.1-flash#max max "" "$account" 0 0 0 "$VERDICT" "$reason"
