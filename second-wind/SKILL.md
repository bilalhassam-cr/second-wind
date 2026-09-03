---
name: second-wind
description: Run work on a second Claude Code account, OpenAI Codex, Grok Build or Cursor Agent, either as an independent reviewer or as a full-access worker. Use when the user wants a second opinion, an adversarial review, an independent read of something already built, or competing options from different models, and when they say they are nearly out of usage, running low on their limit, or want to spend the other account or the other subscription, and when they ask to route work to the personal account, Codex, Grok or Cursor for the next tasks. Also for /second-wind and for setting second-wind up.
---

# Second wind

Routes work to a second Claude subscription, Codex, Grok Build or Cursor Agent.
Two reasons, kept apart. **Independence**, because a reviewer that watched you
build the thing is not independent. **Not waiting**, because each plan has its own
window, so a job can start elsewhere rather than at the next reset.

## First, resolve $SW

A Bash tool call runs in the user's project, so relative paths and `$0` do not
resolve to this skill. Resolve it once and use `$SW` everywhere:

```bash
SW=$(jq -r '.skill_dir // empty' ~/.second-wind/config.json 2>/dev/null)
SW=${SW/#\~/$HOME}
[ -d "$SW" ] || SW=$HOME/.claude/skills/second-wind
python3 "$SW/scripts/setup.py" --accounts
```

`--accounts` prints cached figures and spawns nothing, sorted by headroom; add
`--live` to run the readers first, and only when the table says the readings are
stale. `--check` says whether the chain is wired up. Both print `NOT SET UP` and
exit 0 when nothing is configured, so read the output, not the exit code, and
**offer to set it up in one line before getting on with what the user asked
for.** A missing optional tool never blocks a task.

## The command surface

**`/second-wind` with no arguments is the picker.** In the desktop app this is
the only way to route, since its model menu takes no rows of ours.

1. `setup.py --accounts` for the figures, then
   `python3 "$SW/scripts/route.py" --destinations` for each destination, its
   headroom and its presets with a line each.
2. Show them before asking. If a widget or inline HTML rendering tool is
   available, render one compact card: a row per destination with its label, two
   small bars (5h and weekly, or the Cursor pools), a **most headroom** mark on
   the recommended row and the preset names under each. Otherwise print a tidy
   aligned table. Then one line of advice, such as "the personal account has the
   most headroom; Codex if you want a different model's read".
3. **AskUserQuestion**, "Where should the next tasks run?". Labels stay short,
   the destination name only, with the usage and the recommendation in the
   description. "Keep it here" is one of the options, recommended when this
   account's headroom is above 40%.
4. **AskUserQuestion** again: the presets for whatever was chosen, each option
   labelled with the preset and described by its own line.
5. And a third: "Review only (read, no edits)" or "Full access (does the work)".
6. `python3 "$SW/scripts/route.py" --set <worker> --model M --effort E --mode
   review|work --here`. Confirm in one line naming the destination, model,
   effort and mode, and say `/second-wind off` stops it.

On **Keep it here**, still ask the preset question, then say plainly that you
cannot change this session's model yourself and give the exact `/model` and
`/effort` lines to type. `references/routing.md` carries both preset tables.

**`/second-wind off`** runs `python3 "$SW/scripts/route.py" --clear`.

**`/second-wind <text>`** does the job now and asks nothing. Take the worker from
the words (personal, codex, grok, cursor, else the one with the most headroom)
and the mode from them too: review, second opinion or check means `--review`;
build, fix, write or do means `--work`. Write the prompt to a file, call the
runner, report as below.

## Setting it up

Only when `--accounts` prints `NOT SET UP`. One question at a time, one command
at a time. **Read `references/setup.md` before starting**: it carries the wizard
in full, the install and sign-in command for each client, and every trap worth
naming in the conversation.

The shape of it. Run `setup.py --detect` before asking anything. State the cost,
because nothing here is free: a second Claude worker needs a second Claude
subscription, Codex a ChatGPT plan, Grok Build SuperGrok or X Premium+, Cursor
Agent Cursor Pro. Ask with **AskUserQuestion, multi-select** which to connect,
then sign them in one at a time, re-running `--detect` between each. Ask which
Claude profile is primary and which level to run at: **reviewer** is read-only
delegation with no automatic handover, **worker** is full access plus the hooks
that notice a limit, **relief** adds handover at a task boundary when the primary
crosses its thresholds. Then write everything with one command:

