#!/bin/sh
# Run a job on another account and leave a durable record of it.
#
#   run.sh secondary <prompt-file> [--review|--work] [--model M] [--effort E]
#   run.sh codex     <prompt-file> [--review|--work] [--model M] [--effort E]
#
# Prints the worker's reply on stdout. The call is logged either way, because a
# delegation that failed quietly is the failure mode this exists to prevent: the
# reply still reads like prose, and nobody notices nothing happened.
set -u
umask 077   # exchanges hold whole prompts and replies; they are nobody else's business

SW_HOME="${SW_HOME:-$HOME/.second-wind}"
CFG="$SW_HOME/config.json"
[ -f "$CFG" ] || { echo "second-wind: not set up. Run scripts/setup.py --discover" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "second-wind: jq is required" >&2; exit 2; }
need_bin() { command -v "$1" >/dev/null 2>&1 || { echo "second-wind: '$1' is not on PATH. Without it a delegation fails as a shell error that gets logged as though it were the worker's reply." >&2; exit 2; }; }

worker=${1:-}; promptfile=${2:-}
[ -n "$worker" ] && [ -f "$promptfile" ] || {
  echo "usage: run.sh <secondary|codex> <prompt-file> [--review|--work] [--model M] [--effort E]" >&2; exit 2; }
shift 2

