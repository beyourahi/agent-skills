#!/usr/bin/env bash
# tests/implement-smoke.sh - fakes-only tests for scripts/implement.sh; temp HOME and temp dirs, no model call, no network.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd -P); IM=$ROOT/scripts/implement.sh
T=$(mktemp -d "${TMPDIR:-/tmp}/impl-smoke.XXXXXX"); T=$(cd "$T" && pwd -P); FAILED=0 OUTSIDE=0
trap 'kill "$OUTSIDE" 2>/dev/null; rm -rf "$T"' EXIT
ok() { printf 'ok   impl: %s\n' "$1"; }; bad() { printf 'FAIL impl: %s\n' "$1"; FAILED=1; }
chk() { local d=$1; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
export T HOME=$T/home; EVR=$HOME/.local/state/dev-evidence; E=$EVR/task
mkdir -p "$E" "$T/bin" "$T/oc" "$T/sys"; ln -s "$(command -v python3)" "$T/sys/python3"
cat > "$T/oc/opencode" <<'FAKE'
#!/bin/bash
printf 'opencode %s | cfg=%s free=%s cc=%s xdg=%s state=%s tmp=%s pwd=%s\n' "$*" "${OPENCODE_CONFIG_DIR:-}" "${OPENCODE_DISABLE_FREE_MODELS:-}" "${OPENCODE_DISABLE_CLAUDE_CODE:-}" "${XDG_CACHE_HOME:-}" "${XDG_STATE_HOME:-}" "${TMPDIR:-}" "$PWD" >> "$T/calls.log"
if [ "$1 $2" = "session export" ]; then id=${!#}; m=deepseek-v4.1-flash; e=null
  [ "$id" = ses_probe ] && m=${FAKE_PROBE_MODEL:-$m}; [ "$id" = ses_main ] && [ -n "${FAKE_ERR:-}" ] && e="{\"type\": \"$FAKE_ERR\"}"
  printf '{"info": {"id": "%s", "cost": 0, "tokens": {"input": 5, "output": 2}}, "messages": [{"type": "assistant", "model": {"providerID": "opencode-go", "id": "%s", "variant": "high"}, "error": %s}]}\n' "$id" "$m" "$e"; exit 0; fi
agent=""; while [ $# -gt 0 ]; do [ "$1" = --agent ] && agent=$2; shift; done
id=ses_main; c=${FAKE_COST:-0}; if [ "$agent" = implementer-probe ]; then id=ses_probe; c=0; elif [ -n "${FAKE_ACTION:-}" ]; then eval "$FAKE_ACTION"; fi
printf '{"type": "step_finish", "sessionID": "%s", "part": {"cost": %s}}\n' "$id" "$c"
FAKE
cat > "$T/bin/lsof" <<'FAKE'
#!/bin/bash
[ -e "$T/taint" ] && printf 'p%s\nn%s\n' "$ROOTPID" "$W"; exec /usr/sbin/lsof "$@"
FAKE
cat > "$T/bin/claude" <<'FAKE'
#!/bin/bash
printf 'claude %s\n' "$*" >> "$T/calls.log"; cat > /dev/null; [ -z "${FAKE_SN_ACTION:-}" ] || eval "$FAKE_SN_ACTION"
while [ $# -gt 0 ]; do [ "$1" = --settings ] && printf '%s\n' "$2" > "$T/sn.json"; shift; done
if [ -n "${FAKE_CLAUDE_JSON:-}" ]; then printf '%s\n' "$FAKE_CLAUDE_JSON"
else echo '{"is_error": false, "total_cost_usd": 0.1, "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 3, "outputTokens": 4}}}'; fi
FAKE
chmod +x "$T/bin"/* "$T/oc"/*
ROOTPID=$(pgrep -u 0 -x notifyd || pgrep -u 0 -x syslogd | head -1); export ROOTPID W=$T/wt; R=$T/repo; git init -q -b main "$R"; mkdir -p "$R/src" "$R/tests"; printf 'a\n' > "$R/src/a.txt"; printf "it('x', () => {})\n" > "$R/tests/a.test.js"
printf '{"devDependencies": {}}\n' > "$R/package.json"; printf 'lock\n' > "$R/bun.lock"; printf '*.ok\nignored.txt\n' > "$R/.gitignore"
git -C "$R" add -A; git -C "$R" -c user.name=t -c user.email=t@e commit -qm base; git -C "$R" worktree add -q --detach "$W"
mkplan() { local f=plan-$1.json; printf '{"structured_output": {"gate": "plan", "tier": "medium", "acceptance_criteria": ["ac"], "design": "d-%s", "files": ["src/a.txt", "src/b.txt", "tests/", "docs/", "package.json", "bun.lock", ".github/"], "tests": ["t"]}}\n' "$1" > "$E/$f"
  printf '{"gate": "plan", "status": "ok", "file": "%s", "file_sha256": "%s"}\n' "$f" "$(shasum -a 256 "$E/$f" | awk '{print $1}')" >> "$E/gates.log"; }
fresh() { rm -f "$T/taint" "$T/sn.json"; git -C "$W" reset -q --hard; git -C "$W" clean -qffdx; rm -rf "$E"; mkdir -p "$E"; mkplan p1; : > "$T/calls.log"; rm -f "$T"/boot*
  unset FAKE_ACTION FAKE_COST FAKE_ERR FAKE_PROBE_MODEL FAKE_CLAUDE_JSON FAKE_SN_ACTION IMPL_DEADLINE NOOC NOEV CWD XDG_CACHE_HOME XDG_STATE_HOME; }
im() { local p="$T/bin:$T/sys:/usr/bin:/bin:/usr/sbin:/sbin"; [ -n "${NOOC:-}" ] || p="$T/oc:$p"
  ( cd "${CWD:-$W}" && if [ -z "${NOEV:-}" ]; then export EVID=$E; else unset EVID; fi
    PATH=$p bash "$IM" "$@" ) > "$T/out" 2>&1; IRC=$?; }
nmodel() { grep -cE '^(opencode|claude) ' "$T/calls.log"; }
clean() { [ -z "$(git -C "$W" status --porcelain --untracked-files=all)" ]; }
RES='printf "{\"status\": \"done\", \"tests_run\": [\"t\"]}" > .impl/result.json'
# --- preconditions: exit 2, no opencode/claude call ---
pre() { local d=$1; shift; im "$@"; if [ "$IRC" = 2 ] && [ "$(nmodel)" = 0 ]; then ok "pre: $d"; else bad "pre: $d (rc $IRC, $(nmodel) calls)"; fi; }
fresh; NOEV=1 pre "EVID unset" medium --bootstrap none
fresh; pre "no --bootstrap" medium
fresh; echo x >> "$W/src/a.txt"; pre "dirty file" medium --bootstrap none
fresh; echo x > "$W/new.txt"; pre "untracked file" medium --bootstrap none
fresh; mkdir "$W/.impl"; pre ".impl exists" medium --bootstrap none
fresh; echo '{"status": "start", "step": "main"}' >> "$E/impl.log"; pre "trailing start row" medium --bootstrap none
fresh; : > "$E/gates.log"; pre "no logged plan" medium --bootstrap none
fresh; echo x >> "$E/plan-p1.json"; pre "plan hash mismatch" medium --bootstrap none
fresh; CWD=$R pre "main checkout, not a linked worktree" medium --bootstrap none
fresh; git -C "$R" worktree add -q --detach "$EVR/wtx"; CWD=$EVR/wtx pre "worktree under the evidence root" medium --bootstrap none; git -C "$R" worktree remove --force "$EVR/wtx"
fresh; pre "bad tier" max --bootstrap none
# --- happy DeepSeek run ---
fresh; mkdir -p "$T/xc" "$T/xs" "$T/tmpx"; FAKE_ACTION="echo b > src/a.txt; echo x > ignored.txt; $RES" IMPL_MODEL=x OPENCODE_MODEL=y XDG_CACHE_HOME=$T/xc XDG_STATE_HOME=$T/xs TMPDIR=$T/tmpx im medium --bootstrap none
chk "happy: exit 0" test "$IRC" = 0
chk "happy: probe first" bash -c "grep '^opencode run' '$T/calls.log' | head -1 | grep -q -- '--agent implementer-probe'"
chk "happy: pinned argv, no --auto" bash -c "grep -qF -- 'run --standalone --archived --agent implementer --model opencode-go/deepseek-v4.1-flash#high --format json' '$T/calls.log' && ! grep -q -- '--auto' '$T/calls.log'"
MR=$(grep -- '--agent implementer ' "$T/calls.log")
chk "happy: env inherited (XDG/TMPDIR not redirected)" bash -c "printf '%s' \"\$1\" | grep -qF -- 'cfg=$ROOT/opencode free=1 cc=1 xdg=$T/xc state=$T/xs tmp=$T/tmpx'" _ "$MR"
chk "happy: export cwd is not the worktree" bash -c "grep '^opencode session export' '$T/calls.log' | grep -v -q 'pwd=$W\$' && ! grep '^opencode session export' '$T/calls.log' | grep -q 'pwd=$W\$'"
chk "happy: log start then deepseek done" bash -c "head -1 '$E/impl.log' | grep -q '\"status\": \"start\"' && tail -1 '$E/impl.log' | grep -q '\"route\": \"deepseek\", \"model\": \"opencode-go/deepseek-v4.1-flash\", \"effort\": \"high\", \"status\": \"done\"'"
chk "happy: result in EVID, .impl gone, ignored gone, change kept" bash -c "ls '$E'/impl-main-r1-*-result.json && test ! -e '$W/.impl' && test ! -e '$W/ignored.txt' && grep -q b '$W/src/a.txt'"
chk "no other DeepSeek variant in the scripts" bash -c "! grep -qE '#(medium|low|max)' '$IM' '$ROOT/scripts/implement_lib.py'"
# --- DeepSeek unavailable -> Sonnet ---
fresh; FAKE_PROBE_MODEL=beam-free im medium --bootstrap none
chk "probe free model: no main run, Sonnet high" bash -c "grep -q 'unavailable' '$E/impl.log' && ! grep -q -- '--agent implementer ' '$T/calls.log' && grep -q -- '--effort high' '$T/calls.log' && ! grep -q -- '--effort medium' '$T/calls.log'"
fresh; FAKE_COST=0.01 FAKE_ACTION="$RES" im medium --bootstrap none
chk "step cost above 0: unavailable (cost)" grep -q '"status": "unavailable", "reason": "cost' "$E/impl.log"
fresh; NOOC=1 im medium --bootstrap none; chk "opencode missing: Sonnet" grep -q '^claude ' "$T/calls.log"
fresh; FAKE_ERR=provider.go-limit FAKE_ACTION="echo b > src/a.txt; echo x > ignored.txt" FAKE_SN_ACTION="$RES" im medium --bootstrap "touch $T/boot.marker"
chk "go-limit mid-run: patch, clean, ignored gone, bootstrap, Sonnet same round" bash -c "[ $IRC = 0 ] && grep -q src/a.txt '$E'/impl-*-discarded.patch && test ! -e '$W/ignored.txt' && test -e '$T/boot.marker' && grep -q '\"round\": 1, .*\"route\": \"deepseek\".*\"unavailable\"' '$E/impl.log' && tail -1 '$E/impl.log' | grep -q '\"round\": 1, .*\"route\": \"sonnet\".*\"status\": \"done\"'"
S=$(grep '^claude ' "$T/calls.log")
chk "sonnet argv" bash -c "for a in '--model claude-sonnet-5-5' '--setting-sources  --settings' --strict-mcp-config --no-session-persistence '--allowedTools Bash,Read,Edit,Write,Glob,Grep' '--disallowedTools NotebookEdit,WebFetch,WebSearch,Agent' '--permission-mode dontAsk'; do printf '%s' \"\$1\" | grep -qF -- \"\$a\" || exit 1; done" _ "$S"
N=$(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(sum(1 for p in d["agents"]["implementer"]["permissions"] if p.get("action") == "shell" and p.get("effect") == "deny"))' "$ROOT/opencode/opencode.json")
chk "sonnet permission settings" bash -c "[ \"\$(grep -o 'Bash(' \"\$1\" | wc -l | tr -d ' ')\" = \"\$2\" ] && ! grep -q '\"sandbox\"' \"\$1\" && ! grep -qE 'allowUnixSockets|allowLocalBinding' \"\$1\" && for a in 'Bash(git commit:*)' 'Bash(gh:*)' 'Bash(gh)' 'Bash(curl:*)' 'Bash(git -C:*)' 'Read(**/*.env)' 'Read(**/*.env.*)' 'Edit(**/*.env)' 'Edit(**/*.env.*)'; do grep -qF -- \"\$a\" \"\$1\" || exit 1; done && printf '%s' \"\$3\" | grep -qF -- \"\$(cat \"\$1\")\"" _ "$T/sn.json" "$N" "$S"
fresh; NOOC=1 im high --bootstrap none; chk "sonnet effort high for tier high" grep -q -- '--effort high' "$T/calls.log"
fresh; NOOC=1 im medium --trigger --bootstrap none; chk "sonnet effort high for --trigger" grep -q -- '--effort high' "$T/calls.log"
for t in low medium; do fresh; NOOC=1 im $t --bootstrap none; chk "sonnet effort high for $t tier round 1" bash -c "grep -q -- '--effort high' '$T/calls.log' && ! grep -q -- '--effort medium' '$T/calls.log'"; done
chk "no Sonnet medium branch" bash -c "! grep -q 'seff=medium' '$IM'"
fresh; im medium --bootstrap none; : > "$T/calls.log"; NOOC=1 im medium --bootstrap none; chk "sonnet effort high in round 2" grep -q -- '--effort high' "$T/calls.log"
fresh; NOOC=1 FAKE_CLAUDE_JSON='{"is_error": false, "modelUsage": {"claude-sonnet-5-5": {}, "claude-haiku-5-5": {}}}' FAKE_SN_ACTION="echo z > src/a.txt" im medium --bootstrap none
chk "sonnet extra model: exit 3, clean, unavailable row, no round" bash -c "[ $IRC = 3 ] && [ -z \"\$(git -C '$W' status --porcelain --untracked-files=all)\" ] && tail -1 '$E/impl.log' | grep -q '\"route\": \"sonnet\".*\"unavailable\"' && ! grep -qE '\"status\": \"(done|failed|escalate)\"' '$E/impl.log'"
fresh; NOOC=1 FAKE_CLAUDE_JSON='{"is_error": true, "result": "usage limit"}' im medium --bootstrap none; chk "sonnet is_error: exit 3" test "$IRC" = 3
# --- outcome checks: exit 1, failed, clean tree, patch saved ---
fcase() { fresh; FAKE_ACTION=$2 im medium --bootstrap none
  if [ "$IRC" = 1 ] && tail -1 "$E/impl.log" | grep -q '"status": "failed"' && clean && ls "$E"/impl-*-discarded.patch >/dev/null 2>&1; then ok "fail: $1"; else bad "fail: $1 (rc $IRC)"; fi; }
fcase "no result.json" "echo b > src/a.txt"
fcase "path outside the plan files" "echo b > other.txt; $RES"
fcase ".github file matched only by a directory entry" "mkdir -p .github/workflows; echo x > .github/workflows/x.yml; $RES"
fcase "nested repository under a listed directory" "git init -q tests/sub; echo x > tests/sub/f; $RES"
fcase "FIFO package.json" "rm package.json; mkfifo package.json; $RES"
fcase "lockfile change" "echo y >> bun.lock; $RES"
fcase "new devDependency" "printf '{\"devDependencies\": {\"left-pad\": \"1\"}}\n' > package.json; $RES"
fcase "deleted test file" "rm tests/a.test.js; $RES"
fcase "added skip marker" "echo \"it.skip('y', () => {})\" >> tests/a.test.js; $RES"
fcase "result.json is a symlink" "echo '{\"status\": \"done\"}' > '$T/res.json'; ln -s '$T/res.json' .impl/result.json"
fresh; H0=$(git -C "$W" rev-parse HEAD); FAKE_ACTION="echo c > src/a.txt; git -c user.name=t -c user.email=t@e commit -qam x; $RES" im medium --bootstrap none
chk "HEAD moved: failed, HEAD restored, clean, patch saved" bash -c "[ $IRC = 1 ] && tail -1 '$E/impl.log' | grep -q '\"status\": \"failed\", \"reason\": \"HEAD moved' && [ \"\$(git -C '$W' rev-parse HEAD)\" = '$H0' ] && [ -z \"\$(git -C '$W' status --porcelain --untracked-files=all)\" ] && grep -q src/a.txt '$E'/impl-*-discarded.patch"
fresh; FAKE_ACTION='echo q > .impl/escalation.md; printf "{\"status\": \"escalate\"}" > .impl/result.json' im medium --bootstrap none
chk "escalate: exit 5, escalation in EVID, clean" bash -c "[ $IRC = 5 ] && ls '$E'/escalation-*.md && [ -z \"\$(git -C '$W' status --porcelain)\" ]"
fresh; FAKE_ACTION='printf "{\"status\": \"escalate\"}" > .impl/result.json' im medium --bootstrap none; chk "escalate without escalation.md: exit 1" test "$IRC" = 1
# --- timeout and reaper ---
fresh; IMPL_DEADLINE=2 FAKE_ACTION="sleep 31" im medium --bootstrap none
chk "timeout: failed, reason timeout, nothing left running" bash -c "[ $IRC = 1 ] && tail -1 '$E/impl.log' | grep -q '\"reason\": \"timeout' && ! pgrep -f '^sleep 31\$'"
fresh; (cd "$T" && exec sleep 302) & OUTSIDE=$!; FAKE_ACTION="(sleep 301 &); $RES" im medium --bootstrap none
chk "reaper: detached child in the worktree killed, outside process kept" bash -c "! pgrep -f '^sleep 301\$' && kill -0 $OUTSIDE"
# --- fail closed: a worktree process that survives the reaper gives exit 6 and nothing else runs ---
fresh; FAKE_ACTION="echo planted > src/a.txt; touch '$T/taint'; $RES" im medium --bootstrap "touch $T/boot3"
chk "survivor after main run: exit 6, failed row, no result copy, no patch, no bootstrap, change kept" bash -c "[ $IRC = 6 ] && tail -1 '$E/impl.log' | grep -q '\"route\": \"deepseek\".*\"status\": \"failed\", \"reason\": \"worktree processes survived' && ! ls '$E'/impl-*-result.json '$E'/*discarded.patch && test ! -e '$T/boot3' && grep -q planted '$W/src/a.txt'"
fresh; echo '{"status": "start", "step": "main"}' >> "$E/impl.log"; echo junk > "$W/junk.txt"; touch "$T/taint"; im --discard --bootstrap "touch $T/boot4"
chk "survivor on --discard: exit 6, no reset, no bootstrap" bash -c "[ $IRC = 6 ] && test -e '$W/junk.txt' && test ! -e '$T/boot4'"
fresh; NOOC=1 FAKE_SN_ACTION="touch '$T/taint'; $RES" im medium --bootstrap none
chk "survivor after Sonnet: exit 6, sonnet failed row" bash -c "[ $IRC = 6 ] && tail -1 '$E/impl.log' | grep -q '\"route\": \"sonnet\".*\"status\": \"failed\", \"reason\": \"worktree processes survived'"
# --- rounds, discard, check ---
fresh; PS=$(shasum -a 256 "$E/plan-p1.json" | awk '{print $1}'); for i in 1 2 3; do printf '{"step": "main", "round": %s, "plan_sha256": "%s", "route": "deepseek", "status": "failed"}\n' "$i" "$PS" >> "$E/impl.log"; done
im medium --bootstrap none; chk "round cap: exit 4, no model call" bash -c "[ $IRC = 4 ] && [ \$(grep -cE '^(opencode|claude) ' '$T/calls.log') = 0 ]"
mkplan p2; FAKE_ACTION="$RES" im medium --bootstrap none; chk "a new plan restarts the rounds" test "$IRC" = 0
fresh; echo '{"status": "start", "step": "main"}' >> "$E/impl.log"; echo junk > "$W/junk.txt"; im --discard --bootstrap "touch $T/boot2"
chk "--discard: reset, bootstrap, discarded row" bash -c "[ $IRC = 0 ] && [ -z \"\$(git -C '$W' status --porcelain --untracked-files=all)\" ] && test -e '$T/boot2' && tail -1 '$E/impl.log' | grep -q '\"status\": \"discarded\"'"
FAKE_ACTION="$RES" im medium --bootstrap none; chk "after --discard the next call runs" test "$IRC" = 0
DOK='{"route": "deepseek", "model": "opencode-go/deepseek-v4.1-flash", "effort": "high", "status": "done", "step": "s", "round": 1}'
DUN='{"route": "deepseek", "model": "opencode-go/deepseek-v4.1-flash", "effort": "high", "status": "unavailable", "step": "s", "round": 2}'
SOK='{"route": "sonnet", "model": "claude-sonnet-5-5", "effort": "high", "status": "done", "step": "s", "round": 2}'
ck() { local d=$1 want=$2 rc; shift 2; rm -f "$E/impl.log"; for r in "$@"; do echo "$r" >> "$E/impl.log"; done
  ( EVID=$E bash "$IM" --check ) >/dev/null 2>&1; rc=$?; if [ "$rc" = "$want" ]; then ok "check: $d"; else bad "check: $d (rc $rc)"; fi; }
ck "no log passes" 0; ck "valid log passes" 0 '{"route": "", "status": "start", "step": "s"}' "$DOK" "$DUN" "$SOK" '{"route": "", "status": "discarded"}'
ck "deepseek effort medium fails" 1 "${DOK/\"high\"/\"medium\"}"; ck "deepseek beam-free fails" 1 "${DOK/deepseek-v4.1-flash/beam-free}"
ck "sonnet without an unavailable row fails" 1 "$SOK"; ck "sonnet effort medium fails" 1 "$DUN" "${SOK/\"high\"/\"medium\"}"; ck "route kimi fails" 1 '{"route": "kimi", "status": "done"}'
printf '\n%s\n' "$([ $FAILED = 0 ] && echo 'IMPLEMENT TESTS PASSED' || echo 'IMPLEMENT TESTS FAILED')"
exit $FAILED
