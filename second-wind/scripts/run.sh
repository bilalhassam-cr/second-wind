#!/bin/sh
# Run a job on another account and leave a durable record of it.
#
#   run.sh secondary <prompt-file> [--review|--work] [--model M] [--effort E]
#   run.sh codex     <prompt-file> [--review|--work] [--model M] [--effort E]
#   run.sh codex2    <prompt-file> [--review|--work] [--model M] [--effort E]
#   run.sh codex3    <prompt-file> [--review|--work] [--model M] [--effort E]
#   run.sh grok      <prompt-file> [--review|--work] [--model M] [--effort E]
#   run.sh cursor    <prompt-file> [--review|--work] [--model M]
#
# Prints the worker's reply on stdout. The call is logged either way, because a
# delegation that failed quietly is the failure mode this exists to prevent: the
# reply still reads like prose, and nobody notices nothing happened.
set -u
umask 077   # exchanges hold whole prompts and replies; they are nobody else's business

SW_HOME="${SW_HOME:-$HOME/.second-wind}"
CFG="$SW_HOME/config.json"
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" 2>/dev/null && pwd)
[ -f "$CFG" ] || { echo "second-wind: not set up. Run scripts/setup.py --detect" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "second-wind: jq is required" >&2; exit 2; }
need_bin() { command -v "$1" >/dev/null 2>&1 || { echo "second-wind: '$1' is not on PATH. Without it a delegation fails as a shell error that gets logged as though it were the worker's reply." >&2; exit 2; }; }

worker=${1:-}; promptfile=${2:-}
[ -n "$worker" ] && [ -f "$promptfile" ] || {
  echo "usage: run.sh <secondary|codex|codex2|codex3|grok|cursor> <prompt-file> [--review|--work] [--model M] [--effort E]" >&2; exit 2; }
shift 2

