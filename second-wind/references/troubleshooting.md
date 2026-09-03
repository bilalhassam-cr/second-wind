# When it goes wrong

## Start here: is it actually working?

```bash
python3 "$SW/scripts/setup.py" --check
```

It prints every link in the chain, names the conditions blocking handover, and
ends in `READY`, `ARMED`, `NOT READY` or `NOT ARMED`. Almost everything below is
faster to diagnose from that output. `setup.py --accounts` adds the current
figures, and `report.py 7 --share` bundles the lot into one file to send on.

## A reading says TRUST PROMPT

The client opened a directory-trust dialog and the reader stopped rather than
pressing anything. No reader ever answers a dialog: answering one on your behalf
is a decision it has no business making, and a keystroke sent at a modal it does
not understand can do anything.

`TRUST PROMPT` and `LOGIN EXPIRED` are the only two dialogs a reader names, and
it names them by matching their wording. Any other dialog is not recognised as a
dialog at all: it just sits where the panel should be, and the reading ends as
`FAILED: usage panel did not appear` with the last of the screen attached. So
read the screen tail in the status file before assuming a parser fault.

The cure is the pre-trust setup writes. Check it:

```bash
python3 "$SW/scripts/setup.py" --check | grep -i trust
```

If a profile says `NOT TRUSTED`, restart that client and run `setup.py --write`
again. A Claude session that was already running when setup wrote the entry keeps
its own copy of the project list and can write it back on exit, which silently
drops ours. Opening the client once in `~/.second-wind/workdir` yourself and
accepting the dialog there fixes it too.

Codex 0.152.1 shows its own modal, worded differently from Claude's, and its
trust lives in `~/.codex/config.toml` as a `[projects."<path>"]` block.

## A reading says PARSER MISMATCH

The client rendered a panel that no longer carries the labels the parser expects,
which usually means the client updated. This is reported rather than guessed at,
because a wrong percentage is worse than no percentage: the guard ignores an
unreadable account instead of routing work on a number it invented.

`setup.py --check` prints the installed version next to the version second-wind
was set up against. If they differ, that is the first thing to look at. The
parsers live in `scripts/claude-usage.py`, `codex-status.py`, `grok-usage.py` and
`cursor-usage.py`, each with fixtures under `tests/fixtures`; adding the new
wording to the fixtures and the pattern is the whole fix.

## A reading says LOGIN EXPIRED

Sign that client in again. For a Claude profile, `claude auth login` in that
profile repairs the CLI credential without touching the desktop app's.

If this keeps happening to your primary account every day or two, it is the
shared-credential problem: a background reader and the desktop app taking turns
on one credential log each other out. The cure is the optional reader profile, a
third Claude profile signed into the same account, used only for reading:
`setup.py --write ... --reader ~/.claude-usage`.

## A profile says it is signed out, but it is signed in

Almost always `CLAUDE_CONFIG_DIR` set to `~/.claude`. Unset it for the default
profile. See `setup.md`.

The desktop app and CLI keep separate credentials for the same profile, so the
app can work while the CLI's Keychain entry is expired.

## The sign-in put the wrong account in the profile

The sign-in completes against whichever account the browser already holds and
ignores the `--email` hint. Back up `~/.claude.json` first, open the consent URL
in the right browser profile yourself, and check `auth status` afterwards. Full
recipe in `setup.md`.

## Nothing refreshes on its own

```bash
python3 "$SW/scripts/setup.py" --check | grep -i launchd
sh "$SW/scripts/usage-refresh.sh" --force
```

The launchd agent runs `usage-refresh.sh --if-claude-running` every 15 minutes,
and that flag makes it exit at once unless a `claude` process or the desktop app
is running. So an idle machine refreshing nothing is correct behaviour. A refresh
lock older than three minutes is discarded automatically. If the agent is written
but not loaded, rerun `setup.py --write`.

## The scheduled refresh never runs

`~/.second-wind/log/launchd.err` fills up with one line per attempt:

```
/bin/sh: /Users/you/Documents/second-wind/scripts/usage-refresh.sh: Operation not permitted
```

A LaunchAgent has no permission for `~/Documents`, `~/Desktop` or `~/Downloads`,
so it cannot read a skill installed in one of them, whether directly or through a
symlink, and the run dies before its first line. Hooks are unaffected: they run
inside Claude Code, which does have that permission, which is why an on-demand
refresh works while the scheduled one never has. So setup mirrors the refresh
script, `swlib`, the four readers and the model picker into
`~/.second-wind/runtime`, a folder launchd can always read, and points the agent
there. The mirror is refreshed by `--write`, by `--check`, and by every
hook-triggered or manual run of the skill copy, so an edited reader reaches the
schedule on the next refresh. `setup.py --check` shows it as the `runtime mirror`
row. Granting anything Full Disk Access is not needed and does not fix it.

## The status bar is blank, or shows no percentages

In a terminal the status line only appears after a Claude Code restart, and the
limit figures only exist once the account has had a reply. The desktop app runs
no status line at all, which is an open issue with Claude Code; there the session
brief is the surface, and `setup.py --accounts` is the explicit check.

No readable figure means the guard stays quiet. That is deliberate.

## A browser crashes when a worker runs