cfg() { jq -r "$1 // empty" "$CFG"; }
# jq's // treats false as absent, so a false boolean silently reads as empty.
# That is what once stopped the browser guard firing and failover from switching off.
cfgbool() { jq -r "if $1 == null then \"\" else ($1|tostring) end" "$CFG"; }
# quote the pattern: an unquoted ~ inside ${...} is itself tilde-expanded,
# turning ~/x into $HOME/~/x
expand() { case "$1" in "~"/*) printf '%s' "$HOME/${1#"~/"}";; *) printf '%s' "$1";; esac; }
is_int() { case "${1:-}" in ''|*[!0-9]*) return 1;; *) return 0;; esac; }

mode=$(cfg '.defaults.mode'); [ -n "$mode" ] || mode=review
# an unrecognised mode used to fall through to full access, which is the wrong
# way round for a default to fail
case "$mode" in review|work) ;; *) echo "second-wind: defaults.mode must be review or work, got '$mode'" >&2; exit 2 ;; esac
model=""; effort=""
while [ $# -gt 0 ]; do
  case "$1" in
    --review) mode=review ;;
    --work)   mode=work ;;
    --model)  [ $# -ge 2 ] || { echo "second-wind: --model needs a value" >&2; exit 2; }; model=$2; shift ;;
    --effort) [ $# -ge 2 ] || { echo "second-wind: --effort needs a value" >&2; exit 2; }; effort=$2; shift ;;
    *) echo "second-wind: unknown option '$1'" >&2; exit 2 ;;
  esac
  shift
done

tmo=$(cfg '.timeout_seconds'); is_int "$tmo" && [ "$tmo" -gt 0 ] 2>/dev/null || tmo=600
LOGDIR=$(expand "$(cfg '.log_dir')"); [ -n "$LOGDIR" ] || LOGDIR="$SW_HOME/log"

# Prove we can record before doing any work. Delegating and then failing to log
# is worse than not delegating: the work happened and nothing says what it was.
mkdir -p "$LOGDIR" 2>/dev/null || { echo "second-wind: cannot create log dir $LOGDIR" >&2; exit 2; }
chmod 700 "$LOGDIR" 2>/dev/null || true
probe="$LOGDIR/.writable.$$"
: > "$probe" 2>/dev/null || { echo "second-wind: log dir $LOGDIR is not writable, refusing to run" >&2; exit 2; }
rm -f "$probe"

ts=$(date +%Y%m%d-%H%M%S)
slug=$(basename "$(pwd)" | tr ' /' '__' | tr -cd 'A-Za-z0-9_-' | cut -c1-40)
exchange="$LOGDIR/$ts-$$-$worker-$slug.md"   # pid included: parallel calls collide within a second

# A worker that cannot launch a browser has to be told so, in the prompt. Without
# it, the worker follows a project instruction to "verify in a real browser",
# crashes a browser on the user's desktop, and reports success anyway.
case "$worker" in
  codex) cb=$(cfgbool '.codex.can_launch_browser') ;;
  *)     cb=true ;;
esac
# Read-only mode blocks the write tools, but the worker is not told that, and a
# blocked model will cheerfully report "I created the file" having created
# nothing. A delegation that lies is worse than one that fails, so say it plainly.
modeguard=""
if [ "$mode" = review ]; then
  modeguard="You are in read-only mode. The tools that create, edit or delete files, and the shell, are all blocked for this run. Do not claim to have written, created or changed anything: you cannot. If the task requires a change, describe exactly what you would change and say plainly that you did not make it."
fi

guard=""
# anything other than a definite true means we do not know it can, and guessing
# wrong crashes a browser on the user's desktop
if [ "$worker" = codex ] && [ "$cb" != "true" ]; then
  guard="You are running inside a sandbox and cannot launch a web browser: it will abort at startup. Do not run any browser, headless browser, CDP harness, puppeteer or playwright step, even if this project's instructions tell you to verify rendered output that way. Hand that step back instead and say which step you skipped."
fi

# The prompt goes on stdin, never as an argument. Both CLIs have variadic options
# (--add-dir, --disallowed-tools) that swallow a following positional argument,
# and a long prompt would hit the argument-size limit besides.
sendfile=$(mktemp "${TMPDIR:-/tmp}/second-wind-prompt.XXXXXX") || { echo "second-wind: cannot create a temp file" >&2; exit 2; }
cleanup() { rm -f "$sendfile" 2>/dev/null; }
trap 'cleanup; exit 130' INT TERM
trap cleanup EXIT
{
  [ -n "$modeguard" ] && printf '%s\n\n' "$modeguard"
  [ -n "$guard" ] && printf '%s\n\n' "$guard"
  cat "$promptfile"
} >> "$sendfile" || { echo "second-wind: could not assemble the prompt, not running the worker" >&2; exit 2; }

# macOS has no timeout(1), and a hung worker would hang the caller. Run it in the
# background, poll, kill the whole process group if it overruns.
sw_run() {
  # Job control gives the background worker its own process group. The worker can
  # spawn grandchildren, so killing only its direct children leaves processes
  # behind after a timeout.
  set -m 2>/dev/null
  secs=$1; shift
  t_start=$(date +%s)
  outf=$(mktemp "${TMPDIR:-/tmp}/second-wind-out.XXXXXX")
  "$@" <"$sendfile" >"$outf" 2>&1 &
  pid=$!
  timed_out=0; i=0
  while kill -0 "$pid" 2>/dev/null; do
    i=$((i+1))
    if [ "$i" -ge "$secs" ]; then
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
      sleep 2
      kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
      timed_out=1; break
    fi
    sleep 1
  done
  wait "$pid" 2>/dev/null; rc=$?
  if [ "$timed_out" = 1 ]; then
    rc=124
    printf '\n[second-wind] killed after %ss. The worker was still running.\n' "$secs" >> "$outf"
  fi
  reply=$(cat "$outf"); rm -f "$outf"
  dur=$(( $(date +%s) - t_start ))
  return $rc
}

case "$worker" in
  secondary)
    [ "$(cfgbool '.secondary.enabled')" = "false" ] && { echo "second-wind: secondary is disabled in config" >&2; exit 2; }
    need_bin claude
    dir=$(expand "$(cfg '.secondary.config_dir')")
    acct=$(cfg '.secondary.account')
    set -- claude -p --add-dir "$(pwd)"
    if [ "$mode" = review ]; then
      # Bash has to be blocked too. Without it a "read-only" reviewer can still
      # run sed -i, git checkout or rm, which makes the mode's name a lie and
      # makes it unsafe to review files you are still editing.
      set -- "$@" --disallowed-tools "Edit,Write,NotebookEdit,Bash,BashOutput,KillShell"
    else
      set -- "$@" --permission-mode bypassPermissions
    fi
    [ -n "$model" ] && set -- "$@" --model "$model"
    [ -n "$effort" ] && set -- "$@" --effort "$effort"
    # Only set CLAUDE_CONFIG_DIR for a non-default profile. Setting it to
    # ~/.claude makes the CLI look for a hashed keychain entry that does not
    # exist, and a signed-in account then reports itself signed out.
    if [ "$dir" = "$HOME/.claude" ]; then unset CLAUDE_CONFIG_DIR
    else CLAUDE_CONFIG_DIR="$dir"; export CLAUDE_CONFIG_DIR; fi
    sw_run "$tmo" "$@"; rc=$?
    ;;
  codex)
    [ "$(cfgbool '.codex.enabled')" = "true" ] || { echo "second-wind: codex is disabled in config" >&2; exit 2; }
    need_bin codex
    acct=$(cfg '.codex.account')
    set -- codex exec --skip-git-repo-check -C "$(pwd)"
    if [ "$mode" = review ]; then
      set -- "$@" -s read-only
    else
      # --approve-for-me implies the workspace-write sandbox, so it cannot be
      # combined with -s
      set -- "$@" --approve-for-me
    fi
    [ -n "$model" ] && set -- "$@" -m "$model"
    [ -n "$effort" ] && set -- "$@" -c "model_reasoning_effort=\"$effort\""
    sw_run "$tmo" "$@"; rc=$?
    ;;
  *) rm -f "$sendfile"; echo "second-wind: unknown worker '$worker'" >&2; exit 2 ;;
esac

clean=$(printf '%s' "$reply" | grep -v 'listTools() called but server does not advertise')

{
  printf '# Delegated to %s\n\n' "$worker"
  printf -- '- When: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"
  printf -- '- Account: %s\n' "${acct:-unknown}"
  printf -- '- Mode: %s\n' "$mode"
  printf -- '- Working directory: %s\n' "$(pwd)"
  printf -- '- Model: %s\n' "${model:-default}"
  printf -- '- Effort: %s\n' "${effort:-default}"
  printf -- '- Browser guard applied: %s\n' "$([ -n "$guard" ] && echo yes || echo no)"
  printf -- '- Exit code: %s%s\n\n' "$rc" "$([ "$rc" = 124 ] && echo ' (killed on timeout)')"
  printf '## Prompt sent\n\n```\n'; cat "$sendfile"; printf '\n```\n\n'
  printf '## Reply\n\n'; printf '%s\n' "$clean"
} > "$exchange" || { echo "second-wind: failed to write $exchange" >&2; rm -f "$sendfile"; exit 1; }
chmod 600 "$exchange" 2>/dev/null || true
rm -f "$sendfile"

# One line, one append. A single small write to a file opened O_APPEND does not
# interleave, so parallel calls need no lock, which is safer than the lock this
# used to take and then steal from itself.
line=$(jq -nc --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg w "$worker" --arg a "${acct:-unknown}" \
      --arg m "$mode" --arg cwd "$(pwd)" --arg mdl "${model:-default}" --arg eff "${effort:-default}" \
      --arg f "$exchange" --argjson rc "$rc" --argjson dur "$dur" \
      --argjson pc "$(wc -c < "$promptfile" | tr -d ' ')" \
      --argjson rcn "$(printf '%s' "$clean" | wc -c | tr -d ' ')" \
      --argjson guard "$([ -n "$guard" ] && echo true || echo false)" \
  '{ts:$ts,worker:$w,account:$a,mode:$m,cwd:$cwd,model:$mdl,effort:$eff,
    browser_guard:$guard,exit:$rc,duration_s:$dur,prompt_bytes:$pc,reply_bytes:$rcn,exchange:$f}')
ledger="$LOGDIR/$(date +%Y-%m).jsonl"
printf '%s\n' "$line" >> "$ledger" || echo "second-wind: could not append to $ledger" >&2
chmod 600 "$ledger" 2>/dev/null || true

printf '%s\n' "$clean"
if [ "$rc" = 0 ] && [ -z "$(printf '%s' "$clean" | tr -d '[:space:]')" ]; then
  printf '\n[second-wind] WARNING: the worker exited 0 but returned nothing. Treat this as a failed delegation, not as agreement.\n' >&2
fi
printf '\n[second-wind] %s, %s mode, %ss, exit %s. Logged: %s\n' "$worker" "$mode" "$dur" "$rc" "$exchange" >&2
exit "$rc"
