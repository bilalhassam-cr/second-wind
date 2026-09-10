# second-wind

A Claude Code skill that puts your other subscriptions to work.

Many people running Claude Code also pay for a second Claude account, a ChatGPT
plan with Codex, SuperGrok or X Premium+, or Cursor Pro. `second-wind` routes
work to those official clients, for two reasons that are worth keeping apart.

**Independence.** A reviewer that watched you build the thing is not a reviewer.
The other accounts arrive blind, which is the point when you want work challenged
rather than confirmed.

**Not waiting.** Each subscription has its own window. Claude Code (2.1.234 and
later) waits for a limit to reset and continues on its own; that version comes
from Anthropic's changelog, not from anything this repository tests. second-wind
is for starting the job now, on an account that has room, and knowing which
account did it.

## What it does

- **A setup wizard that asks.** It detects the Claude profiles, Codex, Grok Build
  and Cursor Agent, states the cost of each, and connects only what you choose.
  Codex can be up to three sign-ins, a work workspace, the personal space on
  that login and a separate personal account, each in its own directory, and
  the wizard walks the browser side of each sign-in.
- **Three levels.** Reviewer, worker or relief, chosen at setup and changeable by
  running setup again.
- **Two modes.** Read-only for adversarial review, full access for real work.
- **A usage panel.** One row per account at the start of a session, the two
  windows drawn to scale, how old the readings are, any fault that makes one
  unreliable, and a note when a sign-in is not the one it was set up as.
- **A headroom table.** Every account sorted by the strictest window it reported,
  with unreadable figures marked unknown and sorted last.
- **A log of everything delegated**, failures included, because a delegation that
  quietly did nothing still returns text that reads like success.

## The three levels

| Level | Default mode | Handover prompt | What it installs |
|---|---|---|---|
| `reviewer` | review | off | the session brief, and routing from the model picker |
| `worker` | work | off | those, plus hooks that notice a limit and refresh the readings |
| `relief` | work | on | the above, plus the prompt guard that asks for a handover at a task boundary |

Handover is advice, not automation. At level relief the guard adds one line to
the session saying the primary is low and that the next task should go to another
account. Acting on it is the session's decision and yours: nothing in
second-wind dispatches a delegation by itself, and the line arrives between
tasks, never part-way through a job. If you say keep the work here, it stays
here.

## Requirements

- macOS. Linux is expected to work and has not been tested.
- **Claude Code 2.1.251 or newer on your PATH.** The desktop app alone does not
  put it there, and without it every profile reads as unknown and signed out.
- `jq`, and Python 3.9 or newer.
- At least one of: a second Claude account, Codex on a ChatGPT plan, Grok Build
  through SuperGrok or X Premium+, or Cursor Agent on Cursor Pro.

Tested versions:

| Client | Version tested |
|---|---|
| Claude Code | 2.1.251 |
| Codex | 0.153.0 |
| Grok | 1.0.13 |
| Cursor Agent | 2026.08.31 |
| macOS | 26 |

This release has not yet been exercised on a clean machine. The clean-install
exercise in `TESTING.md` is that test, and it has not been run.

Both CLIs move their flags between releases. If a worker starts failing straight
away, check that the flags in `second-wind/scripts/run.sh` still exist in your
version. `setup.py --check` warns when an installed client has moved on from the
version second-wind was set up against.

## Install

```bash
git clone https://github.com/bilalhassam-cr/second-wind second-wind
cd second-wind && ./install.sh
```

`install.sh` copies the skill into `~/.claude/skills/second-wind`. Pass a
directory to install somewhere else, and `--link` (before the directory) to
symlink the checkout instead of copying it, which is what you want if you are
editing the skill itself. Either way it makes sure the shell scripts, `setup.py`
and the hooks are executable, since Claude Code runs the hooks by path.

Restart Claude Code so a new session sees it, then say `set up second-wind`.

## Setup

The wizard detects what is on the machine, states the cost of each option before
you choose, asks which workers to connect and which level you want, then gives
one missing command at a time. To do it by hand:

