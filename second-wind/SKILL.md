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
**offer to set it up in one line before getting on with the task.** A missing
optional tool never blocks one.

## Showing usage: always the panel, never prose

**Whenever usage, limits, headroom or where to route work come up, show the
panel.** Not a sentence of percentages, not a fresh shape invented for the
occasion:

```bash
python3 "$SW/scripts/setup.py" --card
```

Print what it returns verbatim, as monospace text, every line including the
header and the age footnote. Do not redraw the bars, change their width, reorder
or drop rows, wrap it in a table, or add colour, bold or emoji. Do not restate
the figures in prose underneath either; the panel has already said them.

The bars are drawn in `swlib.usage_bar`, to scale, in eighths of a cell, so a
reading below 100% is never a full bar and 98% cannot be mistaken for spent. That
is why it is drawn in Python and not described here in words: the same reading
has to produce the same picture every time, or the panel is decoration rather
than a measurement. `·····` means the client reports no such window, which is
not zero. A row with no usable reading carries the reason instead of a bar.

The `--accounts` table is the different thing and stays as it is: it carries
plan, version, headroom and reader status, and it is for diagnosis rather than
for a glance.

## The command surface

**`/second-wind` with no arguments is the picker.** In the desktop app this is
the only way to route, since its model menu takes no rows of ours.

1. `setup.py --card` for the figures, then
   `python3 "$SW/scripts/route.py" --destinations` for each destination, its
   headroom and its presets with a line each.
2. Show them all before asking, because the question cannot list them all. Print
   the panel verbatim as above, then the presets under it, one line each, and one
   line of advice naming the destination with the most headroom. Do not redraw
   the panel to add the presets into it.
3. **AskUserQuestion**, "Where should the next tasks run?". **Four options is
   the limit and "Keep it here" takes one**, so page one is "Keep it here" plus
   the destinations with the most headroom, and when more are enabled than fit,
   the last option is "Another destination", leading to a second question with
   the rest. Three or fewer all fit, so no second page. Labels are the
   destination name only, with the usage in the description. Keep the
   recommendation on page one, "Keep it here" when this account's headroom is
   above 40%. Count the options: a dropped one is how Cursor went missing.
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
Agent Cursor Pro. Ask with **AskUserQuestion, multi-select** which to connect.
**If Codex is chosen, ask a second multi-select**: a work or team workspace, the
personal space on that same login, a separate personal account. Each becomes its
own Codex role in its own `CODEX_HOME`, with a name the user gives it; ask which
browser profile holds which account before any sign-in, and follow the Codex
section of `references/setup.md` for the flow, one directory at a time. Then
sign everything in one at a time, re-running `--detect` between each. Ask which
Claude profile is primary and which level to run at: **reviewer** is read-only
delegation with no handover, **worker** is full access plus the hooks that notice
a limit, **relief** adds handover at a task boundary. Then write it all at once:

```bash
python3 "$SW/scripts/setup.py" --write --level relief \
  --primary ~/.claude --secondary ~/.claude-secondary \
  --codex on --codex-dir ~/.codex-work --codex-label "Codex work" \
  --codex2 on --codex2-dir ~/.codex-personal --codex2-label "Codex personal" \
  --grok off --cursor off
```

**If this is a test round, ask whether to keep field notes** and recommend yes:
`--field-notes on`. Say in one line what they hold and what they never hold:
setup steps, reader outcomes, routes, handovers and the person's own notes; never
a prompt, a reply, a path, a project name or an address. Then, throughout setup
and later use, **whenever a step fails and a workaround gets past it, record it
yourself** with `python3 "$SW/scripts/setup.py" --note "..."`, one plain
sentence about the process, and at the end of setup ask the person for anything
they had to do that you did not see. `setup.py --field-report` writes the file
they send back.

Other options: `--codex3 on --codex3-dir DIR --codex3-label L`,
`--codex-full-access on`, `--reader ~/.claude-usage`, `--five-hour 85`, `--seven-day 75`,
`--refresh-minutes 30`, `--model-picker on`, `--no-launchd`, `--timeout 900`,
`--force`, and the picker flags in `references/setup.md`. Finally, tell the user
to **restart Claude Code**, then run `--check` and `--accounts`: settings are
read at session start, and a running session can write the project list back
over the workdir trust when it exits.