```bash
python3 "$SW/scripts/setup.py" --write --level relief \
  --primary ~/.claude --secondary ~/.claude-secondary \
  --codex on --grok off --cursor off
```

Other options: `--reader ~/.claude-usage`, `--five-hour 85`, `--seven-day 75`,
`--refresh-minutes 30`, `--model-picker on`, `--picker-routes id,id`,
`--picker-models codex=gpt-5.6/high,...`, `--no-launchd`, `--timeout 900`,
`--force`. Finally, tell the user to **restart Claude Code**, then run `--check`
and `--accounts`: settings are read at session start, and a running session can
write the project list back over the workdir trust when it exits.

## Running work on another account

Always through the runner. It records the call, and an unlogged delegation is the
thing this skill exists to prevent: a job that half worked still returns prose
that reads like success. Write the prompt to a file first, since building the
command inline turns quoting into the hard part of the job.

```bash
"$SW/scripts/run.sh" <secondary|codex|grok|cursor> <prompt-file> [--review|--work] [--model M] [--effort E]   # cursor takes no --effort
```

**`--review` blocks file edits and shell work.** Claude loses its edit, write and
shell tools and loads no MCP servers, Codex runs in a read-only sandbox, Grok
drops Write, Edit and Bash, and Cursor adds `--mode ask`, since `--trust` alone
is not read-only and wrote a file in testing. Use it to have work challenged, and
whenever you are still editing the same files.

**`--work` gives the worker read and write access** to the current directory,
with the same reach you have, except Codex: work mode puts it in the
workspace-write sandbox, so it cannot write outside the directory and **cannot
launch a browser**. Browser QA, screenshots and any CDP, puppeteer or playwright
step stay on this session. Use work mode to build or fix something, and when the
point is to spend the other allowance. Say which mode you used.

**Writing the prompt.** The worker starts blind. Put the question, the relevant
file contents, the constraint that matters and what finished looks like into the
prompt, and never point it at skill definitions written for another system. Open
a review with: you are reviewing work you did not produce, be direct and
specific, lead with the single biggest problem, no compliments, and where
something is wrong say what it should be instead.

**In parallel.** Each call writes its own exchange file and appends one ledger
line, so parallel calls are safe: background each one and `wait`. Subagents
inherit this session's login and always bill the primary account, so the runner
is the only thing that shifts the load.

## Reporting back, and the record

**First check whether it worked.** A non-zero exit means the delegation failed
and the text is an error, not an opinion. Exit 124 is the timeout, and an empty
reply with a zero exit is also a failure. Say so and do the work yourself.

1. Show the answer **verbatim**, in a quoted block, labelled with the account
   that produced it. Do not summarise it and do not soften it.
2. Add one line of your own: whether you agree and what you would do. Where you
   disagree, say so rather than averaging.
3. Name the exchange file so the user can reopen it.
4. In `--work` mode, verify the change yourself. Reported success is not evidence.

**The calling session owns the durable record.** Each worker writes into its own
store, which this session never reads, so anything worth keeping goes into the
user's memory or a project file. Review the last week with
`python3 "$SW/scripts/report.py" 7`; add `--share` for a redacted copy to send on.

## The session brief and handover

At every level a SessionStart hook puts one line per account in front of the
session: the windows, their age, any reader fault, and whether a route is on for
this session. The desktop app runs no status line, so the brief is the only
surface there.

At level relief the guard adds a line when the primary crosses its thresholds. It
is a request, not a dispatch: this session has to act on it, **at a task
boundary**, never part-way through a job already running. Say in one line that
you are handing over and which account is taking it, so the user can stop you. If
the user says keep it here, keep it here. `~/.second-wind/no-failover` turns
automatic handover off altogether.

## Routing from the model picker

In a terminal, `/model second-wind/personal` or
`/model second-wind/codex/gpt-5.6/high` routes too. The switch is **refused on
purpose**: the session keeps its model and the next tasks go to that worker
through the runner, for this session only. Picking any normal model stops it.

The desktop app fires no hook for a typed id, so a routing name there becomes a
model the API rejects; at level relief the guard arms the route anyway and asks
for a real model. In the desktop app, `/second-wind` is the way to pick.

Never write `~/.second-wind/mode` by hand: `route.py` writes it, scoped to the
session that asked. `references/routing.md`, `references/setup.md`,
`references/config.md` and `references/troubleshooting.md` carry the rest.