```bash
SW=~/.claude/skills/second-wind
python3 $SW/scripts/setup.py --detect
python3 $SW/scripts/setup.py --write --level relief \
  --primary ~/.claude --secondary ~/.claude-secondary \
  --codex on --codex-dir ~/.codex-work --codex-label "Codex work" \
  --grok off --cursor off
python3 $SW/scripts/setup.py --check
python3 $SW/scripts/setup.py --accounts
```

Name every worker. A worker flag left out is off, so nothing gets enabled just
because it happens to be installed and signed in.

Create the second profile before running setup: it is one directory and one
sign-in, described in `second-wind/references/setup.md`. A primary that does not
report a terminal login is a warning, because the desktop app can be signed in
while the CLI check says otherwise. A secondary or reader that is not signed in
is a refusal unless you pass `--force`, and a profile directory that does not
exist is always a refusal.

`--write` also creates `~/.second-wind/workdir`, an empty directory the usage
readers run in, and marks it trusted in each Claude profile and in the Codex
config. Without that, a reader meets a trust prompt instead of a usage panel.
**Restart Claude Code afterwards**: a session that was already running holds its
own copy of the project list and can write it back over the trust entry on exit.
`--check` reads the trust back rather than assuming it survived.

A third Claude profile, `--reader ~/.claude-usage`, is optional and signed into
the same account as your primary. It exists because a background reader sharing
one credential with the desktop app logged the app out every day or two.

On macOS, setup installs a launchd agent that runs
`scripts/usage-refresh.sh --if-claude-running` every 15 minutes. It does nothing
unless a `claude` process or the desktop app is running.

## Using it

Ask in plain language. "Get a second opinion before I ship this." "Have the other
account do the research." "I am nearly out of usage, move this across."

Every call goes through one runner, which is what records it:

```bash
"$SW/scripts/run.sh" secondary prompt.txt --review   # no edit tools, no shell
"$SW/scripts/run.sh" codex     prompt.txt --work     # full access, does the job
"$SW/scripts/run.sh" codex2    prompt.txt --review   # a second Codex sign-in
"$SW/scripts/run.sh" grok      prompt.txt --review
"$SW/scripts/run.sh" cursor    prompt.txt --work
```

Claude review mode drops its edit, write and shell tools and loads no MCP
servers. Codex uses a read-only sandbox. Grok drops Write, Edit and Bash. Cursor
adds `--mode ask`; its `--trust` flag alone is not read-only and was seen writing
a file during testing. Work mode is unattended and unrestricted in the current
directory, so do not point it at a directory you would not let an agent modify.

A Codex worker is sandboxed and cannot launch a browser: Chrome aborts at
startup inside the sandbox and the user sees a "quit unexpectedly" dialog with no
explanation. The runner tells the worker so in the prompt. Keep browser QA on
your own session. The one exception is a Codex role set up with
`--codex-full-access on`, which runs work mode with no sandbox at all and no
guard; review mode stays read-only whatever the flag says.

Type **`/second-wind`** and press Enter to be shown every destination with its
current usage and a recommendation, then pick: the account first, then a model
and effort preset for it, then review only or full access. It arms the route for
that session and tells you that **`/second-wind off`** stops it. Say what you
want in the same breath, as in `/second-wind get codex to review the migration`,
and it skips the questions and does the job now. In the desktop app the picker is
the only way to route, because its model menu shows no rows of ours.

You can also route from the model picker. Typing `/model second-wind/personal`,
or `/model second-wind/codex/gpt-5.6/high`, is refused on purpose: the session
keeps the model it has, and a PreModelSwitch hook records that the next tasks go
to that worker, with that model and effort, through the runner. Picking any
normal model deletes the record and hands control back to the usage figures. This
works in the terminal only. The desktop app's model menu shows no custom rows and
runs no hook for a typed id, so a typed id there becomes a model that does not
exist and every prompt fails until you pick a real one. The desktop app is covered
the other way round: at level relief the prompt guard finds the typed name in the
app's own session file, arms the route anyway, and refuses that prompt with a line
asking you to pick a normal model and send it again. In the desktop app you can
also ask in chat to route: the skill offers a picker and writes the same record.

Review what has been delegated, and produce a shareable copy:

