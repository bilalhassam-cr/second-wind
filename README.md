# second-wind

A Claude Code skill that puts your other subscriptions to work.

Most people running Claude Code have more than one AI subscription sitting idle: a
second Claude account, a ChatGPT plan with Codex on it, or both. `second-wind`
routes work to them for two different reasons.

**Independence.** A reviewer that watched you build the thing is not a reviewer.
The other accounts arrive blind, which is the point when you want work challenged
rather than confirmed.

**Headroom.** Each subscription has its own usage window. When your main account
gets close to its limit, the heavy lifting moves across so you can keep working.

## What it does

- **Preflight that asks.** Finds every Claude profile and Codex install on the
  machine, shows you what each one actually is, and asks which is primary. It will
  not guess, because guessing sends your main work to the wrong subscription.
- **Two modes.** Read-only for adversarial review, full access for real work.
- **Automatic failover.** At 90% of your 5-hour window or 80% of your weekly one,
  work routes to the other account. Both numbers are yours to change.
- **A status bar** showing which account you are on and how much of each limit is
  left.
- **A log of everything delegated**, including the failures, because a delegation
  that quietly did nothing still returns text that reads like success.
- **A browser guard** built from a real crash, described below.

## Requirements

- Claude Code, and `jq`
- Python 3.8 or newer
- At least one of: a second Claude account, or Codex signed in to a ChatGPT plan
- macOS or Linux

## Install

```bash
git clone https://github.com/<you>/second-wind.git
cd second-wind && ./install.sh
```

Then, in a Claude Code session, say `set up second-wind`, or from a terminal:

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --discover
python3 ~/.claude/skills/second-wind/scripts/setup.py \
  --write --primary ~/.claude --secondary ~/.claude-secondary
```

Restart Claude Code for the status bar. No second account yet? It is one directory
and one sign-in: see `second-wind/references/setup.md`.

## Using it

Ask in plain language. "Get a second opinion on this before I ship it." "Have the
other account do the research." "I am nearly out of usage, move this across."

Under the hood every call goes through one runner, which is what records it:

```bash
scripts/run.sh secondary prompt.txt --review    # read-only, independent
scripts/run.sh codex     prompt.txt --work      # full access, does the job
```

Review what has been delegated:

```bash
python3 scripts/report.py 7
```

## Three things worth knowing

**Setting `CLAUDE_CONFIG_DIR` to `~/.claude` breaks authentication.** Leaving it
unset and setting it explicitly to the default directory are not the same: the
explicit form looks for a Keychain entry that does not exist and reports a
signed-in account as signed out. This skill handles it; code you add should too.

**Sign-in ignores the `--email` hint.** It completes against whichever account
your browser already holds. Open the consent URL yourself in the right browser
profile.

**A sandboxed worker cannot launch a browser.** Codex runs seatbelt-sandboxed and
Chrome aborts at startup inside it, which surfaces to you as a "quit unexpectedly"
dialog with nothing explaining why. Setup probes this once, and the runner tells
that worker not to attempt browser work. This matters most in projects whose own
instructions say to verify rendered output in a real browser, because the worker
will otherwise follow them straight into the crash.

## On terms of use

This runs the official clients, one sign-in per account, each subscription paying
its own way. That is different in kind from a relay multiplexing several
subscriptions through one endpoint, which is the pattern that draws enforcement.
Do not build one on top of this.

Failover hands over **at a task boundary and tells you it did**, rather than
rotating silently mid-request. That is partly about staying on the right side of
the line and partly because you should know which account did your work.

Sharing a login with another person is prohibited by the terms outright. One
person holding two subscriptions of their own is not.

## Uninstall

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --uninstall
rm -rf ~/.second-wind ~/.claude/skills/second-wind
```

Settings files are restored from the backups setup made. Your accounts are
untouched.

## Licence

MIT. See `LICENSE`.
