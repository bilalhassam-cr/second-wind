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
[ -f "$SW_HOME/no-failover" ] && exit 0
command -v jq >/dev/null 2>&1 || exit 0
[ "$(jq -r '.failover.enabled // true' "$CFG")" = "true" ] || exit 0

input=$(cat 2>/dev/null)
# don't fire on the session's own machinery
case "$input" in
  *"<system-reminder>"*|*"<task-notification>"*|*"<command-name>"*) exit 0 ;;
esac

t5=$(jq -r '.thresholds.five_hour_pct // 90' "$CFG")
t7=$(jq -r '.thresholds.seven_day_pct // 80' "$CFG")
pf="$SW_HOME/usage-primary.json"
[ -f "$pf" ] || exit 0

# a stale reading is worse than none: the window may already have reset
age=$(( $(date +%s) - $(jq -r '.cached_at // 0' "$pf") ))
[ "$age" -gt 3600 ] && exit 0

five=$(jq -r '.five_hour_pct // empty' "$pf"); week=$(jq -r '.seven_day_pct // empty' "$pf")
f=${five%%.*}; w=${week%%.*}
hit5=no; hit7=no
[ -n "${f:-}" ] && [ "$f" -ge "$t5" ] 2>/dev/null && hit5=yes
[ -n "${w:-}" ] && [ "$w" -ge "$t7" ] 2>/dev/null && hit7=yes
[ "$hit5" = no ] && [ "$hit7" = no ] && exit 0

sec_acct=$(jq -r '.secondary.account // "the secondary account"' "$CFG")
sec_on=$(jq -r '.secondary.enabled // true' "$CFG")
codex_on=$(jq -r '.codex.enabled // false' "$CFG")
resets=$(jq -r '.five_hour_resets_at // empty' "$pf")
resets_txt=""
[ -n "$resets" ] && resets_txt=" The 5-hour window resets at $(date -r "$resets" +%H:%M 2>/dev/null)."

# is the secondary itself in trouble?
sf="$SW_HOME/usage-secondary.json"
sec_note=""
if [ -f "$sf" ]; then
  sfa=$(( $(date +%s) - $(jq -r '.cached_at // 0' "$sf") ))
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
