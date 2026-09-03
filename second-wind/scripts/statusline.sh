#!/bin/sh
# second-wind status bar: which account, model, effort, context, and the real
# Claude.ai 5-hour and 7-day limits.
#
# It also caches the limit figures. That is not decoration: rate_limits is handed
# to the status bar and to nothing else, so an interactive session is the only
# place those numbers can be had without opening a second one.
input=$(cat)
umask 077
SW_HOME="${SW_HOME:-$HOME/.second-wind}"
CFG="$SW_HOME/config.json"
command -v jq >/dev/null 2>&1 || exit 0

g() { printf '%s' "$input" | jq -r "$1 // empty" 2>/dev/null; }
model=$(g '.model.display_name'); effort=$(g '.effort.level')
ctx=$(g '.context_window.remaining_percentage')
version=$(g '.version')
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

# Key the role by config directory, not by email. Matching on email meant that
# if discovery could not read an address, the config held "unknown", the match
# failed, the cache was written under some other name, and the usage guard found
# no file and stayed silent. The headline feature would be off with nothing
# saying so.
here=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
role=""; plan=""; label=""
if [ -f "$CFG" ]; then
  ex() { case "$1" in "~"/*) printf '%s' "$HOME/${1#"~/"}";; *) printf '%s' "$1";; esac; }
  pd=$(ex "$(jq -r '.primary.config_dir // empty' "$CFG" 2>/dev/null)")
  sd=$(ex "$(jq -r '.secondary.config_dir // empty' "$CFG" 2>/dev/null)")
  rd=$(ex "$(jq -r '.reader.config_dir // empty' "$CFG" 2>/dev/null)")
  [ -n "$pd" ] && [ "$here" = "$pd" ] && role=primary
  [ -n "$sd" ] && [ "$here" = "$sd" ] && role=secondary
  [ -n "$rd" ] && [ "$here" = "$rd" ] && role=reader
  if [ -n "$role" ]; then
    label=$(jq -r --arg r "$role" '.[$r].label // empty' "$CFG" 2>/dev/null)
    plan=$(jq -r --arg r "$role" '.[$r].plan // empty' "$CFG" 2>/dev/null)
  fi
fi
# The account label is what the user named this account, so it is what the bar
# shows. The role, and then the sign-in, are the fallbacks.
name=${label:-$role}
[ -n "$name" ] || name=$(printf '%s' "${acct:-unknown}" | cut -d@ -f1)

t5=$(jq -r '.thresholds.five_hour_pct // 90' "$CFG" 2>/dev/null || echo 90)
t7=$(jq -r '.thresholds.seven_day_pct // 80' "$CFG" 2>/dev/null || echo 80)

tint() { n=${1%%.*}; lim=$2; lab=$3
  if [ "$n" -ge "$lim" ] 2>/dev/null; then printf '\033[31m%s\033[0m' "$lab"
  elif [ "$n" -ge $(( lim * 2 / 3 )) ] 2>/dev/null; then printf '\033[33m%s\033[0m' "$lab"
  else printf '%s' "$lab"; fi; }

out=""
[ -n "$name" ] && out="\033[2m$name\033[0m"
[ -n "$model" ] && out="$out${out:+ · }$model"
[ -n "$effort" ] && out="$out · $effort"
[ -n "$ctx" ] && out="$out · \033[2mctx $(printf '%.0f' "$ctx")%\033[0m"
[ -n "$five" ] && out="$out · $(tint "$five" "$t5" "5h $(printf '%.0f' "$five")%")"
[ -n "$week" ] && out="$out · $(tint "$week" "$t7" "7d $(printf '%.0f' "$week")%")"
if [ -n "$five" ] && [ -n "$five_r" ]; then
  n=${five%%.*}
  [ "$n" -ge "$t5" ] 2>/dev/null && hm=$(date -r "$five_r" +%H:%M 2>/dev/null || date -d "@$five_r" +%H:%M 2>/dev/null || printf '')
  [ -n "$hm" ] && out="$out \033[2m(resets $hm)\033[0m"
fi
[ -n "$out" ] || out="\033[2mno usage reported yet\033[0m"
printf '%b' "$out"

# Everything below writes the shared cache, in the one schema every reader and
# every hook uses. A profile the config does not name is not written at all: a
# file under some invented role name is read by nothing and only sprawls.
[ -n "$role" ] || exit 0
[ -n "$five" ] || [ -n "$week" ] || exit 0

# JSON numbers, or null. Percentages in the shared schema are integers, and a
# quoted "47" compared against a threshold has burned this project before.
asnum() {
  case "${1:-}" in
    ''|*[!0-9.]*) printf 'null' ;;
    *) printf '%.0f' "$1" 2>/dev/null || printf 'null' ;;
  esac
}
# Display strings for the reset fields, from the epochs the status line is given.
clock() { [ -n "${1:-}" ] || return 0
  date -r "$1" +%H:%M 2>/dev/null || date -d "@$1" +%H:%M 2>/dev/null || printf ''; }
day() { [ -n "${1:-}" ] || return 0
  { date -r "$1" '+%H:%M on %e %b' 2>/dev/null || date -d "@$1" '+%H:%M on %e %b' 2>/dev/null \
    || printf ''; } | tr -s ' '; }

mkdir -p "$SW_HOME" 2>/dev/null
f="$SW_HOME/usage-$role.json"
jq -nc --arg a "$acct" --arg r "$role" --arg p "$plan" --arg v "$version" \
      --arg m "$model" --arg e "$effort" \
      --arg r5 "$(clock "$five_r")" --arg r7 "$(day "$week_r")" \
      --argjson f5 "$(asnum "$five")" --argjson f7 "$(asnum "$week")" \
      --argjson a5 "$(asnum "$five_r")" --argjson a7 "$(asnum "$week_r")" \
      --argjson t "$(date +%s)" \
  'def orn: if . == "" then null else . end;
   {role:$r,worker:"claude",account:($a|orn),plan:($p|orn),
    five_hour_pct:$f5,seven_day_pct:$f7,
    five_hour_resets:$r5,seven_day_resets:$r7,
    five_hour_resets_at:$a5,seven_day_resets_at:$a7,
    extra:{model:$m,effort:$e},client_version:$v,cached_at:$t}' \
  > "$f.tmp.$$" 2>/dev/null && mv "$f.tmp.$$" "$f" 2>/dev/null
rm -f "$f.tmp.$$" 2>/dev/null
chmod 600 "$f" 2>/dev/null || true
printf '%s\n' "OK: interactive status line wrote this reading." \
  > "$SW_HOME/refresh-status-$role.txt" 2>/dev/null || true
exit 0
