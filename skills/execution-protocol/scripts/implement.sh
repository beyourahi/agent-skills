#!/usr/bin/env bash
# implement.sh - run one execution-protocol implementer round from the last logged Opus plan: DeepSeek V4.1 Flash High inside
# scripts/implementer.sb; Sonnet 5.5 High only when DeepSeek is unavailable, inside Claude Code's own sandbox. Logs every attempt to EVID/impl.log.
set -uo pipefail
usage() { cat <<'EOF'
usage: EVID=<dir> implement.sh <low|medium|high> --bootstrap <cmd|none> [--step NAME] [--notes FILE] [--trigger]
       EVID=<dir> implement.sh --discard --bootstrap <cmd|none>   (reset a worktree a killed run left behind)
       EVID=<dir> implement.sh --check                             (validate the routes in EVID/impl.log)
  run from the root of a clean, linked task worktree; the brief comes from the last plan gate.sh logged in EVID
  env: IMPL_DEADLINE (seconds per run, default 3600); IMPL_SANDBOX_EXEC (tests only: replaces /usr/bin/sandbox-exec)
exit: 0 done (conductor reads the diff, then re-runs the bootstrap), 1 failed round, 2 usage or precondition (no model call),
      3 no implementer available, 4 round cap (3 per step and plan), 5 escalate (EVID/escalation-<tag>.md),
      6 confined processes survived (the worktree is tainted: read, run or commit nothing from it; remove it, use a new path)
EOF
}
die() { printf 'implement.sh: %s\n' "$*" >&2; exit 2; }
SKILL=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P); LIB=$SKILL/scripts/implement_lib.py; py() { python3 -I "$LIB" "$@"; }
DSM=opencode-go/deepseek-v4.1-flash; SNM=claude-sonnet-5-5
# main runs only after bash has parsed the whole file, so an edit to this file during a run cannot change what runs.
main() {
if [ "${1:-}" = --help ]; then usage; exit 0; fi
[ -n "${EVID:-}" ] || die "EVID is unset"
[ -d "$EVID" ] || die "EVID is not a directory: $EVID"
EVID=$(cd "$EVID" && pwd -P)
if [ "${1:-}" = --check ]; then [ $# -eq 1 ] || die "--check takes no other arguments"; if py check "$EVID"; then exit 0; else exit 1; fi; fi
mode=run; tier=-; boot=""; hasboot=0; step=main; notes=-; trigger=0
if [ "${1:-}" = --discard ]; then mode=discard; shift
else [ $# -ge 1 ] || { usage >&2; exit 2; }; tier=$1; shift; case $tier in low|medium|high) ;; *) die "bad tier: $tier" ;; esac; fi
while [ $# -gt 0 ]; do case $1 in
  --bootstrap) shift; [ $# -gt 0 ] && [ -n "$1" ] || die "--bootstrap needs a command or none"; boot=$1; hasboot=1 ;;
  --trigger) [ "$mode" = run ] || die "--trigger is only valid for a run"; trigger=1 ;;
  --step) shift; [ $# -gt 0 ] || die "--step needs a name"; step=$1 ;;
  --notes) shift; [ "$mode" = run ] && [ $# -gt 0 ] || die "--notes needs a file"; [ -f "$1" ] || die "missing notes file: $1"; notes=$(cd "$(dirname "$1")" && pwd -P)/$(basename "$1") ;;
  *) die "unknown argument: $1" ;; esac; shift; done
[ "$hasboot" = 1 ] || die "--bootstrap <cmd|none> is required (the repo's setup command, from its AGENTS.md/CLAUDE.md/README)"
printf '%s' "$step" | grep -Eq '^[A-Za-z0-9._-]+$' || die "bad step name: $step"
case ${IMPL_DEADLINE:-3600} in ''|*[!0-9]*|0) die "IMPL_DEADLINE must be whole seconds above 0" ;; esac; DEADLINE=${IMPL_DEADLINE:-3600}
WT=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git worktree"; WT=$(cd "$WT" && pwd -P)
[ "$(pwd -P)" = "$WT" ] || die "run from the worktree root: $WT"
GD=$(git rev-parse --path-format=absolute --git-dir) && GC=$(git rev-parse --path-format=absolute --git-common-dir) || die "cannot read git dirs"
GD=$(cd "$GD" && pwd -P); GC=$(cd "$GC" && pwd -P); [ "$GD" != "$GC" ] || die "not a linked worktree (create one with git worktree add --detach)"
H=$(cd "$HOME" && pwd -P); EVROOT=$H/.local/state/dev-evidence
inside() { case $1 in "$2"|"$2"/*) return 0 ;; esac; return 1; }
if inside "$H" "$WT" || inside "$WT" "$EVROOT" || inside "$EVROOT" "$WT"; then die "refusing worktree $WT: it is HOME or its ancestor, or overlaps the evidence root"; fi
# reap: stop every process with its cwd in the worktree or confined to it (by our sandbox or Claude Code's); fails closed (exit 6).
RT=""; RM=""; RE=""; TAINT=0
reap() { REAPED=$(cd / && py reap "$WT" "$$"); }
tainted() { TAINT=1; if [ -n "$RT" ]; then py row "$EVID" "$step" "${round:-0}" "${ps:-}" "$tier" "$RT" "$RM" "$RE" failed "confined processes survived: $REAPED" "${SID:--}" 0 0 0
  else py row "$EVID" "$step" 0 "" "" "" "" "" discarded "confined processes survived: $REAPED" "" 0 0 0; fi
  printf 'implement.sh: confined processes survived (%s). The worktree %s is tainted: read, run, bootstrap or commit nothing from it; from the main checkout run git worktree remove --force %s, never reuse that path, and continue in a fresh worktree.\n' "$REAPED" "$WT" "$WT" >&2; exit 6; }
bootstrap() { [ "$boot" = none ] && return 0; (cd "$WT" && bash -c "$boot") >> "$EVID/impl-bootstrap.log" 2>&1 && return 0
  printf 'implement.sh: bootstrap failed (see %s); the worktree is reset but not set up\n' "$EVID/impl-bootstrap.log" >&2; return 1; }
discard() { reap || tainted; rm -rf "$WT/.impl"; git -C "$WT" reset --hard -q; git -C "$WT" clean -ffdxq; bootstrap; }
if [ "$mode" = discard ]; then discard; brc=$?; py row "$EVID" "$step" 0 "" "" "" "" "" discarded "--discard" "" 0 0 0
  printf 'implement.sh: worktree reset to HEAD\n'; exit $((brc ? 1 : 0)); fi
[ -z "$(git status --porcelain --untracked-files=all)" ] || die "worktree is not clean; commit or discard changes first"
[ ! -e "$WT/.impl" ] && [ ! -L "$WT/.impl" ] || die "$WT/.impl exists; run implement.sh --discard first"
if py laststart "$EVID"; then die "the last impl.log row is start: a run was killed; run implement.sh --discard --bootstrap <cmd|none> first"; fi
pinfo=$(py plan "$EVID" "$step") || die "no logged Opus plan in $EVID/gates.log with a matching file (run gate.sh plan first)"
IFS=$'\t' read -r pf ps round counted <<< "$pinfo"
if [ "$counted" -ge 3 ]; then printf 'implement.sh: 3 rounds for step %s on this plan; re-plan through gate.sh plan or implement the step yourself\n' "$step" >&2; exit 4; fi
tag=$step-r$round-${ps:0:8}; SBX=${IMPL_SANDBOX_EXEC:-/usr/bin/sandbox-exec}
UTMP=$(cd "$(getconf DARWIN_USER_TEMP_DIR)" && pwd -P); UCACHE=$(cd "$(getconf DARWIN_USER_CACHE_DIR)" && pwd -P)
# Trusted state stays in this process and EVID; the child sees only the worktree, with the brief and its result under .impl/.
py row "$EVID" "$step" "$round" "$ps" "$tier" "" "" "" start "" "" 0 0 0
RUN=$(mktemp -d "$UTMP/implement.XXXXXX"); RUN=$(cd "$RUN" && pwd -P); mkdir -p "$RUN/cache" "$RUN/state" "$RUN/tmp" "$RUN/x"; RT=deepseek; RM=$DSM; RE=high
trap 'rm -rf "$RUN"; [ "$TAINT" = 1 ] || rm -rf "$WT/.impl"' EXIT; trap 'trap - INT TERM HUP; reap || tainted; printf "implement.sh: interrupted; run implement.sh --discard next\n" >&2; exit 130' INT TERM HUP
brief=$EVID/impl-$tag-brief.md; err=$EVID/impl-$tag.err; : > "$err"; py brief "$SKILL/implementer-brief.md" "$pf" "$WT" "$notes" "$brief" || die "cannot build the brief"
mkimpl() { mkdir "$WT/.impl" && cp "$brief" "$WT/.impl/brief.md"; }; mkimpl
patch_out() { { git status --porcelain --untracked-files=all; git diff --binary HEAD; } > "$EVID/impl-$tag-$1-discarded.patch" 2>/dev/null; }
# A self-check inside the sandbox: creating a file in HOME must fail, or nothing runs (exit 99).
CHK='f="$HOME/.impl-sbx-$1"; shift; if ( : > "$f" ) 2>/dev/null; then rm -f "$f"; exit 99; fi; [ ! -e "$f" ] || exit 99; echo IMPL-SANDBOX-OK >&2; exec "$@"'
sbx() { env OPENCODE_CONFIG_DIR="$SKILL/opencode" OPENCODE_CONFIG_CONTENT="$(cat "$SKILL/opencode/opencode.json")" OPENCODE_DISABLE_CLAUDE_CODE=1 \
    OPENCODE_DISABLE_FREE_MODELS=1 XDG_CACHE_HOME="$RUN/cache" XDG_STATE_HOME="$RUN/state" TMPDIR="$RUN/tmp" timeout -k 30 "$DEADLINE" "$SBX" -f "$SKILL/scripts/implementer.sb" -D "HOME=$H" -D "WT=$WT" \
    -D "OCDATA=$H/.local/share/opencode" -D "UTMP=$UTMP" -D "UCACHE=$UCACHE" -D "EVROOT=$EVROOT" -D "RUN=$RUN" \
    /bin/sh -c "$CHK" sh "$(python3 -I -c 'import secrets; print(secrets.token_hex(8))')" "$@"; }
# ds NAME AGENT MSG [ARGS]: one sandboxed opencode run in the worktree, exported and classified. Sets KIND (ok|unavailable|timeout), WHY, SID, TIN, TOUT, COST.
ds() { local name=$1 agent=$2 msg=$3 evf=$EVID/impl-$tag-$1.events se=$EVID/impl-$tag-$1.sbx rc; shift 3; KIND=unavailable; SID=-; TIN=0; TOUT=0; COST=0
  if [ ! -x "$SBX" ]; then WHY="sandbox: $SBX missing"; return; fi
  if ! command -v opencode >/dev/null 2>&1; then WHY="opencode not on PATH"; return; fi
  (cd "$WT" && sbx opencode run --standalone --archived --agent "$agent" --model "$DSM#high" --format json \
    --title "$(basename "$EVID")-$step-r$round-$name" "$@" "$msg" < /dev/null > "$evf" 2> "$se") & wait $!; rc=$?; reap || tainted; cat "$se" >> "$err"
  if [ "$rc" = 124 ] || [ "$rc" = 137 ]; then KIND=timeout; WHY="timeout after ${DEADLINE}s"; return; fi
  if [ "$rc" = 99 ]; then WHY="sandbox: self-check write in HOME was not denied"; return; fi
  if ! grep -q '^IMPL-SANDBOX-OK$' "$se"; then WHY="sandbox: did not start (rc $rc)"; return; fi
  SID=$(py sid "$evf"); SID=${SID:--}; local ex=$EVID/impl-$tag-$name-export.json; : > "$ex"
  [ "$SID" = - ] || { (cd "$RUN/x" && sbx opencode session export --standalone "$SID") > "$ex" 2>> "$err" & wait $!; reap || tainted; }
  IFS=$'\t' read -r KIND WHY TIN TOUT COST <<< "$(py dsclass "$ex" "$evf" "$SID")"; }
# outcome ROUTE MODEL EFFORT FORCED: AC13 checks on a verified-model run; done keeps the changes, anything else is saved and discarded.
outcome() { local st why; IFS=$'\t' read -r st why <<< "$(cd / && py final "$EVID" "$WT" "$pf" "$tag" "$step" "$round" "$ps" "$tier" "$1" "$2" "$3" "$SID" "$TIN" "$TOUT" "$COST" "$4")"
  printf 'implement %s round %s route=%s status=%s reason=%s\n' "$step" "$round" "$1" "$st" "$why"
  case $st in
    done) rm -rf "$WT/.impl"; git clean -ffdXq
      printf 'implement.sh: done. Conductor: read the full diff (git status; git diff HEAD) before running anything from it, then re-run the bootstrap.\n'; exit 0 ;;
    escalate) patch_out "$1"; discard; printf 'implement.sh: escalated; read %s\n' "$EVID/escalation-$tag.md"; exit 5 ;;
    *) patch_out "$1"; discard; exit 1 ;; esac; }
unavail() { py row "$EVID" "$step" "$round" "$ps" "$tier" "$1" "$2" "$3" unavailable "$WHY" "$SID" "$TIN" "$TOUT" "$COST"
  printf 'implement %s round %s route=%s unavailable: %s\n' "$step" "$round" "$1" "$WHY" >&2; }
ds probe implementer-probe "Reply with OK only."
if [ "$KIND" = ok ]; then ds main implementer "Implement the attached brief exactly. Write .impl/result.json last, then stop." -f "$WT/.impl/brief.md"
  if [ "$KIND" = timeout ]; then outcome deepseek "$DSM" high "$WHY"; fi
  if [ "$KIND" = ok ]; then outcome deepseek "$DSM" high -; fi
  unavail deepseek "$DSM" high; patch_out deepseek; discard; mkimpl
else [ "$KIND" != timeout ] || WHY="probe $WHY"; unavail deepseek "$DSM" high; fi
seff=high; RT=sonnet; RM=$SNM; RE=$seff
KIND=unavailable; WHY="claude not on PATH"; SID=-; TIN=0; TOUT=0; COST=0
if command -v claude >/dev/null 2>&1; then so=$EVID/impl-$tag-sonnet.json
  # Bash is the only tool, so every file and network access goes through Claude Code's sandbox (failIfUnavailable: no sandbox, no run).
  (cd "$WT" && exec timeout -k 30 "$DEADLINE" claude -p --model "$SNM" --effort "$seff" --setting-sources "" --settings "$(py sonnetcfg "$WT" "$GC" "$H" "$EVROOT")" \
    --strict-mcp-config --mcp-config '{"mcpServers":{}}' --permission-mode dontAsk --allowedTools Bash \
    --disallowedTools Read,Grep,Glob,Edit,Write,NotebookEdit,WebFetch,WebSearch,Agent --no-session-persistence --output-format json) < "$brief" > "$so" 2>> "$err" & wait $!
  reap || tainted; IFS=$'\t' read -r KIND WHY TIN TOUT COST <<< "$(py snclass "$so")"
  if [ "$KIND" = ok ]; then outcome sonnet "$SNM" "$seff" -; fi
  patch_out sonnet; discard
fi
unavail sonnet "$SNM" "$seff"
printf 'implement.sh: no implementer available (DeepSeek and Sonnet 5.5 both unavailable); the conductor implements this step itself\n' >&2; exit 3
}
main "$@"; exit $?
