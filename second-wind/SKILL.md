---
name: second-wind
description: Run work on a second Claude Code account, OpenAI Codex, Grok Build or Cursor Agent, either as an independent reviewer or as a full-access worker, and hand heavy work over at a task boundary when the main account is nearly spent. Use for second opinions, adversarial reviews, competing options, low-usage requests, account setup and independent parallel work.
---

# Second wind

Routes work to a second Claude subscription, Codex, Grok Build or Cursor Agent.
The reasons need keeping apart: **independence**, because a reviewer that watched
you build the thing is not independent, and **headroom**, because each paid plan
has its own usage window.

## First, find the scripts

A Bash tool call runs in the user's project, so relative paths and `$0` do not
resolve to this skill. Resolve the directory once, at the start of every use, and
use `$SW` in every command below:

```bash
SW=$(jq -r '.skill_dir // empty' ~/.second-wind/config.json 2>/dev/null)
SW=${SW/#\~/$HOME}
[ -d "$SW" ] || SW=$HOME/.claude/skills/second-wind
python3 "$SW/scripts/setup.py" --accounts 2>/dev/null || echo NOT_SET_UP
python3 "$SW/scripts/setup.py" --check 2>/dev/null || true
```

If that prints NOT_SET_UP, it is not configured. **Say one line offering to set it
up, then get on with what the user actually asked for.** Never let a missing
optional tool block the task: they came here to do something else.

The table answers where work should go now. It refreshes enabled prompt-free
readers, sorts eligible accounts by most headroom, and labels missing or
unreadable figures unknown. Claude and Codex use 5-hour and weekly usage, Grok
uses its weekly window, and Cursor uses its monthly pools.

If `--check` prints NOT ARMED, the delegation commands still work; only the
automatic handover is inert. Its output names the conditions blocking it.

## Interactive first run

Invoking this skill with no arguments is an interactive first run. Follow this
flow and do not print four sets of instructions at once.

1. Detect current state before asking anything:

   ```bash
   python3 "$SW/scripts/setup.py" --detect
   ```

   The JSON names existing Claude config directories and their sign-in state,
   whether `codex`, `grok` and `cursor-agent` are on PATH, and the existing
   second-wind config if there is one.

2. Explain the cost before the choice. A second Claude worker requires a second
   Claude subscription the user already holds. Codex needs a ChatGPT plan. Grok
   Build spends the SuperGrok or X Premium+ allowance already being paid for.
   Cursor Agent spends Cursor Pro. Nothing in this list is free.

3. Use AskUserQuestion as a multi-select when it is available and ask which to
   connect: **second Claude account, Codex, Grok Build, Cursor**. Any combination,
   including one, is valid. At this choice, not later, warn:

   - Never set `XAI_API_KEY` for Grok Build. `api.x.ai` is a separate paid
     developer product, while Grok Build spends the consumer subscription.
   - Cursor's installer removes `~/.local/bin/agent` and takes the generic
     `agent` name. That can delete Grok's alias. Always invoke the clients as
     `grok` and `cursor-agent`.

4. Walk each selected worker one at a time. State whether it is installed and
   signed in. Give only that worker's missing command, wait for it to finish, and
   re-run `setup.py --detect` before moving to the next. Never repeat install or
   sign-in commands that are already satisfied.

   - Second Claude: install is already satisfied when `claude_bin` is present.
     If a second profile is needed:

     ```bash
     mkdir -p ~/.claude-secondary
     CLAUDE_CONFIG_DIR="$HOME/.claude-secondary" claude auth login --claudeai
     ```

   - Codex: install with `npm i -g @openai/codex`; sign in with `codex login`;
     verify with `codex login status`.
   - Grok Build: install with
     `curl -fsSL https://x.ai/cli/install.sh | bash`; sign in with `grok login`.
     Grok has no passive login-status command. Run `grok models` first; success
     means it is already signed in. Only on failure run `grok login`, then verify
     with `grok models` again. It sends no model prompt. Never export
     `XAI_API_KEY`.
   - Cursor: install with `curl https://cursor.com/install -fsS | bash`; sign in
     interactively with `cursor-agent login`; verify with
     `cursor-agent status --format json`. `CURSOR_API_KEY` can authenticate
     headless delegated work, but it cannot read `/usage`.