cfg() { jq -r "$1 // empty" "$CFG"; }
# jq's // treats false as absent, so a false boolean silently reads as empty.
# That is what once stopped the browser guard firing and failover from switching off.
cfgbool() { jq -r "if $1 == null then \"\" else ($1|tostring) end" "$CFG"; }
# quote the pattern: an unquoted ~ inside ${...} is itself tilde-expanded,
# turning ~/x into $HOME/~/x
expand() { case "$1" in "~"/*) printf '%s' "$HOME/${1#"~/"}";; *) printf '%s' "$1";; esac; }
is_int() { case "${1:-}" in ''|*[!0-9]*) return 1;; *) return 0;; esac; }

# Every worker has to spend the subscription its own client is signed into. An
# API key left in the environment makes the client bill that key instead,
# quietly, and the delegation then costs money nobody meant to spend. Codex is
# the sharpest case: its CLI prefers OPENAI_API_KEY over the ChatGPT login when
# the variable is set.
#
# Every worker gets the whole list cleared, not just its own vendor's key. A key
# for another vendor is no safer: an agent that can run commands can read the
# environment it was handed, and a key it has no business seeing is a key that
# can leave with it. One list in one place, because six lists in four branches
# is how one of them goes stale.
#
# Cursor is the one exception, and only for its own key. It has two sign-ins and
# an API key is one of them, so clearing the key unconditionally broke every
# API-key delegation. The key is kept when the config records that sign-in, and
# when the config says nothing at all, because an unrecorded auth is more likely
# an old config than a bill.
VENDOR_KEYS="ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_AUTH_TOKEN OPENAI_API_KEY XAI_API_KEY CURSOR_API_KEY"
creds_cleared=""
creds_kept=""
isolate_credentials() {
  creds_kept=""
  if [ "$1" = cursor ]; then
    case "$(cfg '.cursor.auth')" in
      api_key|"") creds_kept="CURSOR_API_KEY" ;;
    esac
  fi
  creds_cleared=""
  for name in $VENDOR_KEYS; do
    case " $creds_kept " in *" $name "*) continue ;; esac
    creds_cleared="${creds_cleared:+$creds_cleared }$name"
    unset "$name"
  done
}

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
# log.dir is the v4 key. The old top-level log_dir is still read so a config
# written by an earlier version keeps working without a migration step.
LOGDIR=$(expand "$(cfg '.log.dir')")
[ -n "$LOGDIR" ] || LOGDIR=$(expand "$(cfg '.log_dir')")
[ -n "$LOGDIR" ] || LOGDIR="$SW_HOME/log"
maxkb=$(cfg '.log.max_exchange_kb'); is_int "$maxkb" && [ "$maxkb" -gt 0 ] 2>/dev/null || maxkb=200
prunedays=$(cfg '.log.prune_days'); is_int "$prunedays" && [ "$prunedays" -gt 0 ] 2>/dev/null || prunedays=30

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

# Read-only mode blocks the write tools, but the worker is not told that, and a
# blocked model will cheerfully report "I created the file" having created
# nothing. A delegation that lies is worse than one that fails, so say it plainly.
modeguard=""
if [ "$mode" = review ]; then
  modeguard="You are in read-only mode. The built-in tools that create, edit or delete files, and the shell, are all blocked for this run. Do not claim to have written, created or changed anything: you cannot. If the task requires a change, describe exactly what you would change and say plainly that you did not make it."
  # Only the Claude worker is started with MCP loading switched off, so only its
  # guard says so. Telling a Codex or Cursor worker the same thing would be false.
  [ "$worker" = secondary ] && modeguard="$modeguard No MCP servers are loaded for this run either, so any tool one of them would have provided is unavailable too."
fi

# A worker that cannot launch a browser has to be told so, in the prompt. Without
# it, the worker follows a project instruction to "verify in a real browser",
# crashes a browser on the user's desktop, and reports success anyway.
#
# For Codex this is decided by what the command line passes, not measured.
# Review mode passes -s read-only, and work mode passes the workspace-write
# sandbox that --approve-for-me implies, and a command-line sandbox overrides
# whatever the user's own Codex config says. A sandboxed process cannot reach
# the window server, so the guard applies. The one exception is a Codex role
# whose config grants full_access: in work mode it runs with no sandbox at all,
# a browser can start, and telling it otherwise would be false.
guard=""
codex_full=false
sandbox_note="the client's own"
# Plain if, not a case inside $( ): macOS /bin/sh cannot parse a bare ) in a
# case pattern inside command substitution, and the header line after it was
# silently lost.
if [ "$worker" = codex ] || [ "$worker" = codex2 ] || [ "$worker" = codex3 ]; then
  if [ "$(cfgbool ".$worker.full_access")" = "true" ] && [ "$mode" = work ]; then
    codex_full=true
    sandbox_note="none, full access"
  elif [ "$mode" = review ]; then
    sandbox_note="read-only"
  else
    sandbox_note="workspace-write"
  fi
  if [ "$codex_full" != true ]; then
    guard="You are running inside a sandbox and cannot launch a web browser: it will abort at startup. Do not run any browser, headless browser, CDP harness, puppeteer or playwright step, even if this project's instructions tell you to verify rendered output that way. Hand that step back instead and say which step you skipped."
  fi
fi

# The prompt is assembled once. Claude, Codex and Cursor read it on stdin. Grok's
# single-turn flag requires its prompt as the immediately following argument.
sendfile=$(mktemp "${TMPDIR:-/tmp}/second-wind-prompt.XXXXXX") || { echo "second-wind: cannot create a temp file" >&2; exit 2; }
mcpfile=""   # empty MCP server list, written below for a Claude review run
lastmsg=""   # Codex writes its final message here
fullf=""     # the reply as it came back
bodyf=""     # the same reply, capped for the log
cleanup() {
  rm -f "$sendfile" 2>/dev/null
  # outf belongs to sw_run and may not exist yet, hence the default. Left
  # behind by an interrupted run it holds a whole prompt and reply, so it is
  # cleaned up here rather than only on the happy path.
  for stale in "$mcpfile" "$lastmsg" "$fullf" "$bodyf" "${outf:-}"; do
    [ -n "$stale" ] && rm -f "$stale" 2>/dev/null
  done
  return 0
}
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

isolate_credentials "$worker"

case "$worker" in
  secondary)
    [ "$(cfgbool '.secondary.enabled')" = "false" ] && { echo "second-wind: secondary is disabled in config" >&2; exit 2; }
    need_bin claude
    dir=$(expand "$(cfg '.secondary.config_dir')")
    acct=$(cfg '.secondary.account')
    set -- claude -p --add-dir "$(pwd)"
    if [ "$mode" = review ]; then sandbox_note="read-only tools, no MCP servers"
    else sandbox_note="none, bypassPermissions"; fi
    if [ "$mode" = review ]; then
      # Bash has to be blocked too. Without it a "read-only" reviewer can still
      # run sed -i, git checkout or rm, which makes the mode's name a lie and
      # makes it unsafe to review files you are still editing.
      set -- "$@" --disallowed-tools "Edit,Write,NotebookEdit,Bash,BashOutput,KillShell"
      # Blocking the built-in tools is not enough on its own: an MCP server can
      # supply its own write and shell tools, and those are not covered by the
      # list above. Loading none of them is the only reliable read-only run.
      mcpfile=$(mktemp "${TMPDIR:-/tmp}/second-wind-mcp.XXXXXX") || { echo "second-wind: cannot create a temp file" >&2; exit 2; }
      printf '%s\n' '{"mcpServers": {}}' > "$mcpfile" || { echo "second-wind: cannot write the empty MCP config" >&2; exit 2; }
    else
      set -- "$@" --permission-mode bypassPermissions
    fi
    [ -n "$model" ] && set -- "$@" --model "$model"
    [ -n "$effort" ] && set -- "$@" --effort "$effort"
    # Keep the MCP pair last. --mcp-config takes a list of paths, so any later
    # value that does not begin with a dash is read as a second config file.
    [ -n "$mcpfile" ] && set -- "$@" --strict-mcp-config --mcp-config "$mcpfile"
    # Only set CLAUDE_CONFIG_DIR for a non-default profile. Setting it to
    # ~/.claude makes the CLI look for a hashed keychain entry that does not
    # exist, and a signed-in account then reports itself signed out.
    if [ "$dir" = "$HOME/.claude" ]; then unset CLAUDE_CONFIG_DIR
    else CLAUDE_CONFIG_DIR="$dir"; export CLAUDE_CONFIG_DIR; fi
    sw_run "$tmo" "$@"; rc=$?
    ;;
  codex|codex2|codex3)
    [ "$(cfgbool ".$worker.enabled")" = "true" ] || { echo "second-wind: $worker is disabled in config" >&2; exit 2; }
    need_bin codex
    acct=$(cfg ".$worker.account")
    # Each Codex role owns a CODEX_HOME, and with it a sign-in. Only a directory
    # of its own is exported; the default ~/.codex is left for the client to find
    # unaided, the same way CLAUDE_CONFIG_DIR is handled above. A role pointed at
    # a directory that does not exist stops here, because Codex would otherwise
    # create an empty one, meet no login, and the failure would read as the
    # worker's opinion.
    home=$(expand "$(cfg ".$worker.config_dir")")
    home=${home%/}
    # Compared by resolved path, as swlib.codex_home does, so a trailing slash
    # or a symlinked ~/.codex is still the default and never gets the variable.
    default_home=$(cd "$HOME/.codex" 2>/dev/null && pwd -P)
    if [ -n "$home" ]; then
      real_home=$(cd "$home" 2>/dev/null && pwd -P) || real_home=""
      if [ -z "$real_home" ]; then
        if [ "$home" = "$HOME/.codex" ]; then home=""
        else echo "second-wind: $worker's directory $home does not exist. Create it and sign in with CODEX_HOME=$home codex login" >&2; exit 2; fi
      elif [ -n "$default_home" ] && [ "$real_home" = "$default_home" ]; then
        home=""
      fi
    fi
    if [ -n "$home" ]; then CODEX_HOME="$home"; export CODEX_HOME
    else unset CODEX_HOME; fi
    # Refuse an unsigned role here. Started anyway, Codex retries the API five
    # times with no bearer token and the 401s get logged as though they were the
    # worker's reply. `codex login status` writes to stderr and, on some builds,
    # exits 0 while saying "Not logged in", so both the exit code and the text
    # are read.
    login_out=$(codex login status 2>&1); login_rc=$?
    if [ "$login_rc" -ne 0 ] || printf '%s' "$login_out" | grep -qi "not logged in"; then
      echo "second-wind: $worker is not signed in${home:+ (CODEX_HOME=$home)}. Run:  ${home:+CODEX_HOME=$home }codex login" >&2
      exit 2
    fi
    # Codex prints its whole session to stdout: tool calls, diffs, reasoning and
    # server chatter. Asking it for the final message in a file gives the reply
    # on its own, so what we log and print is the answer rather than a transcript
    # that has to be scrubbed line by line afterwards.
    lastmsg=$(mktemp "${TMPDIR:-/tmp}/second-wind-last.XXXXXX") || { echo "second-wind: cannot create a temp file" >&2; exit 2; }
    set -- codex exec --skip-git-repo-check -C "$(pwd)" -o "$lastmsg"
    if [ "$mode" = review ]; then
      set -- "$@" -s read-only
    elif [ "$codex_full" = true ]; then
      # No sandbox and no approval prompts. Only when the config for this role
      # says full_access, which setup writes only when asked for by name.
      set -- "$@" --dangerously-bypass-approvals-and-sandbox
    else
      # --approve-for-me implies the workspace-write sandbox, so it cannot be
      # combined with -s
      set -- "$@" --approve-for-me
    fi
    [ -n "$model" ] && set -- "$@" -m "$model"
    [ -n "$effort" ] && set -- "$@" -c "model_reasoning_effort=\"$effort\""
    sw_run "$tmo" "$@"; rc=$?
    # The variable was for the worker alone. The refresh started at the end of
    # this script must not inherit one role's home while reading another's.
    unset CODEX_HOME
    # A run killed on timeout, or one that died early, may leave the file empty.
    # The stream is then all there is, and it is better than nothing.
    # grep, not test -s: a file holding only a newline is empty for our purposes,
    # and grep stops at the first non-space byte instead of reading it all.
    if grep -q '[^[:space:]]' "$lastmsg" 2>/dev/null; then
      reply=$(cat "$lastmsg")
    else
      printf '[second-wind] Codex wrote no final message; falling back to its stream output.\n' >&2
    fi
    ;;
  grok)
    [ "$(cfgbool '.grok.enabled')" = "true" ] || { echo "second-wind: grok is disabled in config" >&2; exit 2; }
    need_bin grok
    acct=$(cfg '.grok.account')
    grok_prompt=$(cat "$sendfile")
    if [ "$mode" = review ]; then
      set -- grok --disallowed-tools "Write,Edit,Bash"
      sandbox_note="Write, Edit and Bash disallowed"
    else
      set -- grok --permission-mode bypassPermissions
      sandbox_note="none, bypassPermissions"
    fi
    [ -n "$model" ] && set -- "$@" --model "$model"
    [ -n "$effort" ] && set -- "$@" --reasoning-effort "$effort"
    # Keep this last: a flag between -p and the prompt is parsed as the missing
    # --single value and Grok exits before doing any work.
    set -- "$@" -p "$grok_prompt"
    sw_run "$tmo" "$@"; rc=$?
    ;;
  cursor)
    [ "$(cfgbool '.cursor.enabled')" = "true" ] || { echo "second-wind: cursor is disabled in config" >&2; exit 2; }
    need_bin cursor-agent
    [ -z "$effort" ] || { echo "second-wind: --effort is not supported by cursor-agent; choose a parameterised --model instead" >&2; exit 2; }
    acct=$(cfg '.cursor.account')
    set -- cursor-agent -p --trust
    # --trust only skips the workspace prompt. It does not prevent writes.
    if [ "$mode" = review ]; then set -- "$@" --mode ask; sandbox_note="ask mode"
    else sandbox_note="none, trust with writes"; fi
    [ -n "$model" ] && set -- "$@" --model "$model"
    sw_run "$tmo" "$@"; rc=$?
    ;;
  *) rm -f "$sendfile"; echo "second-wind: unknown worker '$worker'" >&2; exit 2 ;;
esac

# The reply is recorded as the worker gave it. There used to be a grep here that
# dropped one known noisy line; with Codex now reporting its final message in its
# own file there is nothing to filter, and filtering a reply on a pattern is how
# a real error gets hidden inside the noise it was meant to remove.
fullf=$(mktemp "${TMPDIR:-/tmp}/second-wind-full.XXXXXX") || { echo "second-wind: cannot create a temp file" >&2; exit 2; }
bodyf=$(mktemp "${TMPDIR:-/tmp}/second-wind-body.XXXXXX") || { echo "second-wind: cannot create a temp file" >&2; exit 2; }
printf '%s\n' "$reply" > "$fullf"

# Cap what goes on disk. A 7MB exchange is not a record anybody reads, and a log
# directory of them is what made pruning necessary in the first place. The ledger
# still records the full size, so a capped reply is visible as one.
reply_bytes=$(wc -c < "$fullf" | tr -d ' ')
truncated=false
if command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/truncate.py" ]; then
  cap=$(python3 "$SCRIPT_DIR/truncate.py" "$fullf" "$bodyf" "$maxkb" 2>/dev/null)
else
  cap=""
fi
if [ -n "$cap" ]; then
  reply_bytes=$(printf '%s' "$cap" | jq -r '.reply_bytes')
  truncated=$(printf '%s' "$cap" | jq -r '.truncated')
else
  # Never lose the exchange over a failed cap: log it whole and say so.
  cat "$fullf" > "$bodyf"
  echo "second-wind: could not cap the exchange body, logging it in full" >&2
fi
case "$truncated" in true|false) ;; *) truncated=false ;; esac
is_int "$reply_bytes" || reply_bytes=0

{
  printf '# Delegated to %s\n\n' "$worker"
  printf -- '- When: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"
  printf -- '- Account: %s\n' "${acct:-unknown}"
  printf -- '- Mode: %s\n' "$mode"
  printf -- '- Working directory: %s\n' "$(pwd)"
  printf -- '- Model: %s\n' "${model:-default}"
  printf -- '- Effort: %s\n' "${effort:-default}"
  printf -- '- Browser guard applied: %s\n' "$([ -n "$guard" ] && echo yes || echo no)"
  printf -- '- Sandbox: %s\n' "$sandbox_note"
  printf -- '- Credentials cleared: %s\n' "${creds_cleared:-none}"
  printf -- '- Credentials kept: %s\n' \
    "$([ -n "$creds_kept" ] && echo "$creds_kept, this worker's own sign-in" || echo none)"
  printf -- '- Exit code: %s%s\n\n' "$rc" "$([ "$rc" = 124 ] && echo ' (killed on timeout)')"
  printf '## Prompt sent\n\n```\n'; cat "$sendfile"; printf '\n```\n\n'
  printf '## Reply\n\n'; cat "$bodyf"
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
      --argjson rcn "$reply_bytes" \
      --argjson trunc "$truncated" \
      --argjson guard "$([ -n "$guard" ] && echo true || echo false)" \
      --arg sb "$sandbox_note" \
  '{ts:$ts,worker:$w,account:$a,mode:$m,cwd:$cwd,model:$mdl,effort:$eff,
    browser_guard:$guard,sandbox:$sb,exit:$rc,duration_s:$dur,prompt_bytes:$pc,
    reply_bytes:$rcn,truncated:$trunc,exchange:$f}')
ledger="$LOGDIR/$(date +%Y-%m).jsonl"
printf '%s\n' "$line" >> "$ledger" || echo "second-wind: could not append to $ledger" >&2
chmod 600 "$ledger" 2>/dev/null || true

# Prune old exchanges, once the ledger line for this run is safely appended. Only
# the bodies go: the ledger is the record of what was delegated and it stays for
# good, so a pruned month still says what happened, just not in full.
find "$LOGDIR" -maxdepth 1 -type f -name '*.md' -mtime +"$prunedays" \
  -exec rm -f {} + 2>/dev/null || true

# The reply, in full, on stdout. Only the log is capped: the caller asked for
# this answer and should get all of it.
cat "$fullf"
if [ "$rc" = 0 ] && ! grep -q '[^[:space:]]' "$fullf" 2>/dev/null; then
  printf '\n[second-wind] WARNING: the worker exited 0 but returned nothing. Treat this as a failed delegation, not as agreement.\n' >&2
fi
printf '\n[second-wind] %s, %s mode, %ss, exit %s. Logged: %s\n' "$worker" "$mode" "$dur" "$rc" "$exchange" >&2
[ "$truncated" = true ] && printf '[second-wind] The logged copy of the reply was capped at %sKB. Full size: %s bytes.\n' "$maxkb" "$reply_bytes" >&2

# A delegation has just spent someone else's allowance, so the reading for that
# worker and for the primary are both out of date. Refresh them in the
# background: this run is finished and must not wait for a reader.
if [ "${SW_NO_REFRESH:-}" != 1 ] && [ -f "$SCRIPT_DIR/usage-refresh.sh" ]; then
  nohup /bin/sh "$SCRIPT_DIR/usage-refresh.sh" --force --only "$worker" primary \
    </dev/null >/dev/null 2>&1 &
fi
exit "$rc"
