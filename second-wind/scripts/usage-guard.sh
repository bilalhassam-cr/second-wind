#!/bin/sh
# UserPromptSubmit hook: when the primary account is close to its limit, tell the
# session to hand the next task to another account.
#
# It fires at a task boundary and says so, rather than rerouting silently. That
# distinction matters: one person moving their own work to their own second
# subscription between tasks is ordinary use, whereas silent mid-request rotation
# is the shape that looks like defeating per-account limits. It is also simply
# more useful to know which account did the work.
#
# Switch off:  touch ~/.second-wind/no-failover     (or set failover.enabled false)
set -u
SW_HOME="${SW_HOME:-$HOME/.second-wind}"
CFG="$SW_HOME/config.json"
[ -f "$CFG" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0
# jq's // treats false as absent, so `.failover.enabled // true` reads a
# deliberate false as true and failover could not be switched off in config.
cfgbool() { jq -r "if $1 == null then \"\" else ($1|tostring) end" "$CFG"; }
expand() { case "$1" in "~"/*) printf '%s' "$HOME/${1#"~/"}";; *) printf '%s' "$1";; esac; }

# Setup installs this hook into both profiles so uninstall remains symmetric, but
# the cache below is deliberately the primary cache. Letting the secondary run it
# would describe the primary's figures as its own and could tell it to route work
# to itself. An unset CLAUDE_CONFIG_DIR is the default primary profile.
primary_dir=$(expand "$(jq -r '.primary.config_dir // empty' "$CFG")")
current_dir=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
# Compare resolved paths, so a symlinked or differently spelled config dir does
# not silently disarm the guard in a session that really is the primary.
resolve() { [ -d "$1" ] && (cd "$1" 2>/dev/null && pwd -P) || printf '%s' "${1%/}"; }
[ -n "$primary_dir" ] && [ "$(resolve "$current_dir")" = "$(resolve "$primary_dir")" ] || exit 0

[ -f "$SW_HOME/no-failover" ] && exit 0
fo=$(cfgbool '.failover.enabled'); [ "$fo" = "false" ] && exit 0
is_int() { case "${1:-}" in ''|*[!0-9]*) return 1;; *) return 0;; esac; }
# BSD date takes -r, GNU date takes -d @epoch
hm() { date -r "$1" +%H:%M 2>/dev/null || date -d "@$1" +%H:%M 2>/dev/null || printf ''; }

input=$(cat 2>/dev/null)
# don't fire on the session's own machinery
case "$input" in
  *"<system-reminder>"*|*"<task-notification>"*|*"<command-name>"*) exit 0 ;;
esac

# A mode file is an explicit task-boundary override, so it is handled before any
# usage reading. It still honours the master failover switches above, and invalid
# contents stay silent rather than routing work somewhere the user did not name.
manual_mode=""
[ -f "$SW_HOME/mode" ] && manual_mode=$(tr -d '[:space:]' < "$SW_HOME/mode" 2>/dev/null)
case "$manual_mode" in
  secondary) manual_workers="the secondary Claude account" ;;
  codex) manual_workers="Codex" ;;
  both) manual_workers="the secondary Claude account and Codex" ;;
  *) manual_workers="" ;;
esac
if [ -n "$manual_workers" ]; then
  msg="[second-wind] Manual routing override '$manual_mode' is active.

Before starting the task in this message, tell the user in one line that you are routing
the heavy work to $manual_workers, then send the substantial pieces there through
second-wind. Keep orchestrating, reconciling and deciding on this account.

Hand over at this task boundary, never part-way through a job already running here. If
the user tells you to keep the work on this account, do that without arguing. Remove
$SW_HOME/mode to return to usage-based handover."
  jq -n --arg m "$msg" '{hookSpecificOutput:{hookEventName:"UserPromptSubmit",additionalContext:$m}}'
  exit 0
fi

t5=$(jq -r '.thresholds.five_hour_pct // 90' "$CFG"); is_int "$t5" || t5=90
t7=$(jq -r '.thresholds.seven_day_pct // 80' "$CFG"); is_int "$t7" || t7=80
pf="$SW_HOME/usage-primary.json"
[ -f "$pf" ] || exit 0

# a stale reading is worse than none: the window may already have reset
cached=$(jq -r '.cached_at // 0' "$pf"); is_int "$cached" || exit 0
age=$(( $(date +%s) - cached ))
# ignore readings older than an hour (the window may have reset) and any reading
# stamped in the future (a corrupt cache would otherwise look permanently fresh)
[ "$age" -gt 3600 ] && exit 0
[ "$age" -lt -60 ] && exit 0

five=$(jq -r '.five_hour_pct // empty' "$pf"); week=$(jq -r '.seven_day_pct // empty' "$pf")
f=${five%%.*}; w=${week%%.*}
hit5=no; hit7=no
[ -n "${f:-}" ] && [ "$f" -ge "$t5" ] 2>/dev/null && hit5=yes
[ -n "${w:-}" ] && [ "$w" -ge "$t7" ] 2>/dev/null && hit7=yes
[ "$hit5" = no ] && [ "$hit7" = no ] && exit 0

sec_acct=$(jq -r '.secondary.account // "the secondary account"' "$CFG")
sec_on=$(cfgbool '.secondary.enabled'); [ -n "$sec_on" ] || sec_on=true
codex_on=$(cfgbool '.codex.enabled'); [ -n "$codex_on" ] || codex_on=false
resets=$(jq -r '.five_hour_resets_at // empty' "$pf")
resets_txt=""
[ -n "$resets" ] && resets_txt=" The 5-hour window resets at $(hm "$resets")."

# Delegated secondary work uses print mode, which runs no status line. A secondary
# reading therefore comes only from a separate interactive session and is usually
# absent or behind the work delegated since it was written.
sf="$SW_HOME/usage-secondary.json"
sec_note=""
[ "$sec_on" = "true" ] && sec_note=" Note: secondary usage is usually unknown because delegated print-mode sessions do not run a status line. Do not assume it has headroom."
if [ -f "$sf" ]; then
  sfc=$(jq -r '.cached_at // 0' "$sf"); is_int "$sfc" || sfc=0
  sfa=$(( $(date +%s) - sfc ))
  if [ "$sfa" -lt 86400 ]; then
    s5=$(jq -r '.five_hour_pct // empty' "$sf"); s5=${s5%%.*}
    [ -n "${s5:-}" ] && [ "$s5" -ge "$t5" ] 2>/dev/null && \
      sec_note=" Note: the secondary account is also at ${s5}% of its 5-hour window, so it may not have headroom either. Say so rather than assuming it is fresh."
  fi
fi

which=""
[ "$hit5" = yes ] && which="the 5-hour window is ${f}% spent (threshold ${t5}%)"
[ "$hit7" = yes ] && which="${which:+$which and }the weekly window is ${w}% spent (threshold ${t7}%)"

workers="the secondary Claude account ($sec_acct)"
[ "$sec_on" = "true" ] || workers=""
[ "$codex_on" = "true" ] && workers="${workers:+$workers, or }Codex"
[ -n "$workers" ] || exit 0

msg="[second-wind] This account is running low: ${which}.${resets_txt}${sec_note}

Before starting the task in this message, tell the user in one line that you are routing
the heavy work to ${workers}, then do it: send the substantial pieces out through
second-wind rather than doing them here. Keep orchestrating, reconciling and deciding
on this account, since that is cheap and it holds the conversation.

Two things to respect. Hand over at this task boundary, never part-way through a job
already running here. And if the user tells you to keep the work on this account, do that
without arguing: they can see the same numbers you can."

jq -n --arg m "$msg" '{hookSpecificOutput:{hookEventName:"UserPromptSubmit",additionalContext:$m}}'
exit 0