5. Show the detected Claude profiles and ask which is primary if more than one
   exists. Frame the choice in the user's terms:

   > Primary is where you actually work day to day. It keeps your history and
   > your context, and it orchestrates. Secondary is the one you want to spend
   > on the heavy lifting and on independent reviews.

   The default profile (`~/.claude`) is usually primary because it is what the
   desktop app and every plain `claude` command already use. Say that, but let
   them choose. Do not write a config for a selected signed-out profile.

6. Write one config command with selected workers `on`, unselected workers `off`,
   and omit `--secondary` if a second Claude account was not selected:

   ```bash
   python3 "$SW/scripts/setup.py" --write \
     --primary ~/.claude --secondary ~/.claude-secondary \
     --codex on --grok on --cursor on
   ```

   Optional controls are `--codex-harvest off`, `--five-hour 85`,
   `--seven-day 75`, `--refresh-minutes 30`, `--default-mode work` and
   `--no-failover`.

   This records the constraints imposed by each worker command, writes
   `~/.second-wind/config.json`, and adds a status bar and a usage guard to both
   Claude profiles' settings files, backing each up first and leaving every other
   setting untouched.

7. Finish by running `setup.py --accounts` and show the complete sorted table so
   the first result after connection is where the headroom is. Then run
   `setup.py --check`. Tell the user to restart Claude Code so the status bar and
   updated hook appear.

If there is no second account yet, `references/setup.md` covers creating one. It
is one directory and one sign-in.

## Running work on another account

Always through the runner. It is what records the call, and an unlogged
delegation is the thing this skill exists to prevent: a job that half worked
still returns prose that reads like success.

```bash
"$SW/scripts/run.sh" secondary <prompt-file> [--review|--work] [--model M] [--effort E]
"$SW/scripts/run.sh" codex     <prompt-file> [--review|--work] [--model M] [--effort E]
"$SW/scripts/run.sh" grok      <prompt-file> [--review|--work] [--model M] [--effort E]
"$SW/scripts/run.sh" cursor    <prompt-file> [--review|--work] [--model M]
```

Write the prompt to a file first, then pass the path. Building the command inline
turns quoting into the hard part of the job.

### The two modes

**`--review` blocks file edits and shell work.** Claude uses
`--disallowed-tools`, which does not constrain write-capable MCP servers in that
profile. Codex uses a read-only sandbox. Grok uses
`--disallowed-tools "Write,Edit,Bash"`. Cursor uses `--mode ask`. `--trust` alone
is not read-only and wrote a file in live testing. Use review mode when you want
the work challenged and whenever you are still editing the same files.

**`--work` is full access.** The worker reads, writes, edits and runs commands in
the working directory, the same as you. Use it when the job is to build or fix
something, and when the point is to spend the other allowance rather than yours.

The config sets which mode is the default. Say which mode you used when reporting
back, because it changes how much weight the answer deserves.

The runner uses the verified command contracts. Grok work is
`grok --permission-mode bypassPermissions -p "<prompt>"`; review is
`grok --disallowed-tools "Write,Edit,Bash" -p "<prompt>"`. The prompt must follow
`-p` immediately or Grok reports that `--single` needs a value. Cursor work is
`cursor-agent -p --trust`; review adds `--mode ask`.

### Writing the prompt

The worker starts blind. Give it the question and everything it needs to answer
in the prompt text: the relevant file contents, the constraint that matters, what
finished looks like. Do not send it hunting for context it has no reason to know
about, and never point it at skill definitions written for a different system.

For a review, the framing that gets a useful answer:

> You are reviewing work you did not produce. Be direct and specific. Lead with
> the single biggest problem. No compliments, no summary of what the work does.
> If something is wrong, say what is wrong, where, and what it should be instead.

### Running several at once

The runner is safe to run in parallel: each call writes its own exchange file and
appends one short line to the ledger, which does not interleave. When work genuinely
splits into independent pieces, send them out together rather than in sequence:

```bash
( "$SW/scripts/run.sh" secondary a.txt --work > a.out 2>/dev/null ) &
( "$SW/scripts/run.sh" codex     b.txt --work > b.out 2>/dev/null ) &
( "$SW/scripts/run.sh" grok      c.txt --work > c.out 2>/dev/null ) &
wait
```

Subagents and workflow agents cannot be moved this way. They inherit the current
session's login and always bill the primary account. Dispatching through the
runner is what actually shifts the load.

## What a worker cannot do

