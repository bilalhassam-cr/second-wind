# second-wind

A publishable Claude Code skill. Everything here ships to strangers, so two rules
govern the repo.

## Hard constraints

- **No personal data.** No real names, email addresses, employers, account
  identifiers or machine-specific paths in any file. Config carries all of that at
  runtime. Check before every commit: `grep -riE '<your-name>|<your-domain>' .`
- **Nothing that resembles a relay.** This runs official clients, one sign-in per
  account. Do not add token multiplexing, proxying or anything that presents one
  endpoint spending several subscriptions. That is the architecture that draws
  enforcement, and it would make the tool unpublishable.
- **Failover hands over at a task boundary and says so.** Never silent, never
  mid-request. If you change that, you change what the tool is.
- **Verify by test, not by reading.** Every claim in the README about behaviour
  was reproduced on a real machine. Keep it that way.
- British English, no em dashes.

## Layout

`second-wind/` is the installable skill. `install.sh` copies it into a skills
directory. `examples/` and `README.md` are for GitHub only.

## Known traps this code already handles

Do not "simplify" these away; each cost real debugging time.

- Setting `CLAUDE_CONFIG_DIR=$HOME/.claude` breaks auth. Only set it for non-default profiles.
- `jq '.x // empty'` returns empty for `false`. Booleans need `cfgbool`.
- `${1#~/}` tilde-expands the pattern. Quote it: `${1#"~/"}`.
- `codex login status` writes to stderr.
- `--approve-for-me` cannot be combined with `-s`.
- macOS has no `timeout(1)`; the runner has its own watchdog.
- A sandboxed worker cannot launch a browser, so the runner warns the worker off. A capability probe must not reproduce the failure it detects; the default probe launches nothing. When inferring instead of measuring, infer from the command the code actually uses, not from a setting that command overrides.
