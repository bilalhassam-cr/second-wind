# second-wind

A Claude Code skill that puts your other subscriptions to work.

Many people running Claude Code also pay for another Claude account, a ChatGPT
plan with Codex, SuperGrok or X Premium+, or Cursor Pro. `second-wind` routes
work to those official clients for two different reasons.

**Independence.** A reviewer that watched you build the thing is not a reviewer.
The other accounts arrive blind, which is the point when you want work challenged
rather than confirmed.

**Headroom.** Each subscription has its own usage window. When your main account
gets close to its limit, the heavy lifting moves across so you can keep working.

## What it does

- **Preflight that asks.** Detects Claude profiles, Codex, Grok Build and Cursor
  Agent, explains the cost and account-specific warnings, then connects only the
  workers the user chooses.
- **Two modes.** Read-only for adversarial review, full access for real work.
- **Automatic failover.** At 90% of your 5-hour window or 80% of your weekly one,
  a hook asks the model to route the heavy work to another account, and the model
  usually complies. Both numbers are yours to change.
- **A status bar** showing which account you are on and how much of each limit is
  used.
- **A headroom table** that lists every account, puts readable current figures
  first, and leaves unavailable figures as `unknown`.
- **A log of everything delegated**, including the failures, because a delegation
  that quietly did nothing still returns text that reads like success.
- **A browser guard** built from a real crash, described below.

## Requirements

- **Claude Code 2.1.251 or newer on your PATH.** The desktop app alone does not
  put it there. Without it, account discovery reports every profile as unknown
  and signed out.
- `jq`, and Python 3.8 or newer
- At least one of: a second Claude account, Codex on a ChatGPT plan, Grok Build
  through SuperGrok or X Premium+, or Cursor Agent on Cursor Pro
- macOS. Linux is expected to work, but has not been tested.

Static script and repository policy checks have been run on macOS 26.5. Earlier
end-to-end behaviour was exercised there with Claude Code 2.1.251 and Codex
0.150.1, but against a divergent local copy. This installable repository has not
yet been clean-installed and exercised end to end. Flags on both CLIs move, so if
a worker starts failing immediately, check that the flags in `scripts/run.sh`
still exist in your version.

## Install

```bash
git clone REPOSITORY_URL second-wind
cd second-wind && ./install.sh
```

Restart Claude Code so a new session can see the copied skill. Then say `set up
second-wind`. With no setup choices supplied, the skill detects current state,
asks which workers to connect, and walks through them one at a time. It states
the cost before the choice: every option uses a paid plan or a second paid
subscription. Nothing here is free.

The guided flow gives one missing command at a time. Grok Build installs with
`curl -fsSL https://x.ai/cli/install.sh | bash`, lands at `~/.grok/bin/grok`, and
signs in with `grok login`. Cursor Agent installs with
`curl https://cursor.com/install -fsS | bash` and signs in with
`cursor-agent login`.

For a manual setup, inspect the same detection JSON and write the chosen workers:

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --detect
python3 ~/.claude/skills/second-wind/scripts/setup.py \
  --write --primary ~/.claude --secondary ~/.claude-secondary \
  --codex on --grok on --cursor on
```

Create the second profile **before** running setup: it is one directory and one
sign-in, described in `second-wind/references/setup.md`. Setup refuses to write a
config for a profile that does not exist or is not signed in.

After setup writes the profile settings, restart Claude Code once more. Then see
where the available headroom is and confirm automatic handover is armed:

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --accounts
python3 ~/.claude/skills/second-wind/scripts/setup.py --check
```

`--accounts` sorts accounts by the strictest reported pool. Claude and Codex use
their 5-hour and weekly windows, Grok uses its weekly window, and Cursor uses its
Included, Auto and API monthly pools. Missing or unreadable figures are `unknown`
and sort last. The aligned output has no colour. `--check` verifies every
installed status line, guard entry and enabled worker command.

All four readers open the client's own usage or status panel without sending a
model prompt. Claude and Codex show 5-hour and weekly usage. Grok has a weekly
window only. Cursor reports Included, Auto and API pools plus the reset date.

## Using it

Ask in plain language. "Get a second opinion on this before I ship it." "Have the
other account do the research." "I am nearly out of usage, move this across."

To answer "where should work go right now" directly:

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --accounts
```

Under the hood every call goes through one runner, which is what records it:

```bash
SW=~/.claude/skills/second-wind
"$SW/scripts/run.sh" secondary prompt.txt --review   # no edit tools, no shell
"$SW/scripts/run.sh" codex     prompt.txt --work     # full access, does the job
"$SW/scripts/run.sh" grok      prompt.txt --review
"$SW/scripts/run.sh" cursor    prompt.txt --work
```

Claude review mode blocks its built-in edit and shell tools, but it cannot
constrain write-capable MCP servers configured in the secondary profile. Codex
uses a read-only sandbox. Grok removes Write, Edit and Bash. Cursor uses
`--mode ask`. Cursor's `--trust` flag alone is not read-only and was observed
writing a file during testing.

Work mode uses the client's unattended work command in the current directory.
Do not point it at a directory you would not let an unattended agent modify.

To force every task boundary to a worker regardless of usage, write `secondary`,
`codex`, `grok`, `cursor`, `both` or `all` to `~/.second-wind/mode`. Remove the
file to return to usage-based handover. The `no-failover` file and
`failover.enabled: false` remain master switches.

Review what has been delegated:

```bash
python3 "$SW/scripts/report.py" 7
```

## Things worth knowing

**Setting `CLAUDE_CONFIG_DIR` to `~/.claude` breaks authentication.** Leaving it
unset and setting it explicitly to the default directory are not the same: the
explicit form looks for a Keychain entry that does not exist and reports a
signed-in account as signed out. This skill handles it; code you add should too.

**Claude usage is read without a prompt.** The reader opens `/usage`, parses the
account's own panel and exits. It spends no model allowance. The status line can
also update the same cache during an ordinary terminal session.

**Never set `XAI_API_KEY` for Grok Build.** The `api.x.ai` developer API is a
separate paid product. Grok Build must use the consumer allowance included with
SuperGrok or X Premium+. The runner explicitly removes this variable.

**Always call the clients `grok` and `cursor-agent`.** Cursor's installer removes
`~/.local/bin/agent` and takes that generic name, which can delete Grok's `agent`
alias. Grok's `grok` command survives.

**Cursor has two authentication paths.** Headless delegated work can use
`CURSOR_API_KEY`. Reading `/usage` requires the interactive browser login, which
the key does not cover. On-demand can report unavailable even when the account
holds credit.

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

**Codex usage comes from `/status`.** Codex reports percentages remaining;
second-wind converts them to percentages used so all accounts use the same
measure. It reads the 5-hour, weekly and monthly credit limits, the account and
plan, and the credit count. Monthly credits only cap overage. Spending them does
not mean the plan windows are exhausted, so the 5-hour and weekly windows decide
Codex headroom.

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

Uninstall removes the status-line and guard entries it added and puts back any
status line it replaced. It does not roll the whole settings file back, though
setup did leave a timestamped backup of it beside the original. **Run the uninstall before the
`rm -rf`**, because the replaced status line is stored under `~/.second-wind`.
Your accounts are untouched.

## Licence

MIT. See `LICENSE`.
