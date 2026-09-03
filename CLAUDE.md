# second-wind

A publishable Claude Code skill. Everything here ships to strangers, so two rules
govern the repo.

## Hard constraints

- **No personal data.** No real names, email addresses, employers, account
  identifiers or machine-specific paths in any file. Config carries all of that at
  runtime. Check before every commit: `grep -riE '<your-name>|<your-domain>' .`
  The reader fixtures use `user@example.com`; keep that exclusion in the CI grep.
- **Nothing that resembles a relay.** This runs official clients, one sign-in per
  account. Do not add token multiplexing, proxying or anything that presents one
  endpoint spending several subscriptions. That is the architecture that draws
  enforcement, and it would make the tool unpublishable.
- **Never read an OAuth token.** Usage comes from driving the vendor's own client
  and parsing its panel. Anthropic's February 2026 policy forbids using consumer
  OAuth tokens in any other tool. Slower is the price and it is the right price.
- **Failover hands over at a task boundary and says so.** Never silent, never
  mid-request. If you change that, you change what the tool is.
- **Readers never press a key at a dialog they do not recognise.** They stop and
  report `TRUST PROMPT`. Every keystroke is gated on a matched screen string, not
  on elapsed time.
- **Verify by test, not by reading.** Every claim in the README about behaviour
  was reproduced on a real machine. Keep it that way.
- British English, no em dashes.

## Layout

`second-wind/` is the installable skill. `install.sh` copies it into a skills
directory, or symlinks it with `--link` when you are editing it. `examples/` and
`README.md` are for GitHub only. Tests are `python3 -m unittest discover -s
second-wind/tests`, and CI runs them alongside compileall, shellcheck and the
policy greps.

## Known traps this code already handles

Do not "simplify" these away; each cost real debugging time.

- Setting `CLAUDE_CONFIG_DIR=$HOME/.claude` breaks auth. Only set it for non-default profiles.
- A sign-in completes against whichever account the browser already holds and ignores `--email`.
- `jq '.x // empty'` returns empty for `false`. Booleans need `cfgbool`.
- `${1#~/}` tilde-expands the pattern. Quote it: `${1#"~/"}`.
- `codex login status` writes to stderr.
- `--approve-for-me` cannot be combined with `-s`.
- Codex needs `-o <file>` for its final message: without it the reply is buried in
  a whole session transcript, and filtering that transcript hides real errors.
- Codex 0.152.1 shows a directory-trust modal in an unknown directory, and on a
  trusted one spends ten to forty seconds starting MCP servers before it accepts
  `/status`. Setup pre-trusts the workdir; the reader waits rather than typing.
- A Claude session that was already running writes its own copy of the project
  list back on exit, dropping a trust entry written under it. Restart, then check.
- Claude Code reads `settings.json` at session start. Hooks, the status line and
  the picker rows only appear in the next session.
- The `modelPicker` key is only ever removed when it carries our `_second_wind`
  marker, and it needs `replaceBuiltInOptions` to show our rows.
- Never set `XAI_API_KEY` for Grok Build; it bills the separate developer API.
- Cursor's `--trust` is not read-only, and its installer takes the `agent` name.
- macOS has no `timeout(1)`; the runner has its own watchdog.
- A Codex worker is always sandboxed and cannot launch a browser, because the
  runner passes a sandbox flag on the command line in both modes and that
  overrides `sandbox_mode`. The guard is a constant, not a measurement, and
  nothing launches a browser to find out.
