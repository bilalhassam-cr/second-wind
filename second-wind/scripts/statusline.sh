#!/bin/sh
# second-wind status bar: which account, model, effort, context, and the real
# Claude.ai 5-hour and 7-day limits.
#
# It also caches the limit figures. That is not decoration: rate_limits is handed
# to the status bar and to nothing else, so this is the only way any other part of
# the system can find out how much allowance is left.
input=$(cat)
SW_HOME="${SW_HOME:-$HOME/.second-wind}"
CFG="$SW_HOME/config.json"
command -v jq >/dev/null 2>&1 || exit 0

g() { printf '%s' "$input" | jq -r "$1 // empty" 2>/dev/null; }
model=$(g '.model.display_name'); effort=$(g '.effort.level')
ctx=$(g '.context_window.remaining_percentage')
five=$(g '.rate_limits.five_hour.used_percentage'); five_r=$(g '.rate_limits.five_hour.resets_at')
week=$(g '.rate_limits.seven_day.used_percentage'); week_r=$(g '.rate_limits.seven_day.resets_at')

# The default profile keeps oauthAccount in ~/.claude.json; a custom config dir
# keeps its own copy. Never assume the config dir contains it.
if [ -n "${CLAUDE_CONFIG_DIR:-}" ] && [ "$CLAUDE_CONFIG_DIR" != "$HOME/.claude" ]; then
  acctfile="$CLAUDE_CONFIG_DIR/.claude.json"
else
  acctfile="$HOME/.claude.json"
fi
acct=$(jq -r '.oauthAccount.emailAddress // empty' "$acctfile" 2>/dev/null)

role=""
if [ -f "$CFG" ]; then
  pa=$(jq -r '.primary.account // empty' "$CFG" 2>/dev/null)
  sa=$(jq -r '.secondary.account // empty' "$CFG" 2>/dev/null)
  [ -n "$acct" ] && [ "$acct" = "$pa" ] && role=primary
  [ -n "$acct" ] && [ "$acct" = "$sa" ] && role=secondary
fi
[ -n "$role" ] || role=$(printf '%s' "$acct" | cut -d@ -f1)

t5=$(jq -r '.thresholds.five_hour_pct // 90' "$CFG" 2>/dev/null || echo 90)
t7=$(jq -r '.thresholds.seven_day_pct // 80' "$CFG" 2>/dev/null || echo 80)

tint() { n=${1%%.*}; lim=$2; lab=$3
  if [ "$n" -ge "$lim" ] 2>/dev/null; then printf '\033[31m%s\033[0m' "$lab"
  elif [ "$n" -ge $(( lim * 2 / 3 )) ] 2>/dev/null; then printf '\033[33m%s\033[0m' "$lab"
  else printf '%s' "$lab"; fi; }

out=""
[ -n "$role" ] && out="\033[2m$role\033[0m"
[ -n "$model" ] && out="$out${out:+ · }$model"
[ -n "$effort" ] && out="$out · $effort"
[ -n "$ctx" ] && out="$out · \033[2mctx $(printf '%.0f' "$ctx")%\033[0m"
[ -n "$five" ] && out="$out · $(tint "$five" "$t5" "5h $(printf '%.0f' "$five")%")"
[ -n "$week" ] && out="$out · $(tint "$week" "$t7" "7d $(printf '%.0f' "$week")%")"
if [ -n "$five" ] && [ -n "$five_r" ]; then
  n=${five%%.*}
  [ "$n" -ge "$t5" ] 2>/dev/null && out="$out \033[2m(resets $(date -r "$five_r" +%H:%M 2>/dev/null))\033[0m"
fi
[ -n "$out" ] || out="\033[2mno usage reported yet\033[0m"
printf '%b' "$out"

# cache, keyed by role so the two profiles never overwrite each other
if [ -n "$five" ] || [ -n "$week" ]; then
  mkdir -p "$SW_HOME" 2>/dev/null
  f="$SW_HOME/usage-${role:-unknown}.json"
  jq -nc --arg a "$acct" --arg r "$role" --arg m "$model" --arg e "$effort" \
        --arg f5 "$five" --arg f7 "$week" --arg r5 "$five_r" --arg r7 "$week_r" \
        --argjson t "$(date +%s)" \
    '{account:$a,role:$r,model:$m,effort:$e,five_hour_pct:$f5,seven_day_pct:$f7,
      five_hour_resets_at:$r5,seven_day_resets_at:$r7,cached_at:$t}' \
    > "$f.tmp" 2>/dev/null && mv "$f.tmp" "$f" 2>/dev/null
fi
exit 0
