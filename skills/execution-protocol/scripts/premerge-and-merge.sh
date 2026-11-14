#!/usr/bin/env bash
# premerge-and-merge.sh - execution-protocol pre-merge checks, PR body block and squash merge.
set -euo pipefail
usage() { cat <<'EOF'
usage: EVID=<dir> premerge-and-merge.sh <PR-number> [--no-ci]   (repo mode: checks, PR body block, squash merge;
         --no-ci only for a repo with no CI at all: no workflows and no checks reported)
       EVID=<dir> premerge-and-merge.sh --diff FILE   (outside mode: checks only, no gh)
exit: 0 all checks passed (and merged in repo mode), 1 a check failed (nothing merged), 2 usage
EOF
}
die() { printf 'premerge: %s\n' "$*" >&2; exit 2; }
if [ "${1:-}" = --help ]; then usage; exit 0; fi
[ -n "${EVID:-}" ] || die "EVID is unset"
[ -d "$EVID" ] || die "EVID is not a directory: $EVID"
mode=repo; pr=""; difffile=""; key=""
if [ "${1:-}" = --diff ]; then mode=diff; [ $# -eq 2 ] || { usage >&2; exit 2; }; difffile=$2
  [ -f "$difffile" ] || die "missing diff file: $difffile"; key=$(shasum -a 256 "$difffile" | awk '{print $1}')
else case ${1:-} in ''|*[!0-9]*) usage >&2; exit 2 ;; esac; pr=$1; noci=0
  if [ "${2:-}" = --no-ci ] && [ $# -eq 2 ]; then noci=1; elif [ $# -ne 1 ]; then usage >&2; exit 2; fi
  git rev-parse --git-dir >/dev/null 2>&1 || die "not a git worktree"; key=$(git rev-parse HEAD); fi
TMP=$(mktemp -d "${TMPDIR:-/tmp}/premerge.XXXXXX"); trap 'rm -rf "$TMP"' EXIT; out=$EVID/premerge-$key.txt; : > "$out"; FAILED=0; pass_() { printf 'PASS %s\n' "$1" | tee -a "$out"; }
fail_() { printf 'FAIL %s\n' "$1" | tee -a "$out"; FAILED=1; }
logrow() { printf '{"ts": "%s", "gate": "%s", "tier": "%s", "round": 0, "key": "%s", "route": "", "model": "", "effort": "", "session_id": "", "account": "", "input_tokens": 0, "output_tokens": 0, "cost_usd": 0, "verdict": "", "status": "%s", "reason": "%s", "file": "", "file_sha256": ""}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$2" "${final:-}" "$3" "$1" "$4" >> "$EVID/gates.log"; }
if [ "$mode" = repo ]; then
  if git diff --quiet HEAD; then pass_ "clean tracked tree at $key"; else fail_ "tracked tree is dirty"; fi
  prinfo=$(gh pr view "$pr" --json headRefOid,baseRefName,state 2>>"$out") || prinfo=""
  read -r prhead base prstate <<< "$(printf '%s' "$prinfo" | python3 -I -c 'import json,sys
try: d = json.load(sys.stdin)
except Exception: d = {}
print(d.get("headRefOid", ""), d.get("baseRefName", ""), d.get("state", ""))')"
  if [ -n "$base" ]; then git fetch -q origin "$base" 2>>"$out" || fail_ "cannot fetch origin/$base"; else fail_ "cannot read PR base"; fi
fi
recheck=$EVID/tier-recheck-$key.md; if [ -s "$recheck" ]; then pass_ "found $(basename "$recheck")"; else fail_ "missing tier-recheck-$key.md"; fi
tiers=$(python3 -I - "$EVID/PLAN.md" "$recheck" <<'PY'
import re, sys
def t(p):
    try: return [x.lower() for x in re.findall(r"\bTier:\s*\**\s*(low|medium|high)\b", open(p, errors="replace").read(), re.I)]
    except Exception: return []
r = {"low": 0, "medium": 1, "high": 2}; pl = t(sys.argv[1]); rc = t(sys.argv[2])
pt = max(pl, key=lambda x: r[x]) if pl else ""; rt = rc[-1] if rc else ""
if not pt or not rt: print("none", rt or "none", pt or "none", 0); sys.exit()
f = max([x for x in (pt, rt) if x], key=lambda x: r[x]) if (pt or rt) else "none"
print(f, rt or "none", pt or "none", 1 if rt and pt and r[rt] < r[pt] else 0)
PY
); read -r final rt pt lower <<< "$tiers"
if [ "$final" = none ]; then fail_ "PLAN.md and the tier re-check each need a Tier line (re-check: $rt, PLAN.md: $pt)"; else pass_ "final tier $final"; fi
if [ "$lower" = 1 ]; then fail_ "tier re-check ($rt) is lower than PLAN.md ($pt)"; else pass_ "tier re-check not below PLAN.md"; fi
if [ "$final" = medium ] || [ "$final" = high ]; then
  first=""; if [ "$mode" = repo ] && [ -n "${base:-}" ]; then first=$(git log --format=%at "origin/$base..HEAD" 2>/dev/null | sort -n | head -1); fi
  planout=$(python3 -I - "$EVID" "${first:-}" <<'PY'
import calendar, glob, hashlib, json, os, sys, time
evid, first = sys.argv[1], sys.argv[2]; rows = []; p = os.path.join(evid, "gates.log")
for l in (open(p, errors="replace") if os.path.exists(p) else []):
    try: d = json.loads(l)
    except Exception: d = None
    if isinstance(d, dict): rows.append(d)
def ep(d):
    try: return calendar.timegm(time.strptime(d.get("ts", ""), "%Y-%m-%dT%H:%M:%SZ"))
    except Exception: return None
logged = set(d.get("file_sha256") for d in rows if d.get("gate") == "plan" and d.get("status") == "ok")
pts = [ep(d) for d in rows if d.get("gate") == "plan" and d.get("status") == "ok"]
p0 = min([x for x in pts if x is not None] or [None])
low = any(d.get("gate") == "review" and d.get("status") == "ok" and d.get("tier") == "low" and ep(d) is not None and p0 is not None and ep(d) < p0 for d in rows)
ok = "ok" if any(hashlib.sha256(open(f, "rb").read()).hexdigest() in logged for f in glob.glob(os.path.join(evid, "plan-*.json"))) else "no"
print(ok, "yes" if first and (p0 is None or int(first) < p0) and not low else "no", "yes" if low else "no")
PY
)
  read -r planok early low <<< "$planout"
  if [ "$planok" = ok ]; then pass_ "plan logged for $final"; else fail_ "no logged Opus plan for $final"; fi
  if [ "$early" = yes ]; then fail_ "branch's first commit predates the first logged Opus plan; plan before code"
  elif [ "$low" = yes ] && [ -n "$first" ]; then pass_ "code predates plan: raised from Low"
  elif [ -n "$first" ]; then pass_ "code follows the plan"; fi
fi
if [ "$final" = high ]; then gates="review security"; else gates="review"; fi
gateout=$(python3 -I - "$EVID" "$key" "$final" "${base:-}" $gates <<'PY'
import glob, hashlib, json, os, re, subprocess, sys
evid, key, final, base = sys.argv[1:5]; gates = sys.argv[5:]
rank = {"low": 0, "medium": 1, "high": 2}; rows = []
p = os.path.join(evid, "gates.log")
if os.path.exists(p):
    for l in open(p, errors="replace"):
        try: d = json.loads(l)
        except Exception: d = None
        if isinstance(d, dict): rows.append(d)
def sha(f): return hashlib.sha256(open(f, "rb").read()).hexdigest()
xi = set(); sp = set(); pv = {}
for i, d in [(i, d) for i, d in enumerate(rows) if d.get("status") == "ok"]:
    q = pv.get(d.get("gate"), -1); pv[d.get("gate")] = i
    if q >= 0 and d.get("route") == "opus" and rows[q].get("route") == "fallback" and rows[q].get("key") == d.get("key"): xi.add(i); sp.add(q)
def step8(g, k):
    f = os.path.join(evid, "%s-%s.json" % (g, k))
    if not os.path.exists(f): return None
    try:
        o = json.load(open(f)); o = o.get("structured_output", o); fs = o.get("findings", [])
        if o.get("gate") != g or o.get("verdict") != "pass" or str(o.get("head_sha", "")) != k: return None
        if not isinstance(fs, list) or any(not isinstance(x, dict) or x.get("severity") == "important" for x in fs): return None
    except Exception: return None
    return f
def auth(d):
    f = os.path.join(evid, d.get("file") or "-")
    return d.get("gate") == "epoch" and d.get("status") == "ok" and d.get("verdict") == "pass" and d.get("route") in ("opus", "fallback") and os.path.isfile(f) and d.get("file_sha256") == sha(f) and step8("epoch", d.get("key", "")) is not None
e = max([i + 1 for i, d in enumerate(rows) if auth(d)] or [0])
def git(*a): return subprocess.run(("git",) + a, capture_output=True).stdout
def bound(d, k):
    if not base: return True
    mb = git("merge-base", "origin/" + base, k).decode().strip()
    ds = hashlib.sha256(git("diff", "--no-color", "--full-index", mb, k)).hexdigest() if mb else ""
    return bool(mb) and d.get("base_ref") == "origin/" + base and d.get("base_sha") == mb and d.get("diff_sha256") == ds
def logged(g, k, f, check_bind=True, fb=False):
    for d in rows:
        if d.get("gate") == g and d.get("status") == "ok" and d.get("key") == k and d.get("file_sha256") == sha(f):
            if final == "high" and g in ("review", "security") and d.get("route") != "opus" and not fb: continue
            if check_bind and not bound(d, k): continue
            if rank.get(d.get("tier", ""), -1) < rank.get(final, 0): continue
            if final in ("medium", "high") and d.get("route") not in ("opus", "fallback"): continue
            return True
    return False
for g in gates:
    f = step8(g, key); el = []
    ran = any(d.get("gate") == g and d.get("key") == key and d.get("status") == "ok" for d in rows)
    st = "ok" if f and logged(g, key, f) else "prov" if f and logged(g, key, f, True, True) else "unbound" if f and logged(g, key, f, False, True) else "ran" if ran else "no"
    print("GATE %s %s %s" % (g, st, "-"))
    for d in rows:
        x = d.get("key", "")
        if d.get("gate") == g and d.get("status") == "ok" and x and x != key:
            fx = step8(g, x)
            if fx and logged(g, x, fx) and x not in el: el.append(x)
    if el: print("ELIG %s %s" % (g, " ".join(el)))
    bad = sorted(set(d.get("key", "") for i, d in enumerate(rows) if i not in sp and d.get("gate") == g and d.get("status") == "ok" and d.get("key") and d.get("key") != key and d.get("verdict") != "pass"))
    if bad: print("FAILED %s %s" % (g, " ".join(bad)))
s = max([i + 1 for i, d in enumerate(rows) if d.get("gate") == "plan" and d.get("status") == "ok"] or [0])
for g in gates:
    n = len([d for i, d in enumerate(rows) if i >= s and i not in xi and d.get("gate") == g and d.get("status") == "ok"])
    tot = len([d for i, d in enumerate(rows) if i >= e and i not in xi and d.get("gate") == g and d.get("status") == "ok"])
    print("ROUNDS %s %s %d/%d" % (g, "ok" if n <= 3 and tot <= 6 else "fail", n, tot))
last = {}
for d in rows[s:]:
    if d.get("status") in ("ok", "blocked"): last[d.get("gate")] = d.get("status")
print("BLOCKED %s" % ("fail" if "blocked" in last.values() else "ok"))
ap = len([d for d in rows if d.get("gate") == "epoch" and d.get("status") == "ok" and d.get("verdict") == "pass"])
print("EPOCHS %s %d" % ("fail" if ap > 2 or any(d.get("gate") == "epoch" and d.get("status") == "ok" and d.get("verdict") == "fail" for d in rows[e:]) else "ok", ap))
st = sorted(os.path.basename(f) for f in glob.glob(os.path.join(evid, "*.json")) if re.match(r"(plan|review|security|epoch)-[^.]+\.json$", os.path.basename(f)) and not any(d.get("status") == "ok" and d.get("file_sha256") == sha(f) for d in rows))
print("STRAY %s" % (",".join(st) or "-"))
PY
)
D() { local b; b=$(git merge-base "origin/$base" "$1" 2>/dev/null) || return 1
  git diff --no-color --full-index --binary "$b" "$1" | sed -e 's/^@@ .* @@.*$/@@/'; }
D "$key" > "$TMP/dh" 2>/dev/null || : > "$TMP/dh"
while read -r tag a b c; do
  case $tag in
    GATE) if [ "$b" = ok ]; then pass_ "gate $a passes at $key"; continue; fi
      carried=0; failsame=0
      for y in $(printf '%s\n' "$gateout" | awk -v g="$a" '$1 == "FAILED" && $2 == g { $1 = ""; $2 = ""; print }'); do
        git cat-file -e "$y^{commit}" 2>/dev/null || continue
        if D "$y" > "$TMP/dy" 2>/dev/null && cmp -s "$TMP/dy" "$TMP/dh"; then failsame=1; fi
      done
      if [ "$mode" = repo ] && [ -n "$base" ] && [ -s "$TMP/dh" ] && [ "$b" = no ] && [ "$failsame" = 0 ]; then
        for x in $(printf '%s\n' "$gateout" | awk -v g="$a" '$1 == "ELIG" && $2 == g { $1 = ""; $2 = ""; print }'); do
          git cat-file -e "$x^{commit}" 2>/dev/null || continue
          D "$x" > "$TMP/dx" 2>/dev/null || continue
          if [ -s "$TMP/dx" ] && cmp -s "$TMP/dx" "$TMP/dh"; then pass_ "CARRY $a $x -> $key"; logrow carry "$a" "$key" "carried from $x"; carried=1; break; fi
        done
      fi
      if [ "$carried" = 0 ] && [ "$b" = prov ]; then fail_ "provisional fallback pass; needs Opus on this head ($a-$key.json)"
      elif [ "$carried" = 0 ] && [ "$b" = unbound ]; then fail_ "gate bound to a partial or different diff: $a-$key.json"
      elif [ "$carried" = 0 ] && [ "$failsame" = 1 ]; then fail_ "$a failed on this exact diff at an earlier head; never carried"
      elif [ "$carried" = 0 ]; then fail_ "missing passing $a-$key.json"; fi ;;
    ROUNDS) if [ "$b" = fail ]; then fail_ "$a has too many ok rounds (since plan/total: $c; max 3/6)"; else pass_ "$a rounds ok ($c)"; fi ;;
    BLOCKED) if [ "$a" = fail ]; then fail_ "a blocked gate is logged"; else pass_ "no blocked gate logged"; fi ;;
    EPOCHS) if [ "$a" = fail ]; then fail_ "epoch authorizations: max 2, none denied ($b valid)"; else pass_ "epoch authorizations ok ($b)"; fi ;;
    STRAY) if [ "$a" = - ]; then pass_ "every gate file was written by gate.sh"; else fail_ "gate files not written by gate.sh: $a"; fi ;;
  esac