## Running work on another account

Always through the runner: it records the call, and an unlogged delegation is
what this skill exists to prevent, since a half-worked job still returns prose
reading like success. Write the prompt to a file, or quoting becomes the job.

```bash
"$SW/scripts/run.sh" <secondary|codex|codex2|codex3|grok|cursor> <prompt-file> [--review|--work] [--model M] [--effort E]   # cursor takes no --effort
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
step stay here. The exception is a Codex role whose config carries
`full_access: true`, set with `setup.py --write --codex-full-access on`: that
role runs work mode with no sandbox and no approval prompts, a browser can
start, and the exchange header records `Sandbox: none, full access`. Review
mode is read-only whatever the config says. Use work mode to build or fix
something, and when the point is to spend the other allowance. Say which mode
you used.

**Several Codex accounts.** `codex`, `codex2` and `codex3` are separate sign-ins,
each in its own `CODEX_HOME`. The desktop app rewrites `~/.codex/auth.json` when its user
switches workspace, so a role on that default directory changes plan without
telling anyone; the panel and `--check` say `signed in as X, not the configured
Y` when a reader sees a different account or plan from the one setup pinned.
When that appears, stop routing to that role until the sign-in is put back or,
if the change was meant, the reading is refreshed and `--write` rerun so the new
sign-in is pinned.

**Writing the prompt.** The worker starts blind. Put the question, the relevant
file contents, the constraint that matters and what finished looks like into the
prompt, and never point it at skill definitions written for another system. Open
a review with: you are reviewing work you did not produce, be direct, lead with
the single biggest problem, no compliments, and where something is wrong say
what it should be instead.

**In parallel.** Each call writes its own exchange file and one ledger line, so
parallel calls are safe: background each and `wait`. Subagents inherit this
session's login and bill the primary account, so only the runner shifts it.

## Reporting back, and the record

**First check whether it worked.** A non-zero exit means the delegation failed
and the text is an error, not an opinion. Exit 124 is the timeout, and an empty
reply with a zero exit is a failure too. Say so and do the work yourself.

1. Show the answer **verbatim**, in a quoted block, labelled with the account
   that produced it. Do not summarise or soften it.
2. Add one line of your own: whether you agree and what you would do. Where
   you disagree, say so rather than averaging.
3. Name the exchange file so the user can reopen it.
4. In `--work` mode, verify the change yourself. Reported success is not evidence.

**The calling session owns the durable record.** Each worker writes into its own
store, which this session never reads, so anything worth keeping goes into the
user's memory or a project file. Review the last week with
`python3 "$SW/scripts/report.py" 7`; add `--share` for a redacted copy to send on.

## The session brief and handover

At every level a SessionStart hook puts the panel in front of the session: a row
per account with its windows, the age of the readings, any reader fault, and
whether a route is on here. It arrives already drawn, and the rules above for
showing it apply. The desktop app runs no status line, so the brief is the only
surface.

At level relief the guard adds a line when the primary crosses its thresholds. It
is a request, not a dispatch: this session has to act on it, **at a task
boundary**, never part-way through a job already running. Say in one line that
you are handing over and which account is taking it, so the user can stop you.
If the user says keep it here, keep it here. `~/.second-wind/no-failover` turns
handover off altogether.

## Routing from the model picker

In a terminal, `/model second-wind/personal` or
`/model second-wind/codex/gpt-5.6/high` routes too. The switch is **refused on
purpose**: the session keeps its model and the next tasks go to that worker
through the runner, for this session only. Picking any normal model stops it.
The desktop app fires no hook for a typed id, so a routing name there becomes a
model the API rejects; at level relief the guard arms the route anyway and asks
for a real model. In the desktop app, `/second-wind` is the way to pick.

Never write the route files by hand: `route.py` writes one per session, in
`~/.second-wind/routes/`, so two chats cannot overwrite each other.
`route.py --show` lists every armed route when one seems to apply to the wrong
chat. `references/routing.md`, `references/setup.md`, `references/config.md` and
`references/troubleshooting.md` carry the rest.
