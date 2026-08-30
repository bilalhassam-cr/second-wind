---
name: second-wind
description: Run work on a second Claude Code account or on OpenAI Codex, either as an independent adversarial reviewer or as a full-access worker, and hand the heavy lifting over automatically when the main account's usage limit is nearly spent. Use this whenever the user asks for a second opinion, an independent or outside review, an adversarial or devil's advocate read, a critic, or two competing options rather than one answer. Use it when they say they are running low on usage, close to a limit, want to save their main allowance, or want work run on their other account or on Codex. Use it also when several independent pieces of work could run at once, and for setting up or checking which account is primary and which is secondary. Reach for it even when the user does not name the tool, as long as the job is "get someone other than you to look at this" or "do not spend my main account on this".
---

# Second wind

Most people running Claude Code have more than one subscription sitting idle: a
second Claude account, a ChatGPT plan with Codex on it, or both. This routes work
to them, for two different reasons that need keeping apart.

**Independence.** A reviewer that watched you build the thing is not a reviewer.
The other accounts arrive blind, which is the whole value when you want work
challenged rather than confirmed.

**Headroom.** Each subscription has its own usage window. Moving substantial work
off the main account keeps it available for the conversation that has all the
context.

## Before anything else: is it set up?

```bash
python3 "$(dirname "$0")/scripts/setup.py" --show 2>/dev/null || echo NOT_SET_UP
```

If that says NOT_SET_UP, run the setup flow below. Do not guess which account is
which, and do not proceed without config: sending someone's main work to the
wrong subscription is worse than doing nothing.

## Setting it up

1. Discover what is on the machine:

   ```bash
   python3 scripts/discover.py
   ```

2. Show the user what was found and **ask which account should be primary**. Use
   AskUserQuestion if you have it. Frame it in their terms, not in config terms:

   > Primary is where you actually work day to day. It keeps your history and
   > your context, and it orchestrates. Secondary is the one you want to spend
   > on the heavy lifting and on independent reviews.

   The default profile (`~/.claude`) is usually primary, because it is what the
   desktop app and every plain `claude` command already use. Say that, but let
   them choose. If a profile shows `logged_in: false`, say so plainly and offer
   the sign-in step from `references/setup.md` rather than writing a broken config.

3. Write the config:

   ```bash
   python3 scripts/setup.py --write --primary ~/.claude --secondary ~/.claude-secondary
   ```

   Optional: `--codex off`, `--five-hour 85`, `--seven-day 75`,
   `--default-mode work`, `--no-failover`.

   This probes what each worker can do, writes `~/.second-wind/config.json`, and
   adds a status bar and a usage guard to both profiles' settings files, backing
   each up first and leaving every other setting untouched.

4. Tell them to restart Claude Code so the status bar appears, and that the limit
   figures only exist after the first reply in a session.

If there is no second account yet, `references/setup.md` covers creating one. It
is one directory and one sign-in.

## Running work on another account

Always through the runner. It is what records the call, and an unlogged
delegation is the thing this skill exists to prevent: a job that half worked
still returns prose that reads like success.

```bash
scripts/run.sh secondary <prompt-file> [--review|--work] [--model M] [--effort E]
scripts/run.sh codex     <prompt-file> [--review|--work] [--model M] [--effort E]
```

Write the prompt to a file first, then pass the path. Building the command inline
turns quoting into the hard part of the job.

### The two modes

**`--review` is read-only.** The worker cannot edit, write or create anything. Use
it when you want the work challenged, and whenever you are still part-way through
a job yourself: two sessions editing the same files will clobber each other, and a
reviewer that has not seen the conversation may "fix" something that was
deliberate.

**`--work` is full access.** The worker reads, writes, edits and runs commands in
the working directory, the same as you. Use it when the job is to build or fix
something, and when the point is to spend the other allowance rather than yours.

The config sets which mode is the default. Say which mode you used when reporting
back, because it changes how much weight the answer deserves.

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

The runner is safe to run in parallel; the ledger is locked. When work genuinely
splits into independent pieces, send them out together rather than in sequence:

```bash
( scripts/run.sh secondary a.txt --work > a.out 2>/dev/null ) &
( scripts/run.sh codex     b.txt --work > b.out 2>/dev/null ) &
wait
```

Subagents and workflow agents cannot be moved this way. They inherit the current
session's login and always bill the primary account. Dispatching through the
runner is what actually shifts the load.

## What a worker cannot do

**A sandboxed worker cannot launch a browser.** Codex runs seatbelt-sandboxed, and
Chrome aborts at startup inside it because it cannot reach the window server. The
user sees a "quit unexpectedly" dialog and nothing explains why. Setup probes this
once and records the answer, and the runner prepends a line to that worker's
prompts telling it not to try.

So do not delegate browser QA, screenshot verification, scroll or motion checks,
or anything using a CDP harness, puppeteer or playwright, to a worker whose
`can_launch_browser` is false. Those steps belong on the primary session, which is
not sandboxed. Check with:

```bash
python3 scripts/setup.py --show | jq '.codex.can_launch_browser'
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

One person moving their own work between their own two subscriptions between tasks
is ordinary use. Silent mid-request rotation to defeat a per-account limit is not,
and it is also just worse: you lose track of what ran where.

Turn it off with `touch ~/.second-wind/no-failover`, or `failover.enabled: false`
in the config.

## Reporting back, and the record

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
python3 scripts/report.py 7
```

## When it goes wrong

`references/troubleshooting.md` covers the failures that actually happen: a
profile that reports itself signed out when it is signed in, sign-in landing on
the wrong account, a worker that exits zero having done nothing, and the browser
crash above.