Expected, and already handled. A Codex worker is always sandboxed, because the
runner passes its own sandbox flag in both review and work mode and a
command-line sandbox overrides `sandbox_mode` in the user's Codex config. A
sandboxed process cannot reach the window server, so Chrome aborts at startup and
macOS shows a "quit unexpectedly" dialog. The runner prepends a line to the
prompt telling the worker not to attempt browser work.

Keep browser QA, screenshot verification and any CDP, puppeteer or playwright
step on the primary session. Do not "fix" this by turning the worker's sandbox
off: that trades a dialog for unrestricted disk access. If a project has its own
browser QA harness, say in that project's instructions which runtime may run it,
because the worker reads those instructions and will otherwise follow them
straight into the crash.

## A delegation exited zero but nothing happened

The likeliest failures are a bad flag and a refusal that reads like prose. Both
leave a normal-looking reply. Read the reply, not the exit code:

```bash
python3 "$SW/scripts/report.py" 7
```

The runner warns when a worker exits zero and returns nothing, and exit 124 means
it was killed on the timeout (`timeout_seconds`, 600 by default). In `--work`
mode, verify the artefact yourself.

Exchange files are capped at 200 KB and pruned after 30 days. The ledger line
stays for good, so an old month still says what was delegated even when the body
has gone.

## Grok fails or charges the wrong product

Never set `XAI_API_KEY`. It selects the separately paid `api.x.ai` developer API
instead of the SuperGrok or X Premium+ consumer allowance. The runner removes the
variable before every Grok delegation.

Grok work is `grok --permission-mode bypassPermissions -p "<prompt>"`; review is
`grok --disallowed-tools "Write,Edit,Bash" -p "<prompt>"`. The prompt must be the
argument immediately after `-p`. Grok `/usage` has a weekly window only.

## Cursor writes during a review

`--trust` is not a read-only setting. It only skips the workspace trust prompt,
and it wrote a file during live testing. Review must use
`cursor-agent -p --trust --mode ask`.

Headless work can authenticate with `CURSOR_API_KEY`, but the usage reader needs
the interactive `cursor-agent login`. On-demand may report unavailable even when
credit exists.

## `agent` opens the wrong client

Cursor's installer removes `~/.local/bin/agent` before taking that name, which
can delete Grok's alias. Use `grok` and `cursor-agent` explicitly. Reinstalling a
generic alias only makes the collision recur.

## Handover will not fire

In order: the level is `relief`; `failover.enabled` is true; there is no
`~/.second-wind/no-failover` file; the primary reading exists, is under an hour
old and carries no fault status; and the percentage is genuinely at or over the
threshold. The guard ignores a reading older than an hour because the window may
have reset since, and it ignores one whose status is `TRUST PROMPT`,
`LOGIN EXPIRED` or `PARSER MISMATCH` because the figures behind it describe
nothing. It never runs on the secondary profile.

To force it regardless, write `secondary`, `codex`, `grok`, `cursor`, `both` or
`all` to `~/.second-wind/mode`. Remove the file to go back to usage-based
handover. The model picker writes the same file as JSON, which can also carry a
model and an effort; both forms are read.

## "Model isn't available" after picking a route

The routing hook is not installed, so the pseudo-model id was treated as a real
one and the switch went through to the API. Run `setup.py --check`: it lists a
`primary PreModelSwitch` line, which says `MISSING` when the hook is not in
`settings.json`. Rerun `--write` to install it, then **restart Claude Code**,
because settings are read at session start.

From the desktop app the message means something else: its model menu runs no
PreModelSwitch hook for a typed id, so a typed `second-wind/...` or
`claude-personal` becomes the session's model and every prompt fails. Recover by
picking a real model from the menu. Route from the desktop app by asking in chat
instead. A route that is refused with "the next tasks route to ..." is the hook
working: the block is the mechanism, not a fault.

## A route says the worker is not connected

The hook only writes a routing override for a worker the config has enabled, so
picking `second-wind/grok` with Grok off is refused rather than recorded. Turn
the worker on with `--write --grok on`, or pick a different route. `--check`
lists every worker and its state.

## The model picker rows are wrong or stale

The rows are written by the refresh, so they are as old as the last reading. They
appear only with `--model-picker on`, and the key carries a `_second_wind` marker
so nothing else in `settings.json` is touched. `model-picker.py --print` shows the
line without writing it, and `model-picker.py --off` removes the key.

Setting `--model-picker off` at `--write` stops the refresh rewriting the rows,
it does not remove the key already in the file. Run `model-picker.py --off` after
it, or wait for `--uninstall`.

## Settings changes do not take effect

Claude Code reads `settings.json` at session start. A hook, status line or picker
row added by setup while a session is running appears in the next session, not
this one. Restart before concluding something is broken.

## Undo everything

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --uninstall
rm -rf ~/.second-wind ~/.claude/skills/second-wind
```

Run the uninstall **before** the `rm -rf`, because any status line it replaced is
stored under `~/.second-wind` and is put back from there.

It removes our hooks, status line and model picker key, restores the previous
status line, and unloads the launchd agent. It does not roll the whole settings
file back; setup left a timestamped copy beside the original, so a full rollback
is a manual file copy. It prints the two workdir trust entries to delete by hand,
because editing those files while a client is running risks corrupting them.

Your accounts, their logins and their own settings are untouched throughout.
