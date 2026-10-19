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
directory, or symlinks it with `--link` when you are editing it; both set the
executable bit on the shell scripts, `setup.py` and the hooks, which Claude Code
runs by absolute path. Nothing else needs one: the rest is imported by python3. `examples/` and
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
- A Codex worker is sandboxed and cannot launch a browser, because the runner
  passes a sandbox flag on the command line in both modes and that overrides
  `sandbox_mode`. The guard is decided from the flags, not measured, and nothing
  launches a browser to find out. The one exception is a role with
  `full_access: true` in work mode, which runs with no sandbox and no guard.
- macOS `/bin/sh` cannot parse a bare `)` in a `case` pattern inside `$( )`. The
  script still runs, and every line after the substitution in that block is
  silently lost. Compute the value into a variable first.
- The Codex desktop app owns `~/.codex` and rewrites `auth.json` on a workspace
  switch, keeping the same email and changing the plan. Each Codex role has its
  own `CODEX_HOME`; the drift check compares plan as well as account for Codex.
- From 0.158 Codex draws full screen and sends only the cells that change, each
  run placed by a cursor jump. Read in the order painted, a row arrives in
  pieces and a letter already on screen is never sent again, so the Codex
  reader parses the screen as shown (`ptyreader.render`) and looks for dialogs
  in both readings. A full-screen Codex gets no key unless its input line is
  on screen.
- Codex 0.159.2 and 0.160.0 ask "Trust this folder?" in a git folder, with
  "Trust and continue" already selected, so one Enter trusts it. Both wordings
  are in `TRUST`.
- Codex renamed its Pro plans in 0.158 and again in 0.160, and a bare "Pro" has
  meant two plans. The reader stores each plan under its first name
  (`plan_name`); taken as printed, every pinned Pro Lite read as a sign-in
  change. Behind its background server the panel names no address, so a Codex
  reading's account is unknown.
- Skip on the Codex update dialog leaves an "Update available!" notice above
  the input line. The words outlive the dialog, so a dialog is answered only
  while the input line is off screen; answering the words typed "22" into the
  composer and every Codex reading failed from 9 to 18 October 2026.
- Every open Claude app session writes the primary's reading each minute, with
  the windows from its own last response, so an idle session carries old
  figures. `statusline.sh` drops a reading that is lower than the one on file
  for the same window. Without a terminal sign-in the primary is read only this
  way, and `claude-usage.py` keeps that reading rather than asking for a login.
- launchd runs an overdue timer in a dark wake, when the Mac is asleep and has
  no display and often no network. Most refresh failures from 9 to 18 October
  2026 were Grok, Cursor and the second Claude failing together then.
  `--if-claude-running` skips a wake whose `pmset -g systemstate` has no
  Graphics.
- Claude 2.1.285 paints the last figures it fetched at once, of any age, says
  "Refreshing" and then repaints them or says "Showing last-known usage as of
  44m ago (rate limited ...)". Read at the first figure, those old ones were
  saved as current. The reader waits for the refresh to end and saves nothing
  when the figures are last-known. On 18 October 2026, after about fifteen
  forced refreshes in an hour, the server rate-limited the account for over an
  hour, and every reading in that time was last-known. An hour after the last
  fetch the tab drops the old figures and says only "Error: Usage endpoint is
  rate limited". Every failed form ends in an "r to retry" footer.
- A client that cannot reach its server says so where the panel would be. Each
  reader matches that wording and reports it, and the Claude reader presses
  Right only while nothing of the Usage tab is on screen: Right from a tab
  still loading moved it on to Config and Status.
