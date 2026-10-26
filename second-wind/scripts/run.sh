#!/bin/sh
# Compatibility entry point. Both harnesses use the same Python runner.
set -eu
# shellcheck disable=SC1007
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)
if [ "$#" -lt 2 ]; then
  echo 'usage: run.sh ACCOUNT PROMPT-FILE [--review|--work] [--model M] [--effort E]' >&2
  exit 2
fi
account=$1
prompt=$2
shift 2
mode=no
for option in "$@"; do
  case "$option" in --review|--work) mode=yes ;; esac
done
if [ "$mode" = no ]; then
  selected=$(SW_RUN_ACCOUNT="$account" python3 -c 'import json,os; p=os.path.join(os.environ.get("SW_HOME",os.path.expanduser("~/.second-wind")),"config.json"); d=json.load(open(p)); a=d.get("aliases",{}).get(os.environ["SW_RUN_ACCOUNT"],os.environ["SW_RUN_ACCOUNT"]); print("grokbot" if d.get("accounts",{}).get(a,{}).get("kind")=="grokbot" else d.get("defaults",{}).get("mode","review"))')
  case "$selected" in grokbot) ;; review|work) set -- "$@" "--$selected" ;; *) echo 'second-wind: invalid defaults.mode' >&2; exit 2 ;; esac
fi
exec python3 "$HERE/second_wind.py" run "$account" --prompt-file "$prompt" --workspace "$PWD" "$@"