done <<< "$gateout"
if bash "$(dirname "${BASH_SOURCE[0]}")/implement.sh" --check >>"$out" 2>&1; then pass_ "implementer log: pinned routes only"; else fail_ "implementer log has an off-route row"; fi
[ "$FAILED" = 0 ] || { printf 'premerge: checks failed; nothing merged\n' >&2; exit 1; }
if [ "$mode" = diff ]; then printf 'premerge: checks passed; wrote premerge-%s.txt\n' "$key"; exit 0; fi
if [ "$prstate" = OPEN ] && [ "$prhead" = "$key" ]; then pass_ "PR $pr is OPEN at $key"; else fail_ "PR $pr is not OPEN at $key (head $prhead)"; fi
if [ "$FAILED" = 0 ]; then ck=0; ckout=$(gh pr checks "$pr" 2>&1) || ck=$?
  if [ "$ck" = 0 ]; then pass_ "gh pr checks green"; elif [ "$noci" = 1 ] && [ "$(printf '%s\n' "$ckout" | grep -c .)" = 1 ] && printf '%s' "$ckout" | grep -Eq "^no checks reported on the '.*' branch$" && [ ! -d "$(git rev-parse --show-toplevel)/.github/workflows" ] && [ -z "$(git ls-tree --full-tree "origin/$base" .github/workflows 2>/dev/null)" ]; then pass_ "CI: none (--no-ci)"; else printf '%s\n' "$ckout" >> "$out"; fail_ "gh pr checks not green (exit $ck)"; fi; fi