**A sandboxed worker cannot launch a browser.** Codex runs seatbelt-sandboxed, and
Chrome aborts at startup inside it because it cannot reach the window server. The
user sees a "quit unexpectedly" dialog and nothing explains why. The browser guard
is always on for Codex because the runner passes its own sandbox flag in both review
and work mode. That command-line flag overrides `sandbox_mode`, so the user's Codex
configuration does not change the result. Nothing is launched by this check. The
runner prepends a line to the worker's prompts telling it not to try.

So do not delegate browser QA, screenshot verification, scroll or motion checks,
or anything using a CDP harness, puppeteer or playwright, to a worker whose
`can_launch_browser` is false. Those steps belong on the primary session, which is
not sandboxed. Check with:

```bash
python3 "$SW/scripts/setup.py" --show | jq '.codex.can_launch_browser'
```

This bites hardest in projects whose own instructions say to verify rendered
output in a real browser, because the worker will dutifully try.

## Automatic failover

When the primary account crosses its thresholds, a hook tells the session to route
the heavy work elsewhere. Defaults are 90% of the 5-hour window and 80% of the
weekly window; both are configurable, and the whole thing can be switched off.

The behaviour is deliberate and worth preserving if you edit this:

- It fires **at a task boundary**, never part-way through a job already running.
- It **says so in one line** before doing it, so the user knows which account did
  the work.
- If the user says keep it here, keep it here. They can see the same numbers.

Announcing the handover is not only a compliance posture, it is simply better:
silent rotation means nobody can tell which account did which piece of work. The
terms-of-use position is in the README; do not restate it here.

Turn it off with `touch ~/.second-wind/no-failover`, or `failover.enabled: false`
in the config.

The primary-only guard asks `usage-refresh.sh` to start enabled readers in the
background when needed. Every reader opens the official client's own `/usage` or
`/status` panel and sends no model prompt. Claude reads both configured profiles;
Codex, Grok and Cursor run only when those workers are enabled. The interval
defaults to 15 minutes and comes from `refresh.interval_minutes`. Failures stay
unknown and sort last. These readers start live interactive clients, so never run
them merely as a static repository test. `setup.py --accounts` is the explicit
live refresh and display path.

Codex reports percentages remaining. The parser converts them to percentages
used and reads its 5-hour, weekly and monthly credit limits, reset times, account,
plan and credit count. Monthly credits cap overage only, so the 5-hour and weekly
plan windows determine headroom.

Grok reports one weekly window and no 5-hour window. Cursor reports Included,
Auto and API monthly pools plus a reset date. On-demand may say unavailable even
when credit exists. Cursor usage requires the interactive login; a
`CURSOR_API_KEY` used for headless work does not cover it.

To force routing at every task boundary regardless of usage, write `secondary`,
`codex`, `grok`, `cursor`, `both` or `all` to `~/.second-wind/mode`. Remove that
file to return to usage-based handover. The master failover switches still take
precedence.

## Reporting back, and the record

**First check whether it actually worked.** A non-zero exit means the delegation
failed, and the text you got back is an error message or a timeout notice, not an
opinion. Exit 124 means it was killed for running too long. An empty reply with a
zero exit is also a failure. In any of those cases say so plainly and do the work
yourself; never quote a failure back as though the worker had considered the
question.

1. Show the worker's answer **verbatim** in a quoted block, labelled with which
   account produced it. Do not summarise it and do not soften it. The reason to
   ask an independent reader is to hear something you would not have said.
2. Add one line of your own: whether you agree, and what you would do. Where you
   and the worker disagree, say so plainly rather than averaging. Cross-model
   agreement is a recommendation, not a decision.
3. Name the exchange file so the user can reopen it.
4. In `--work` mode, verify the change yourself: list the file, read it back, run
   the thing. A worker reporting success is not evidence.

**The calling session owns the durable record.** Each worker writes its own
transcript into its own store, which the primary session never reads, and each has
its own memory directory that is a separate world. So anything worth keeping is
yours to write, into the user's memory or into a project file. A finding that
lives only in the delegation log is lost.

Review what has been delegated:

```bash
python3 "$SW/scripts/report.py" 7
```

## When it goes wrong

`references/troubleshooting.md` covers the failures that actually happen: a
profile that reports itself signed out when it is signed in, sign-in landing on
the wrong account, a worker that exits zero having done nothing, and the browser
crash above.
