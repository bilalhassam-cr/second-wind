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

- **The `claude` CLI on your PATH.** The desktop app alone does not put it there.
  Without it, account discovery reports every profile as unknown and signed out.
- `jq`, and Python 3.8 or newer
- At least one of: a second Claude account, or the `codex` CLI signed in to a
  ChatGPT plan
- macOS or Linux

Built and tested on macOS 26.5 with Claude Code 2.1.238 and Codex 0.150.1. The
status bar needs a Claude Code build that reports `rate_limits` to the status
line; `setup.py --check` tells you whether yours does. Flags on both CLIs move,
so if a worker starts failing immediately, check that the flags in
`scripts/run.sh` still exist in your version.

## Install

```bash
git clone https://github.com/YOUR-USERNAME/second-wind.git
cd second-wind && ./install.sh
```

Then, in a Claude Code session, say `set up second-wind`, or from a terminal:

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --discover
python3 ~/.claude/skills/second-wind/scripts/setup.py \
  --write --primary ~/.claude --secondary ~/.claude-secondary
```

Create the second profile **before** running setup: it is one directory and one
sign-in, described in `second-wind/references/setup.md`. Setup refuses to write a
config for a profile that does not exist or is not signed in.

Restart Claude Code afterwards. Both the status bar and the automatic handover
stay inert until you do. Then confirm it is actually working:

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --check
```

That prints whether handover is armed, and if not, the one condition blocking it.
It is the first thing to run whenever something seems wrong.

## Using it

Ask in plain language. "Get a second opinion on this before I ship it." "Have the
other account do the research." "I am nearly out of usage, move this across."

Under the hood every call goes through one runner, which is what records it:

```bash
SW=~/.claude/skills/second-wind
"$SW/scripts/run.sh" secondary prompt.txt --review   # no edit tools, no shell
"$SW/scripts/run.sh" codex     prompt.txt --work     # full access, does the job
```

Review what has been delegated:

```bash
python3 "$SW/scripts/report.py" 7
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
dialog with nothing explaining why. The browser guard is always on for Codex
because the runner always passes its own sandbox flag in both review and work mode.
That command-line flag overrides `sandbox_mode`, so the user's Codex configuration
does not change the result. Nothing is launched by this check. The runner tells the
worker not to attempt browser work. This matters most in projects whose own
instructions say to verify rendered output in a real browser, because the worker
will otherwise follow them straight into the crash.

## On terms of use, read this before installing

Be clear-eyed about what this does. It moves your work to another account when
the first one is close to its limit. Anthropic's usage policy has a clause about
circumventing rate limits and capacity restrictions, and whether routine use of a
second subscription you pay for falls under it is not something this README can
settle for you. Read your own plan's terms and decide:

- Anthropic: <https://www.anthropic.com/legal/consumer-terms> and
  <https://www.anthropic.com/legal/aup>
- OpenAI: <https://openai.com/policies/terms-of-use>

What the design does do: official clients only, one sign-in per account, each
subscription billed to itself, no relay or proxy multiplexing several accounts
through one endpoint. Handover happens at a task boundary and announces itself,
so you always know which account did the work. Nobody outside those companies
knows how enforcement actually works, and this project makes no claim about it.

If an account is suspended, it is your account and your risk. Automation driving
a personal subscription is exactly the case worth checking your own terms on
before you install this.

## Uninstall

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --uninstall
rm -rf ~/.second-wind ~/.claude/skills/second-wind
```

Uninstall removes the two keys it added and puts back any status line it
replaced. It does not roll the whole settings file back, though setup did leave a
timestamped backup of it beside the original. **Run the uninstall before the
`rm -rf`**, because the replaced status line is stored under `~/.second-wind`.
Your accounts are untouched.

## Licence

MIT. See `LICENSE`.