[ "$FAILED" = 0 ] || exit 1
body=$(gh pr view "$pr" --json body 2>/dev/null | python3 -I -c 'import json,sys
try: print(json.load(sys.stdin).get("body", ""))
except Exception: print("")')
printf '%s' "$body" > "$TMP/body.md"
python3 -I - "$TMP/body.md" "$out" "$key" "$TMP/newbody.md" <<'PY'
import os, sys
src, checks, head, dst = sys.argv[1:5]
body = open(src).read() if os.path.exists(src) else ""
block = "<!-- premerge:start -->\n```\n%s\n```\nhead: %s\n<!-- premerge:end -->" % (open(checks).read().strip(), head)
a, b = "<!-- premerge:start -->", "<!-- premerge:end -->"
if a in body and b in body: body = body[:body.index(a)] + block + body[body.index(b) + len(b):]
else: body = (body.rstrip() + "\n\n" + block + "\n") if body.strip() else block + "\n"
open(dst, "w").write(body)
PY
if gh pr edit "$pr" --body-file "$TMP/newbody.md" 2>>"$out"; then pass_ "PR body updated with premerge block"; else fail_ "gh pr edit failed"; exit 1; fi
if gh pr view "$pr" --json body 2>/dev/null | python3 -I -c 'import re,sys
m = re.search(r"<!-- premerge:start -->(.*?)<!-- premerge:end -->", sys.stdin.read(), re.S)
sys.exit(0 if m and sys.argv[1] in m.group(1) else 1)' "$key"; then pass_ "PR body block contains $key"; else fail_ "PR body block missing $key"; exit 1; fi
if gh pr merge "$pr" --squash --match-head-commit "$key" 2>>"$out"; then pass_ "merged PR $pr at $key"; logrow merged "" "$key" "squash merge"; else fail_ "gh pr merge failed"; exit 1; fi
exit 0
