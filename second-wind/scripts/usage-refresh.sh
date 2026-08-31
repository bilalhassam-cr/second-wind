#!/bin/sh
# Refresh each configured account without letting a dead run block later ones.
set -u
umask 077


SW_HOME="${SW_HOME:-$HOME/.second-wind}"
CFG="$SW_HOME/config.json"
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" 2>/dev/null && pwd)
LOCK="$SW_HOME/.refresh.lock"
force=false
[ "${1:-}" = "--force" ] && force=true

mkdir -p "$SW_HOME" || exit 1
if ! mkdir "$LOCK" 2>/dev/null; then
  [ -n "$(find "$LOCK" -prune -mmin +2 -print 2>/dev/null)" ] || exit 0
  rmdir "$LOCK" 2>/dev/null
  mkdir "$LOCK" 2>/dev/null || exit 0
fi
cleanup() { rmdir "$LOCK" 2>/dev/null || true; }
trap 'cleanup; exit 130' INT TERM
trap 'cleanup' 0

[ -f "$CFG" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

expand() { case "$1" in "~"/*) printf '%s' "$HOME/${1#"~/"}";; *) printf '%s' "$1";; esac; }
cfg() { jq -r "$1 // empty" "$CFG" 2>/dev/null; }
cfgbool() { jq -r "if $1 == null then \"\" else ($1|tostring) end" "$CFG" 2>/dev/null; }
status() { printf '%s\n' "$2" > "$SW_HOME/refresh-status-$1.txt"; }
is_stale() {
  [ "$force" = true ] && return 0
  file="$1"
  [ -f "$file" ] || return 0
  cached=$(jq -r '.cached_at // 0' "$file" 2>/dev/null)
  case "$cached" in ''|*[!0-9]*) return 0;; esac
  interval=$(cfg '.refresh.interval_minutes')
  case "$interval" in ''|*[!0-9]*) interval=15;; esac
  age=$(( $(date +%s) - cached ))
  [ "$age" -ge $(( interval * 60 )) ] || [ "$age" -lt -60 ]
}
reader_enabled() {
  on=$(cfgbool ".${1}.enabled")
  refresh=$(cfgbool ".refresh.${1}_enabled")
  [ "$on" = true ] && { [ -z "$refresh" ] || [ "$refresh" = true ]; }
}
run_reader() {
  role=$1; shift
  error="$SW_HOME/.refresh-$role-$$.err"
  if "$@" >/dev/null 2>"$error"; then
    status "$role" "OK: prompt-free usage panel read."
    rm -f "$error"
    return 0
  fi
  detail=$(sed -n '1p' "$error" 2>/dev/null)
  case "$detail" in
    *expired*|*login*required*|*not*signed*in*) status "$role" "LOGIN EXPIRED: sign in to $role, then rerun --accounts." ;;
    *) status "$role" "FAILED: ${detail:-the usage panel could not be read.}" ;;
  esac
  rm -f "$error"
  return 1
}
start_claude() {
  role=$1
  profile=$(expand "$(cfg ".$role.config_dir")")
  output="$SW_HOME/usage-$role.json"
  [ -d "$profile" ] || { status "$role" "MISSING PROFILE: $role profile directory does not exist."; return; }
  is_stale "$output" || return
  if ! command -v claude >/dev/null 2>&1; then
    status "$role" "FAILED: Claude Code is not available on PATH."
    return
  fi
  config_arg=$profile
  [ "$profile" = "$HOME/.claude" ] && config_arg=""
  (run_reader "$role" python3 "$SCRIPT_DIR/claude-usage.py" "$output" --config-dir "$config_arg" || true) &
  pids="$pids $!"
}
start_simple() {
  role=$1; binary=$2; script=$3
  reader_enabled "$role" || return
  output="$SW_HOME/usage-$role.json"
  is_stale "$output" || return
  if ! command -v "$binary" >/dev/null 2>&1; then
    status "$role" "FAILED: $binary is not available on PATH."
    return
  fi
  (run_reader "$role" python3 "$SCRIPT_DIR/$script" "$output" || true) &
  pids="$pids $!"
}

workdir=$(expand "$(cfg '.refresh.working_dir')")
if [ -z "$workdir" ] || [ ! -d "$workdir" ]; then
  for role in primary secondary codex grok cursor; do
    status "$role" "FAILED: no trusted working directory is configured. Rerun setup.py --write."
  done
  exit 1
fi
cd "$workdir" 2>/dev/null || exit 1

pids=""
start_claude primary
[ "$(cfgbool '.secondary.enabled')" = true ] && start_claude secondary

if reader_enabled codex && is_stale "$SW_HOME/usage-codex.json"; then
  if command -v codex >/dev/null 2>&1; then
    (python3 "$SCRIPT_DIR/codex-status.py" "$SW_HOME/usage-codex.json" \
      --status "$SW_HOME/refresh-status-codex.txt" --cwd "$workdir" >/dev/null || true) &
    pids="$pids $!"
  else
    status codex "FAILED: codex is not available on PATH."
  fi
fi
start_simple grok grok grok-usage.py
start_simple cursor cursor-agent cursor-usage.py

for pid in $pids; do wait "$pid" || true; done
exit 0
