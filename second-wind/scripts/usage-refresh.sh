#!/bin/sh
# Refresh the usage readings, one reader per account, all at once.
#
#   usage-refresh.sh [--force] [--only ROLE ...] [--if-claude-running]
#
#   --force               read even when the cached figure is still fresh
#   --only ROLE ...       only these roles: primary secondary codex grok cursor
#   --if-claude-running   do nothing unless Claude is actually in use, which is
#                         what the launchd timer passes
#
# Each reader writes its own cache and its own one-line status file. This script
# owns three things only: the lock, which roles are due, and catching a reader
# that died without saying why. Which roles are due comes from swlib, so the
# staleness rule lives in one place.
set -u
umask 077

SW_HOME="${SW_HOME:-$HOME/.second-wind}"
CFG="$SW_HOME/config.json"
HERE=$(CDPATH= cd "$(dirname "$0")" 2>/dev/null && pwd)
LOCK="$SW_HOME/.refresh.lock"

force=false
if_running=false
only=""
while [ $# -gt 0 ]; do
  case "$1" in
    --force) force=true ;;
    --if-claude-running) if_running=true ;;
    --only)
      shift
      while [ $# -gt 0 ]; do
        case "$1" in --*) break ;; esac
        only="$only $1"
        shift
      done
      continue ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'usage-refresh.sh: unknown option %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

[ -f "$CFG" ] || exit 0

if [ "$if_running" = true ]; then
  pgrep -x claude >/dev/null 2>&1 || pgrep -x Claude >/dev/null 2>&1 || exit 0
fi

mkdir -p "$SW_HOME" || exit 1
if ! mkdir "$LOCK" 2>/dev/null; then
  # A lock older than three minutes belonged to a run that will never finish.
  [ -n "$(find "$LOCK" -prune -mmin +3 -print 2>/dev/null)" ] || exit 0
  rmdir "$LOCK" 2>/dev/null
  mkdir "$LOCK" 2>/dev/null || exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true; exit 130' INT TERM
trap 'rmdir "$LOCK" 2>/dev/null || true' 0

# Same temp file and rename the Python side uses, so a hook reading a status
# never sees half a line.
status() {
  tmp="$SW_HOME/.status-$1.$$"
  printf '%s\n' "$2" > "$tmp" && mv "$tmp" "$SW_HOME/refresh-status-$1.txt"
}

# One call into swlib settles all three questions: where to run, which roles
# are enabled, readable and due, and whether the model picker is wanted. The
# staleness rule stays in swlib.freshness and is not reimplemented here.
plan=$(SW_HOME="$SW_HOME" SW_FORCE="$force" SW_ONLY="$only" python3 - "$HERE" <<'PY'
import os, sys
sys.path.insert(0, sys.argv[1])
import swlib
cfg = swlib.load_config()
refresh = cfg.get("refresh") or {}
force = os.environ.get("SW_FORCE") == "true"
only = os.environ.get("SW_ONLY", "").split()
print("workdir %s" % swlib.expand(refresh.get("workdir")
                                  or refresh.get("working_dir") or ""))
print("picker %s" % ("on" if refresh.get("model_picker") is True else "off"))
for role in swlib.enabled_roles(cfg):
    if only and role not in only:
        continue
    if not swlib.reading_enabled(role, cfg):
        continue
    if force or swlib.freshness(role, cfg=cfg) != "fresh":
        print("role %s" % role)
PY
)
workdir=$(printf '%s\n' "$plan" | sed -n 's/^workdir //p')
picker=$(printf '%s\n' "$plan" | sed -n 's/^picker //p')
due=$(printf '%s\n' "$plan" | sed -n 's/^role //p')

if [ -z "$workdir" ] || [ ! -d "$workdir" ]; then
  for role in primary secondary codex grok cursor; do
    status "$role" "FAILED: no readers' working directory exists. Rerun setup.py --write."
  done
  exit 1
fi
cd "$workdir" 2>/dev/null || exit 1

# A reader writes its own status file and prints the same line. This catches
# the one case it cannot: a crash before it got that far. Comparing file times
# would not do, because a status written in the same second as the marker is
# not newer than it.
run_reader() {
  role=$1
  shift
  out="$SW_HOME/.refresh-$role.out"
  err="$SW_HOME/.refresh-$role.err"
  if ! python3 "$@" >"$out" 2>"$err"; then
    case "$(sed -n '1p' "$out" 2>/dev/null)" in
      OK*|FAILED:*|"LOGIN EXPIRED:"*|"TRUST PROMPT:"*|"PARSER MISMATCH:"*) ;;
      *)
        detail=$(sed -n '1p' "$err" 2>/dev/null)
        status "$role" "FAILED: ${detail:-the usage reader stopped without saying why.}" ;;
    esac
  fi
  rm -f "$out" "$err"
}

start() {
  role=$1; binary=$2; shift 2
  if ! command -v "$binary" >/dev/null 2>&1; then
    status "$role" "FAILED: $binary is not on PATH."
    return
  fi
  (run_reader "$role" "$@" --cwd "$workdir" || true) &
  pids="$pids $!"
}

pids=""
for role in $due; do
  case "$role" in
    primary|secondary)
      start "$role" claude "$HERE/claude-usage.py" --role "$role" ;;
    codex)  start codex codex "$HERE/codex-status.py" ;;
    grok)   start grok grok "$HERE/grok-usage.py" ;;
    cursor) start cursor cursor-agent "$HERE/cursor-usage.py" ;;
  esac
done
for pid in $pids; do wait "$pid" || true; done

# The model picker describes the readings, so it runs after them.
if [ "$picker" = on ]; then
  python3 "$HERE/model-picker.py" >/dev/null 2>&1 || true
fi
exit 0
