#!/bin/sh
# Run a job on another account and leave a durable record of it.
#
#   run.sh secondary <prompt-file> [--review|--work] [--model M] [--effort E]
#   run.sh codex     <prompt-file> [--review|--work] [--model M] [--effort E]
#
# Prints the worker's reply on stdout. Everything is logged either way, because a
# delegation that failed quietly is the failure mode this exists to prevent: the
# reply still reads like prose, and nobody notices nothing happened.
set -u
SW_HOME="${SW_HOME:-$HOME/.second-wind}"
CFG="$SW_HOME/config.json"
[ -f "$CFG" ] || { echo "second-wind: not set up. Run scripts/setup.py --discover" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "second-wind: jq is required" >&2; exit 2; }

worker=${1:-}; promptfile=${2:-}
[ -n "$worker" ] && [ -f "$promptfile" ] || { echo "usage: run.sh <secondary|codex> <prompt-file> [--review|--work]" >&2; exit 2; }
shift 2

cfg() { jq -r "$1 // empty" "$CFG"; }
# jq's // operator treats false as absent, so booleans need their own reader or a
# false value silently reads as empty. This is what stopped the browser guard firing.
cfgbool() { jq -r "if $1 == null then \"\" else ($1|tostring) end" "$CFG"; }
# quote the pattern: an unquoted ~ inside ${...} is itself tilde-expanded,
# which turns ~/x into $HOME/~/x instead of $HOME/x
expand() { case "$1" in "~"/*) printf '%s' "$HOME/${1#"~/"}";; *) printf '%s' "$1";; esac; }

# macOS has no timeout(1), and a worker that hangs would hang the whole session.
# Run it in the background, poll, and kill it if it overruns. Sets $reply and
# returns the worker's exit code, or 124 if it was killed.
sw_run() {
  set +m 2>/dev/null   # keep "Terminated" job-control noise out of the user's terminal
  secs=$1; shift
  outf=$(mktemp "${TMPDIR:-/tmp}/second-wind-out.XXXXXX")
  "$@" </dev/null >"$outf" 2>&1 &
  pid=$!
  timed_out=0; i=0
  while kill -0 "$pid" 2>/dev/null; do
    i=$((i+1))
    if [ "$i" -ge "$secs" ]; then
      kill "$pid" 2>/dev/null; sleep 2; kill -9 "$pid" 2>/dev/null
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
  return $rc
}

mode=$(cfg '.defaults.mode'); [ -n "$mode" ] || mode=review
model=""; effort=""
while [ $# -gt 0 ]; do
  case "$1" in
    --review) mode=review ;;
    --work)   mode=work ;;
    --model)  model=${2:-}; shift ;;
    --effort) effort=${2:-}; shift ;;
  esac
  shift
done

tmo=$(cfg '.timeout_seconds'); [ -n "$tmo" ] || tmo=600
LOGDIR=$(expand "$(cfg '.log_dir')"); [ -n "$LOGDIR" ] || LOGDIR="$SW_HOME/log"
mkdir -p "$LOGDIR"
ts=$(date +%Y%m%d-%H%M%S)
slug=$(basename "$(pwd)" | tr ' /' '__' | tr -cd 'A-Za-z0-9_-' | cut -c1-40)
exchange="$LOGDIR/$ts-$worker-$slug.md"
start=$(date +%s)

# A worker that cannot launch a browser must be told so in the prompt. Without
# this it will follow a project instruction to "verify in a real browser", crash
# a browser on the user's desktop, and report success anyway.
guard=""
prefix=""
case "$worker" in
  codex)  cb=$(cfgbool '.codex.can_launch_browser') ;;
  *)      cb=true ;;
esac
if [ "$cb" = "false" ]; then
  guard="You are running inside a sandbox and cannot launch a web browser: it will abort at startup. Do not run any browser, headless browser, CDP harness, puppeteer or playwright step, even if this project's instructions tell you to verify rendered output that way. Hand that step back instead and say which step you skipped."
  prefix="$guard

"
fi

case "$worker" in
  secondary)
    dir=$(expand "$(cfg '.secondary.config_dir')")
    acct=$(cfg '.secondary.account')
    [ "$(cfgbool '.secondary.enabled')" = "false" ] && { echo "second-wind: secondary is disabled in config" >&2; exit 2; }
    set -- claude -p --add-dir "$(pwd)"
    if [ "$mode" = review ]; then
      set -- "$@" --disallowed-tools "Edit Write NotebookEdit"
    else
      set -- "$@" --permission-mode bypassPermissions
    fi
    [ -n "$model" ] && set -- "$@" --model "$model"
    [ -n "$effort" ] && set -- "$@" --effort "$effort"
    # Only set CLAUDE_CONFIG_DIR for a non-default profile. Setting it to
    # ~/.claude makes the CLI look for a hashed keychain entry that does not
    # exist and the call fails as unauthenticated.
    if [ "$dir" = "$HOME/.claude" ]; then
      unset CLAUDE_CONFIG_DIR
    else
      CLAUDE_CONFIG_DIR="$dir"; export CLAUDE_CONFIG_DIR
    fi
    sw_run "$tmo" "$@" "$prefix$(cat "$promptfile")"; rc=$?
    ;;
  codex)
    acct=$(cfg '.codex.account')
    [ "$(cfgbool '.codex.enabled')" = "true" ] || { echo "second-wind: codex is disabled in config" >&2; exit 2; }
    set -- codex exec --skip-git-repo-check -C "$(pwd)"
    if [ "$mode" = review ]; then
      set -- "$@" -s read-only
    else
      # --approve-for-me implies the workspace-write sandbox and auto-approves,
      # so it cannot be combined with -s
      set -- "$@" --approve-for-me
    fi
    [ -n "$model" ] && set -- "$@" -m "$model"
    [ -n "$effort" ] && set -- "$@" -c "model_reasoning_effort=\"$effort\""
    sw_run "$tmo" "$@" "$prefix$(cat "$promptfile")"; rc=$?
    ;;
  *) echo "second-wind: unknown worker '$worker'" >&2; exit 2 ;;
esac

end=$(date +%s); dur=$(( end - start ))
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
  printf -- '- Duration: %ss\n' "$dur"
  printf -- '- Exit code: %s%s\n\n' "$rc" "$([ "$rc" = 124 ] && echo ' (killed on timeout)')"
  printf '## Prompt sent\n\n```\n'; printf '%s' "$prefix"; cat "$promptfile"; printf '\n```\n\n'
  printf '## Reply\n\n'; printf '%s\n' "$clean"
} > "$exchange"

# concurrent fan-out appends here, so serialise the ledger write
lock="$LOGDIR/.lock"; i=0
while ! mkdir "$lock" 2>/dev/null; do
  i=$((i+1)); [ "$i" -gt 100 ] && { rmdir "$lock" 2>/dev/null; break; }; sleep 0.1
done
trap 'rmdir "$lock" 2>/dev/null' EXIT INT TERM
jq -nc --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg w "$worker" --arg a "${acct:-unknown}" \
      --arg m "$mode" --arg cwd "$(pwd)" --arg mdl "${model:-default}" --arg eff "${effort:-default}" \
      --arg f "$exchange" --argjson rc "$rc" --argjson dur "$dur" \
      --argjson pc "$(wc -c < "$promptfile" | tr -d ' ')" \
      --argjson rcn "$(printf '%s' "$clean" | wc -c | tr -d ' ')" \
      --argjson guard "$([ -n "$guard" ] && echo true || echo false)" \
  '{ts:$ts,worker:$w,account:$a,mode:$m,cwd:$cwd,model:$mdl,effort:$eff,
    browser_guard:$guard,exit:$rc,duration_s:$dur,prompt_bytes:$pc,reply_bytes:$rcn,exchange:$f}' \
  >> "$LOGDIR/$(date +%Y-%m).jsonl"
rmdir "$lock" 2>/dev/null; trap - EXIT INT TERM

printf '%s\n' "$clean"
printf '\n[second-wind] %s, %s mode, %ss, exit %s. Logged: %s\n' "$worker" "$mode" "$dur" "$rc" "$exchange" >&2
exit "$rc"