```bash
python3 "$SW/scripts/report.py" 7
python3 "$SW/scripts/report.py" 7 --share
```

## The brief, the status line and the model picker

At every level, a SessionStart hook puts one line per account in front of the
session. In a terminal, the status line shows the same figures live. The desktop
app runs no status line at all, which is an open issue with Claude Code, so there
the brief is the only surface.

An opt-in experiment relabels the `/model` picker rows with the current figures:
`--model-picker on`. It works by writing a `modelPicker` key with
`replaceBuiltInOptions` into the primary profile's settings, marked as ours so
uninstall removes it and nothing else. The same key carries one `Route: ...` row
per connected worker, plus anything listed in `--picker-routes`. The rows are a
CLI feature: the desktop app's picker ignores them, so there you type the id.

## How usage is read, and why it is slow

Every reading comes from driving the vendor's own client: a throwaway terminal
session in the workdir, `/usage` or `/status`, parse the panel, exit. No model
prompt is sent, so no allowance is spent. Anthropic's February 2026 policy
forbids using consumer OAuth tokens in any other tool, so second-wind never
touches a token or calls a usage endpoint.

That choice has a price, and it is worth being honest about it. Driving a
terminal UI is slower than one HTTP call, it takes tens of seconds per account,
and it breaks whenever a client renames a label or adds a dialog. The readers are
built for that: every keystroke waits for a matched string on screen, a dialog
whose wording the reader recognises as a trust or login screen is reported as
`TRUST PROMPT` or `LOGIN EXPIRED`, any other dialog simply keeps the panel from
appearing and the reading ends as `FAILED: usage panel did not appear`, and a
panel whose labels have moved reports `PARSER MISMATCH` rather than a wrong
number.
Faults are named per account in `~/.second-wind/refresh-status-<role>.txt`, so
one working account cannot hide another one's failure.

Tools that read the usage endpoint directly are faster and will keep being
faster. They also need your token, which is the thing the policy is about. This
one stays on the official clients and pays for it in speed.

Claude and Codex report a 5-hour and a weekly window. Grok reports a weekly
window only. Cursor reports Included, Auto and API monthly pools plus a reset
date, and on-demand can read as unavailable even when the account holds credit.
Codex reports what is left, and the parser converts it to what is used so every
account is measured the same way. Codex monthly credits cap overage only, so they
do not decide its headroom.

## What routing another account is not

Handover here is announced and happens between tasks. It is one person moving
their own work to their own second subscription, in the open, through each
vendor's own client, with each account billed to itself. There is no relay, no
proxy, no pool of tokens behind one endpoint, and nothing that presents several
subscriptions as one. Silent rotation is a different thing, and it is worse for
you as well: when nobody can tell which account did which piece of work, you
cannot audit it, reproduce it or explain it.

## From the author's ledger

The delegation log from building this, 30 August to 2 September 2026:

- 68 delegated calls, 47 to Codex and 21 to a second Claude account
- 702 minutes of worker time moved off the main account
- 6 failures, 5 of them timeouts
- 11 projects

That is four days of one person's use, not a benchmark. It is the whole record
this repository has.

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

## When it goes wrong

Run `python3 "$SW/scripts/setup.py" --check` first. It prints every link in the
chain and names what is blocking handover, and most faults are quicker to
identify from that output than from anywhere else.
`second-wind/references/troubleshooting.md` covers the rest: the `TRUST PROMPT`,
`PARSER MISMATCH` and `LOGIN EXPIRED` statuses, a blank status bar, a delegation
that exited zero having done nothing, and handover that will not fire.

## Uninstall

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --uninstall
rm -rf ~/.second-wind ~/.claude/skills/second-wind
```

Uninstall removes the hooks, the status line and the model picker key it added,
puts back any status line it replaced, and unloads the launchd agent. It does not
roll the whole settings file back, though setup left a timestamped backup beside
the original. It prints the two trust entries to remove by hand, since editing
those files while a client is running is how they get corrupted. **Run the
uninstall before the `rm -rf`**, because the replaced status line is stored under
`~/.second-wind`. Your accounts and their logins are untouched.

## Licence

MIT. See `LICENSE`.
