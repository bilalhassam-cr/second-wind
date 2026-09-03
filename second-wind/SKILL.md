---
name: second-wind
description: Run work on a second Claude Code account, OpenAI Codex, Grok Build or Cursor Agent, either as an independent reviewer or as a full-access worker. Use when the user wants a second opinion, an adversarial review, an independent read of something already built, or competing options from different models, and when they say they are nearly out of usage, running low on their limit, or want to spend the other account or the other subscription, and when they ask to route work to the personal account, Codex, Grok or Cursor for the next tasks. Also for /second-wind and for setting second-wind up.
---

# Second wind

Routes work to a second Claude subscription, Codex, Grok Build or Cursor Agent.
Two reasons, kept apart. **Independence**, because a reviewer that watched you
build the thing is not independent. **Not waiting**, because each plan has its
own window, so a job can start elsewhere rather than at the next reset.

## First, resolve $SW

A Bash tool call runs in the user's project, so relative paths and `$0` do not
resolve to this skill. Resolve it once and use `$SW` everywhere:

```bash
SW=$(jq -r '.skill_dir // empty' ~/.second-wind/config.json 2>/dev/null)
SW=${SW/#\~/$HOME}
[ -d "$SW" ] || SW=$HOME/.claude/skills/second-wind
python3 "$SW/scripts/setup.py" --accounts
```

`--accounts` prints cached figures and spawns nothing; add `--live` to run the
readers first. It sorts the accounts by headroom and says where work goes now.
`--check` says whether the chain is wired up and what is blocking it. Both print
`NOT SET UP` and exit 0 when nothing is configured, so read the output, not the
exit code, and **offer to set it up in one line before getting on with what the
user asked for.** A missing optional tool never blocks a task.

## Setting it up

Invoking this skill with no arguments is an interactive first run. One question
at a time, one command at a time.

1. Detect before asking anything: `python3 "$SW/scripts/setup.py" --detect`. The
   JSON names the Claude profiles and their sign-in state, whether `codex`,
   `grok` and `cursor-agent` are on PATH, the client versions and any config.

2. State the cost before the choice. A second Claude worker needs a second Claude
   subscription, Codex needs a ChatGPT plan, Grok Build spends SuperGrok or X
   Premium+, Cursor Agent spends Cursor Pro. Nothing here is free.

3. Ask with **AskUserQuestion, multi-select**: second Claude account, Codex, Grok
   Build, Cursor. Any combination is valid. `references/setup.md` carries the
   install and sign-in command for each, and two warnings belong in the
   conversation: never set `XAI_API_KEY` for Grok Build, since `api.x.ai` is a
   separate paid developer product, and always call the clients `grok` and
   `cursor-agent`, since Cursor's installer takes the generic name `agent` and
   can delete Grok's alias.

4. Walk the chosen workers **one at a time**, giving only the command that is
   missing, waiting for it, then re-running `--detect` before the next one. A
   Claude sign-in lands on whichever account the browser already holds and
   ignores the `--email` hint, so say which browser profile to open the printed
   URL in. Never set `CLAUDE_CONFIG_DIR` for `~/.claude` itself: it breaks a
   working sign-in.

5. Ask which Claude profile is primary if more than one is signed in. Primary is
   where the user works day to day, keeps their history and orchestrates from.
   `~/.claude` is usually it, because the desktop app and every plain `claude`
   command already use that profile.

6. Ask with **AskUserQuestion, single select**, for the level:

   - **Reviewer**: read-only delegation, no automatic handover, session brief only.
   - **Worker**: full-access delegation, plus the hooks that notice a limit.
   - **Relief**: full access, and handover at a task boundary when the primary
     crosses its thresholds.

7. Write the config with **one command**, workers `on` or `off` as chosen:

   ```bash
   python3 "$SW/scripts/setup.py" --write --level relief \
     --primary ~/.claude --secondary ~/.claude-secondary \
     --codex on --grok off --cursor off
   ```

   Other options: `--reader ~/.claude-usage`, `--five-hour 85`, `--seven-day 75`,
   `--refresh-minutes 30`, `--model-picker on`, `--picker-routes id,id`,
   `--no-launchd`, `--timeout 900`, `--force`. It writes the config, creates and
   pre-trusts `~/.second-wind/workdir` for each Claude profile and for Codex,
   installs the hooks and status line after a backup, and schedules the refresh.

8. Tell the user to **restart Claude Code**, then run `--check` and `--accounts`.
   The restart matters twice: settings are read at session start, and a running
   session can write the project list back over the workdir trust when it exits.

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
is not read-only and wrote a file in testing. Use review mode to have work
challenged, and whenever you are still editing the same files.

**`--work` gives the worker read and write access** to the current directory. A
Claude, Grok or Cursor worker runs with the same reach you have. A Codex worker
does not: work mode passes `--approve-for-me`, which puts it in the
workspace-write sandbox, so it edits files in the directory but cannot write
outside it and **cannot launch a browser**, which is why browser QA, screenshot
checks and any CDP, puppeteer or playwright step stay on this session.
`references/troubleshooting.md` says why. Use work mode to build or fix
something, and when the point is to spend the other allowance. The config sets
the default mode. Say which mode you used when reporting back.

**Writing the prompt.** The worker starts blind. Put the question, the relevant
file contents, the constraint that matters and what finished looks like into the
prompt, and never point it at skill definitions written for another system. Open
a review with: you are reviewing work you did not produce, be direct and
specific, lead with the single biggest problem, no compliments and no summary of
what the work does, and where something is wrong say what it should be instead.

**In parallel.** Each call writes its own exchange file and appends one ledger
line, so parallel calls are safe: background each one and `wait`. Subagents
inherit this session's login and always bill the primary account, whatever they
are told, so the runner is the only thing that shifts the load.

## Reporting back, and the record

**First check whether it worked.** A non-zero exit means the delegation failed
and the text is an error, not an opinion. Exit 124 is the timeout, and an empty
reply with a zero exit is also a failure. Say so and do the work yourself rather
than quoting a failure back as judgement.

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
session: the windows, their age, and any reader fault. The desktop app runs no
status line, so the brief is the only surface there.

At level relief the guard adds a line when the primary crosses its thresholds.
The line is a request, not a dispatch: it asks this session to send the next task
to another account, and the session has to act on it. It arrives **at a task
boundary**, never part-way through a job already running. Say in one line that
you are handing over and which account is taking it, so the user can stop you. If
the user says keep it here, keep it here.

Writing `secondary`, `codex`, `grok`, `cursor`, `both` or `all` to
`~/.second-wind/mode` forces routing whatever the figures say, and
`touch ~/.second-wind/no-failover` turns handover off.

## Routing from the model picker

Type `/model second-wind/personal`, or `/model second-wind/codex/gpt-5.6/high` to
name a model and an effort too. The switch is **refused on purpose**: the session
keeps the model it has and the next tasks go to that worker instead, through the
runner. Picking any normal model stops it. With `--model-picker on` these are
rows in the picker, and `--picker-routes` adds parameterised ones.

The desktop app runs no hook for a typed id, so never type these ids there: ask in chat.
Asked in chat, offer an **AskUserQuestion** picker of worker, then model, then
effort, then write `~/.second-wind/mode` in the shape the hook writes, model and
effort null when not chosen: `{"worker": "codex", "model": "gpt-5.6", "effort":
"high", "set_at": <epoch seconds>, "label": "Codex"}`. Delete it to stop.

`references/setup.md`, `references/config.md` and
`references/troubleshooting.md` carry the rest.
