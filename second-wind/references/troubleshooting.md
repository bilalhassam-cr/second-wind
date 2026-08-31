# When it goes wrong

## A profile says it is signed out, but it is signed in

Almost always `CLAUDE_CONFIG_DIR` set to `~/.claude`. Unset it for the default
profile. See `setup.md`.

The desktop app and CLI keep separate credentials for the same profile. The app
can work while the CLI's Keychain entry is expired. Run `claude auth login` in
that profile to repair the CLI credential without changing the app login.

## The sign-in put the wrong account in the profile

See `setup.md`. Back up `~/.claude.json` first, open the consent URL in the right
browser profile yourself, and check `auth status` afterwards.

## A browser crashes when a worker runs

Expected, and already handled. A sandboxed worker cannot launch a browser: it
aborts at startup because it cannot reach the window server, and the operating
system shows a "quit unexpectedly" dialog. The browser guard is always on for
Codex because the runner passes its own sandbox flag in both review and work mode.
That command-line flag overrides `sandbox_mode`, so the user's Codex configuration
does not change the result. The check launches nothing. If a browser still crashes
during delegated work:

```bash
python3 scripts/setup.py --show | jq '.codex'
python3 scripts/probe.py
```

The second command reports the constant imposed by the runner. It launches
nothing. `can_launch_browser` is always false when Codex is installed because
every runner invocation is sandboxed. Keep browser work on the primary session.
Do not "fix" it by disabling the worker's sandbox: that trades a dialog for
unrestricted disk access.

If a project has its own browser QA harness, put a line in that project's
instructions saying which runtime may run it. The worker reads those instructions
and will otherwise follow them into the crash.

## A delegation exited zero but nothing happened

The likeliest failures are a bad flag and a refusal that reads like prose. Both
leave a normal-looking reply. The exchange file holds the full text:

```bash
python3 scripts/report.py 7
```

Read the reply, not the exit code. In `--work` mode, verify the artefact.

## Start here: is it actually working?

```bash
python3 "$SW/scripts/setup.py" --check
```

It prints every link in the chain and names the conditions that are blocking
handover. Almost everything below is faster to diagnose from that output.

## The status bar is blank, or shows no percentages

The status line updates usage during an ordinary terminal session. The desktop
app does not run status lines, so use the prompt-free account reader there:

```bash
python3 "$SW/scripts/setup.py" --accounts
```

Claude, Codex, Grok and Cursor readers open the official client's `/usage` or
`/status` panel without sending a model prompt. The default refresh interval is
15 minutes. The table states each cached age, leaves failed readings `unknown`,
and sorts them last.

If it says `LOGIN EXPIRED`, sign in to that client. If it says `VERSION TOO OLD`,
upgrade Claude Code to 2.1.251 or newer. Every account has its own
`refresh-status-<role>.txt`, so one successful account cannot hide another one's
failure.

The refresher starts from a working directory already trusted by primary. If that
directory has moved, rerun setup so it can find another trusted entry. A refresh
lock older than three minutes is discarded automatically.

In a normal terminal session, the bar only appears after a Claude Code restart
and the limit figures only exist once the account has had a reply. No readable
figure means the guard stays quiet, which is deliberate.

## Codex has no usage reading

`codex-status.py` opens a throwaway Codex TUI, sends `/status`, and exits without
sending a prompt. It gives the PTY a real window size because the panel otherwise
does not render wide enough to parse. If a startup modal swallows `/status`, the
script presses Escape and retries once. A second failure names a plugin trust
prompt or another startup modal as the likely cause instead of waiting forever.

Codex reports percentages remaining; the cache converts them to used. Monthly
credits only cap overage. Even when those credits are spent, the 5-hour and weekly
plan windows are the figures that determine whether Codex has headroom.

## Grok fails or charges the wrong product

Never set `XAI_API_KEY`. It selects the separately paid `api.x.ai` developer API
instead of the SuperGrok or X Premium+ consumer allowance. The runner removes the
variable before every Grok delegation.

Grok work is `grok --permission-mode bypassPermissions -p "<prompt>"`; review is
`grok --disallowed-tools "Write,Edit,Bash" -p "<prompt>"`. The prompt must be the
argument immediately after `-p`. Grok `/usage` has a weekly window only.

## Cursor writes during a review

`--trust` is not a read-only setting. It only skips the workspace trust prompt
and wrote a file during live testing. Review must use
`cursor-agent -p --trust --mode ask`.

Headless work can authenticate with `CURSOR_API_KEY`, but the usage reader needs
the interactive `cursor-agent login`. On-demand may report unavailable even when
credit exists.

## `agent` opens the wrong client

Cursor's installer removes `~/.local/bin/agent` before taking that name, which
can delete Grok's alias. Use `grok` and `cursor-agent` explicitly. Reinstalling a
generic alias only makes the collision recur.

## Failover will not fire

In order: `failover.enabled` true in the config; no `~/.second-wind/no-failover`
file; `~/.second-wind/usage-primary.json` exists and is under an hour old; the
percentage is genuinely at or over the threshold. The guard ignores readings older
than an hour because a window may have reset since. It starts only enabled usage
readers in the background on primary prompts and never runs failover logic on the
secondary profile. No readable primary figure means the guard stays quiet.

## Undo everything

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --uninstall
rm -rf ~/.second-wind ~/.claude/skills/second-wind
```

Run the uninstall **before** the `rm -rf`, because any status line it replaced is
stored under `~/.second-wind` and is put back from there.

It removes the status-line and usage-guard entries it added and restores whatever
status line was there before. It does not roll the whole
settings file back to how it was. Setup left a timestamped copy of the original
beside it, so a full rollback is a manual file copy if you ever want one.

Your accounts, their logins and their own settings are untouched throughout.
