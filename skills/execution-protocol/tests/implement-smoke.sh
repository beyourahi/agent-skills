#!/usr/bin/env bash
# tests/implement-smoke.sh - fakes-only tests for scripts/implement.sh, plus one real sandbox-exec test of scripts/implementer.sb; temp HOME, no model call, no network.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd -P); IM=$ROOT/scripts/implement.sh
# /tmp, not $TMPDIR: the per-user temp dir is writable inside the sandbox, so denial tests must live outside it.
T=$(mktemp -d /tmp/impl-smoke.XXXXXX); T=$(cd "$T" && pwd -P); FAILED=0; OUTSIDE=0
trap 'kill "$OUTSIDE" 2>/dev/null; pkill -f "time.sleep.306."; chmod -R u+w "$T" 2>/dev/null; rm -rf "$T"' EXIT
ok() { printf 'ok   impl: %s\n' "$1"; }; bad() { printf 'FAIL impl: %s\n' "$1"; FAILED=1; }
chk() { local d=$1; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
export T HOME=$T/home; EVR=$HOME/.local/state/dev-evidence; E=$EVR/task
mkdir -p "$E" "$T/bin" "$T/oc" "$T/sys" "$T/ro"; chmod 555 "$T/ro"; ln -s "$(command -v python3)" "$T/sys/python3"
cat > "$T/bin/sandbox-exec" <<'FAKE'
#!/bin/bash
printf 'sbx %s\n' "$*" >> "$T/calls.log"
while [ $# -gt 0 ]; do case $1 in -f|-D) shift 2 ;; *) break ;; esac; done
[ "${FAKE_SBX_OPEN:-0}" = 1 ] || export HOME="$T/ro"
exec "$@"
FAKE
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
if [ -n "${FAKE_CLAUDE_JSON:-}" ]; then printf '%s\n' "$FAKE_CLAUDE_JSON"
else echo '{"is_error": false, "total_cost_usd": 0.1, "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 3, "outputTokens": 4}}}'; fi
FAKE
chmod +x "$T/bin"/* "$T/oc"/*
ROOTPID=$(pgrep -u 0 -x notifyd || pgrep -u 0 -x syslogd | head -1); export ROOTPID W=$T/wt; R=$T/repo; git init -q -b main "$R"; mkdir -p "$R/src" "$R/tests"; printf 'a\n' > "$R/src/a.txt"; printf "it('x', () => {})\n" > "$R/tests/a.test.js"
printf '{"devDependencies": {}}\n' > "$R/package.json"; printf 'lock\n' > "$R/bun.lock"; printf '*.ok\nignored.txt\n' > "$R/.gitignore"
git -C "$R" add -A; git -C "$R" -c user.name=t -c user.email=t@e commit -qm base; git -C "$R" worktree add -q --detach "$W"
mkplan() { local f=plan-$1.json; printf '{"structured_output": {"gate": "plan", "tier": "medium", "acceptance_criteria": ["ac"], "design": "d-%s", "files": ["src/a.txt", "src/b.txt", "tests/", "docs/", "package.json", "bun.lock", ".github/"], "tests": ["t"]}}\n' "$1" > "$E/$f"
  printf '{"gate": "plan", "status": "ok", "file": "%s", "file_sha256": "%s"}\n' "$f" "$(shasum -a 256 "$E/$f" | awk '{print $1}')" >> "$E/gates.log"; }
fresh() { rm -f "$T/taint"; git -C "$W" reset -q --hard; git -C "$W" clean -qffdx; rm -rf "$E"; mkdir -p "$E"; mkplan p1; : > "$T/calls.log"; rm -f "$T"/boot*
  unset FAKE_ACTION FAKE_COST FAKE_ERR FAKE_PROBE_MODEL FAKE_SBX_OPEN FAKE_CLAUDE_JSON FAKE_SN_ACTION IMPL_DEADLINE NOOC NOEV CWD; }
im() { local p="$T/bin:$T/sys:/usr/bin:/bin:/usr/sbin:/sbin"; [ -n "${NOOC:-}" ] || p="$T/oc:$p"
  ( cd "${CWD:-$W}" && if [ -z "${NOEV:-}" ]; then export EVID=$E; else unset EVID; fi
    PATH=$p IMPL_SANDBOX_EXEC="$T/bin/sandbox-exec" bash "$IM" "$@" ) > "$T/out" 2>&1; IRC=$?; }
nmodel() { grep -cE '^(sbx|opencode|claude) ' "$T/calls.log"; }
clean() { [ -z "$(git -C "$W" status --porcelain --untracked-files=all)" ]; }
RES='printf "{\"status\": \"done\", \"tests_run\": [\"t\"]}" > .impl/result.json'
# --- preconditions: exit 2, no sandbox/opencode/claude call ---
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
fresh; FAKE_ACTION="echo b > src/a.txt; echo x > ignored.txt; $RES" IMPL_MODEL=x OPENCODE_MODEL=y im medium --bootstrap none
chk "happy: exit 0" test "$IRC" = 0
chk "happy: probe first" bash -c "grep '^opencode run' '$T/calls.log' | head -1 | grep -q -- '--agent implementer-probe'"
chk "happy: sandbox argv" grep -qF -- "-f $ROOT/scripts/implementer.sb -D HOME=$HOME -D WT=$W" "$T/calls.log"
chk "happy: pinned argv, no --auto" bash -c "grep -qF -- 'run --standalone --archived --agent implementer --model opencode-go/deepseek-v4.1-flash#high --format json' '$T/calls.log' && ! grep -q -- '--auto' '$T/calls.log'"
UTR=$(cd "$(getconf DARWIN_USER_TEMP_DIR)" && pwd -P); MR=$(grep -- '--agent implementer ' "$T/calls.log"); RD=$(grep -o -- "-D RUN=[^ ]*" "$T/calls.log" | head -1 | cut -d= -f2)
chk "happy: env, cache/state/tmp under RUN in the user temp dir" bash -c "case '$RD' in '$UTR'/*) ;; *) exit 1 ;; esac; printf '%s' \"\$1\" | grep -qF 'cfg=$ROOT/opencode free=1 cc=1 xdg=$RD/cache state=$RD/state tmp=$RD/tmp'" _ "$MR"
chk "happy: export cwd is not the worktree" bash -c "grep '^opencode session export' '$T/calls.log' | grep -v -q 'pwd=$W\$' && ! grep '^opencode session export' '$T/calls.log' | grep -q 'pwd=$W\$'"
chk "happy: log start then deepseek done" bash -c "head -1 '$E/impl.log' | grep -q '\"status\": \"start\"' && tail -1 '$E/impl.log' | grep -q '\"route\": \"deepseek\", \"model\": \"opencode-go/deepseek-v4.1-flash\", \"effort\": \"high\", \"status\": \"done\"'"
chk "happy: result in EVID, .impl gone, ignored gone, change kept" bash -c "ls '$E'/impl-main-r1-*-result.json && test ! -e '$W/.impl' && test ! -e '$W/ignored.txt' && grep -q b '$W/src/a.txt'"
chk "no other DeepSeek variant in the scripts" bash -c "! grep -qE '#(medium|low|max)' '$IM' '$ROOT/scripts/implement_lib.py'"
# --- DeepSeek unavailable -> Sonnet ---
fresh; FAKE_SBX_OPEN=1 im medium --bootstrap none
chk "unconfined sandbox: unavailable, check file removed, Sonnet called" bash -c "grep -q '\"route\": \"deepseek\".*\"status\": \"unavailable\", \"reason\": \"sandbox' '$E/impl.log' && ! ls '$HOME'/.impl-sbx-* && grep -q '^claude ' '$T/calls.log'"
fresh; FAKE_PROBE_MODEL=beam-free im medium --bootstrap none
chk "probe free model: no main run, Sonnet high" bash -c "grep -q 'unavailable' '$E/impl.log' && ! grep -q -- '--agent implementer ' '$T/calls.log' && grep -q -- '--effort high' '$T/calls.log' && ! grep -q -- '--effort medium' '$T/calls.log'"
fresh; FAKE_COST=0.01 FAKE_ACTION="$RES" im medium --bootstrap none
chk "step cost above 0: unavailable (cost)" grep -q '"status": "unavailable", "reason": "cost' "$E/impl.log"
fresh; NOOC=1 im medium --bootstrap none; chk "opencode missing: Sonnet" grep -q '^claude ' "$T/calls.log"
fresh; FAKE_ERR=provider.go-limit FAKE_ACTION="echo b > src/a.txt; echo x > ignored.txt" FAKE_SN_ACTION="$RES" im medium --bootstrap "touch $T/boot.marker"
chk "go-limit mid-run: patch, clean, ignored gone, bootstrap, Sonnet same round" bash -c "[ $IRC = 0 ] && grep -q src/a.txt '$E'/impl-*-discarded.patch && test ! -e '$W/ignored.txt' && test -e '$T/boot.marker' && grep -q '\"round\": 1, .*\"route\": \"deepseek\".*\"unavailable\"' '$E/impl.log' && tail -1 '$E/impl.log' | grep -q '\"round\": 1, .*\"route\": \"sonnet\".*\"status\": \"done\"'"
S=$(grep '^claude ' "$T/calls.log")
chk "sonnet argv" bash -c "for a in '--model claude-sonnet-5-5' '--setting-sources  --settings' --strict-mcp-config --no-session-persistence '--allowedTools Bash' 'Read,Grep,Glob,Edit,Write,NotebookEdit,WebFetch' '--permission-mode dontAsk'; do printf '%s' \"\$1\" | grep -qF -- \"\$a\" || exit 1; done" _ "$S"
chk "sonnet sandbox settings" bash -c "! printf '%s' \"\$1\" | grep -qE 'allowUnixSockets|allowAllUnixSockets|allowLocalBinding' && for a in '\"enabled\": true' '\"failIfUnavailable\": true' '\"allowUnsandboxedCommands\": false' '\"allowedDomains\": []' '$W/.git' '$HOME/.ssh' '$EVR'; do printf '%s' \"\$1\" | grep -qF -- \"\$a\" || exit 1; done; ! grep -q cswap '$T/calls.log'" _ "$S"
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
fresh; FAKE_ACTION='echo q > .impl/escalation.md; printf "{\"status\": \"escalate\"}" > .impl/result.json' im medium --bootstrap none
chk "escalate: exit 5, escalation in EVID, clean" bash -c "[ $IRC = 5 ] && ls '$E'/escalation-*.md && [ -z \"\$(git -C '$W' status --porcelain)\" ]"
fresh; FAKE_ACTION='printf "{\"status\": \"escalate\"}" > .impl/result.json' im medium --bootstrap none; chk "escalate without escalation.md: exit 1" test "$IRC" = 1
# --- timeout and reaper ---
fresh; IMPL_DEADLINE=2 FAKE_ACTION="sleep 31" im medium --bootstrap none
chk "timeout: failed, reason timeout, nothing left running" bash -c "[ $IRC = 1 ] && tail -1 '$E/impl.log' | grep -q '\"reason\": \"timeout' && ! pgrep -f '^sleep 31\$'"
fresh; (cd "$T" && exec sleep 302) & OUTSIDE=$!; FAKE_ACTION="(sleep 301 &); $RES" im medium --bootstrap none
chk "reaper: detached child in the worktree killed, outside process kept" bash -c "! pgrep -f '^sleep 301\$' && kill -0 $OUTSIDE"
# --- fail closed: a confined process that survives the reaper gives exit 6 and nothing else runs ---
fresh; FAKE_ACTION="echo planted > src/a.txt; touch '$T/taint'; $RES" im medium --bootstrap "touch $T/boot3"
chk "survivor after main run: exit 6, failed row, no result copy, no patch, no bootstrap, change kept" bash -c "[ $IRC = 6 ] && tail -1 '$E/impl.log' | grep -q '\"route\": \"deepseek\".*\"status\": \"failed\", \"reason\": \"confined processes survived' && ! ls '$E'/impl-*-result.json '$E'/*discarded.patch && test ! -e '$T/boot3' && grep -q planted '$W/src/a.txt'"
fresh; echo '{"status": "start", "step": "main"}' >> "$E/impl.log"; echo junk > "$W/junk.txt"; touch "$T/taint"; im --discard --bootstrap "touch $T/boot4"
chk "survivor on --discard: exit 6, no reset, no bootstrap" bash -c "[ $IRC = 6 ] && test -e '$W/junk.txt' && test ! -e '$T/boot4'"
fresh; NOOC=1 FAKE_SN_ACTION="touch '$T/taint'; $RES" im medium --bootstrap none
chk "survivor after Sonnet: exit 6, sonnet failed row" bash -c "[ $IRC = 6 ] && tail -1 '$E/impl.log' | grep -q '\"route\": \"sonnet\".*\"status\": \"failed\", \"reason\": \"confined processes survived'"
# --- rounds, discard, check ---
fresh; PS=$(shasum -a 256 "$E/plan-p1.json" | awk '{print $1}'); for i in 1 2 3; do printf '{"step": "main", "round": %s, "plan_sha256": "%s", "route": "deepseek", "status": "failed"}\n' "$i" "$PS" >> "$E/impl.log"; done
im medium --bootstrap none; chk "round cap: exit 4, no model call" bash -c "[ $IRC = 4 ] && [ \$(grep -cE '^(sbx|opencode|claude) ' '$T/calls.log') = 0 ]"
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
# --- real sandbox: scripts/implementer.sb enforces the write boundary (no model) ---
if [ -x /usr/bin/sandbox-exec ]; then
  RH=$T/rh; O=$RH/.local/share/opencode; mkdir -p "$RH/.ssh" "$O/bin" "$O/log" "$O/snapshot/p" "$RH/.cache/opencode" "$RH/.local/state/opencode" "$RH/.local/state/dev-evidence"
  echo k > "$RH/.ssh/id_test"; echo e > "$RH/.local/state/dev-evidence/x"; echo o > "$T/outside.txt"; fresh
  UT=$(cd "$(getconf DARWIN_USER_TEMP_DIR)" && pwd -P); UC=$(cd "$(getconf DARWIN_USER_CACHE_DIR)" && pwd -P)
  RN=$T/run; mkdir -p "$RN" "$T/sock"; SA="-D HOME=$RH -D WT=$W -D OCDATA=$O -D UTMP=$UT -D UCACHE=$UC -D EVROOT=$RH/.local/state/dev-evidence -D RUN=$RN"
  sb() { /usr/bin/sandbox-exec -f "$ROOT/scripts/implementer.sb" -D "HOME=$RH" -D "WT=$W" -D "OCDATA=$O" -D "RUN=$RN" \
    -D "UTMP=$UT" -D "UCACHE=$UC" -D "EVROOT=$RH/.local/state/dev-evidence" /bin/sh -c "$1" >/dev/null 2>&1; }
  for c in "echo x > '$W/ok.txt'" "echo x >> '$W/.gitignore'" "echo x > '$O/opencode.db-wal'" "echo x > '$O/log/l'" "python3 -c \"open('$W/py.txt','w')\""; do chk "real sandbox allows: $c" sb "$c"; done
  for c in "echo x >> '$W/.git'" "echo x >> '$R/.git/config'" "echo x > '$R/.git/hooks/h'" "echo x > '$RH/x'" "echo x > '$RH/.local/share/opencode/bin/rg'" \
    "echo x > '$RH/.cache/opencode/x'" "echo x > '$O/snapshot/p/config'" "echo x > '$O/mcp-auth.json'" "echo x > '$O/opencode.dbx'" "echo x > '$RH/.local/state/opencode/x'" "echo x > '$RH/.local/state/dev-evidence/y'" "ln '$T/outside.txt' '$W/hl'" "python3 -c \"open('$RH/py.txt','w')\"" \
    "cat '$RH/.ssh/id_test'" "cat '$RH/.local/state/dev-evidence/x'"; do if sb "$c"; then bad "real sandbox denies: $c"; else ok "real sandbox denies: $c"; fi; done
  /usr/bin/sandbox-exec -f "$ROOT/scripts/implementer.sb" $SA /bin/sh -c 'cd /; exec sleep 304' & SBP=$!; (cd /; exec sleep 305) & UNP=$!; sleep 1; PL=" $(cd / && python3 -I "$ROOT/scripts/implement_lib.py" pids "$W" $$) "
  chk "pids: sandboxed process outside the worktree found, unconfined one not" bash -c "case '$PL' in *' $SBP '*) ;; *) exit 1 ;; esac; case '$PL' in *' $UNP '*) exit 1 ;; esac"; kill $SBP $UNP 2>/dev/null
  /usr/bin/sandbox-exec -f "$ROOT/scripts/implementer.sb" $SA /usr/bin/python3 -I -c 'import os,time; os.fork() and os._exit(0); os.setsid(); os.chdir("/"); time.sleep(306)'; sleep 0.5
  chk "reap: setsid'd confined process gone, exit 0" bash -c "(cd / && python3 -I '$ROOT/scripts/implement_lib.py' reap '$W' $$) && sleep 0.3 && ! pgrep -f 'time.sleep\(306\)'"
  lsock() { python3 -I -c 'import socket,sys,os; p=sys.argv[1]; os.path.exists(p) and os.remove(p); s=socket.socket(socket.AF_UNIX); s.bind(p); s.listen(5); s.settimeout(30); s.accept()' "$1" & sleep 0.3; }
  conn() { sb "python3 -I -c \"import socket; socket.socket(socket.AF_UNIX).connect('$1')\""; }
  lsock "$T/sock/s"; if conn "$T/sock/s"; then bad "real sandbox denies: unix socket outside WT and RUN"; else ok "real sandbox denies: unix socket outside WT and RUN"; fi
  lsock "$W/s"; chk "real sandbox allows: unix socket in WT" conn "$W/s"; lsock "$RN/s"; chk "real sandbox allows: unix socket in RUN" conn "$RN/s"; /bin/rm -f "$W/s"
  if sb "open -g -a Finder"; then bad "real sandbox denies: open (LaunchServices)"; else ok "real sandbox denies: open (LaunchServices)"; fi
  if sb "osascript -e 'tell application \"Finder\" to get name'"; then bad "real sandbox denies: osascript"; else ok "real sandbox denies: osascript"; fi
  LB=impl.smoke.$$; sb "launchctl submit -l $LB -- /usr/bin/touch $T/sock/esc"; sleep 2; launchctl remove "$LB" 2>/dev/null
  chk "real sandbox: launchctl submit job did not run" test ! -e "$T/sock/esc"
else bad "real sandbox: /usr/bin/sandbox-exec missing"; fi
printf '\n%s\n' "$([ $FAILED = 0 ] && echo 'IMPLEMENT TESTS PASSED' || echo 'IMPLEMENT TESTS FAILED')"
exit $FAILED
