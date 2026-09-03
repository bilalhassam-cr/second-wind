---
name: second-wind
description: Run work on a second Claude Code account, OpenAI Codex, Grok Build or Cursor Agent, either as an independent reviewer or as a full-access worker. Use when the user wants a second opinion, an adversarial review, an independent read of something already built, or competing options from different models, and when they say they are nearly out of usage, running low on their limit, or want to spend the other account or the other subscription. Also for /second-wind and for setting second-wind up.
---

# Second wind

Routes work to a second Claude subscription, Codex, Grok Build or Cursor Agent.
Two reasons, kept apart. **Independence**, because a reviewer that watched you
build the thing is not independent. **Not waiting**, because each plan has its own
window, so a job can start elsewhere now rather than at the next reset.

## First, resolve $SW

A Bash tool call runs in the user's project, so relative paths and `$0` do not
resolve to this skill. Resolve it once and use `$SW` everywhere:

```bash
SW=$(jq -r '.skill_dir // empty' ~/.second-wind/config.json 2>/dev/null)
SW=${SW/#\~/$HOME}
[ -d "$SW" ] || SW=$HOME/.claude/skills/second-wind
python3 "$SW/scripts/setup.py" --accounts 2>/dev/null || echo NOT_SET_UP
```

`--accounts` prints cached figures and spawns nothing; add `--live` to run the
readers first. It sorts the accounts by headroom and answers where work should go
now. `--check` says whether the chain is wired up and what is blocking it. If
NOT_SET_UP appears, **offer to set it up in one line, then get on with what the
user actually asked for.** A missing optional tool never blocks a task.

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
   Build, Cursor. Any combination is valid. Two warnings belong here, not later.
   Never set `XAI_API_KEY` for Grok Build: `api.x.ai` is a separate paid developer
   product, while Grok Build spends the consumer subscription. And Cursor's
   installer removes `~/.local/bin/agent` and takes that generic name, which can
   delete Grok's alias, so always call the clients `grok` and `cursor-agent`.

4. Walk the chosen workers **one at a time**, giving only the command that is
   missing, waiting for it, then re-running `--detect` before the next one.

   - Second Claude: `mkdir -p ~/.claude-secondary`, then
     `CLAUDE_CONFIG_DIR="$HOME/.claude-secondary" claude auth login --claudeai`.
     Sign-in lands on whichever account the browser already holds and ignores the
     `--email` hint, so open the printed URL in the right browser profile. Never
     set `CLAUDE_CONFIG_DIR` for `~/.claude` itself: it breaks a working sign-in.
   - Codex: `npm i -g @openai/codex`, `codex login`, verify with
     `codex login status`, which writes to stderr.
   - Grok Build: `curl -fsSL https://x.ai/cli/install.sh | bash`. Run
     `grok models` first; success means it is signed in already. Only on failure
     run `grok login`.
   - Cursor: `curl https://cursor.com/install -fsS | bash`, `cursor-agent login`,
     verify with `cursor-agent status --format json`. `CURSOR_API_KEY`
     authenticates delegated work but cannot read usage.

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
   `--refresh-minutes 30`, `--model-picker on`, `--no-launchd`, `--timeout 900`,
   `--force`. It writes the config, creates and pre-trusts
   `~/.second-wind/workdir` for each Claude profile and for Codex, installs the
   hooks and status line after backing the settings up, and schedules the refresh.

8. Tell the user to **restart Claude Code**, then run `--check` and `--accounts`.
   The restart matters twice: settings are read at session start, and a running
   session can write its own copy of the project list back over the workdir trust
   when it exits. `references/setup.md` covers creating a second profile, and the
   optional reader profile that keeps a background reader off the desktop app's
   credential.

## Running work on another account

Always through the runner. It records the call, and an unlogged delegation is the
thing this skill exists to prevent: a job that half worked still returns prose
that reads like success. Write the prompt to a file first, since building the
command inline turns quoting into the hard part of the job.

```bash
"$SW/scripts/run.sh" <secondary|codex|grok|cursor> <prompt-file> \
    [--review|--work] [--model M] [--effort E]     # cursor takes no --effort
```

**`--review` blocks file edits and shell work.** Claude loses its edit, write and
shell tools and loads no MCP servers. Codex runs in a read-only sandbox. Grok
drops Write, Edit and Bash. Cursor adds `--mode ask`; `--trust` alone is not
read-only and wrote a file in live testing. Use review mode to have work
challenged, and whenever you are still editing the same files.

**`--work` is full access** in the current directory, the same as you. Use it to
build or fix something, and when the point is to spend the other allowance. The
config sets the default mode. Say which mode you used when reporting back.

**Writing the prompt.** The worker starts blind. Put the question, the relevant
file contents, the constraint that matters and what finished looks like into the
prompt. Never point it at skill definitions written for another system. For a
review:

> You are reviewing work you did not produce. Be direct and specific. Lead with
> the single biggest problem. No compliments, no summary of what the work does.
> If something is wrong, say what is wrong, where, and what it should be instead.

**In parallel.** Each call writes its own exchange file and appends one ledger
line, so parallel calls are safe: background each one and `wait`. Subagents cannot
be moved this way, since they inherit this session's login and always bill the
primary account. The runner is what shifts the load.

## What a worker cannot do

**A Codex worker cannot launch a browser.** It is always sandboxed, because the
runner passes its own sandbox flag in both modes and a command-line sandbox
overrides the user's Codex config. Chrome aborts at startup inside it and the user
gets a "quit unexpectedly" dialog explaining nothing, so the runner tells the
worker not to try. Keep browser QA, screenshot checks and any CDP, puppeteer or
playwright step on the primary session. This bites hardest in projects whose own
instructions say to verify rendered output in a real browser.

## Reporting back, and the record

**First check whether it worked.** A non-zero exit means the delegation failed and
the text is an error, not an opinion. Exit 124 means it was killed on the timeout,
and an empty reply with a zero exit is also a failure. Say so and do the work
yourself rather than quoting a failure back as judgement.

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

At level relief the guard adds a line when the primary crosses its thresholds. It
fires **at a task boundary**, never part-way through a job already running. It
**says so in one line** before doing it, so the user knows which account did the
work. If the user says keep it here, keep it here.

Writing `secondary`, `codex`, `grok`, `cursor`, `both` or `all` to
`~/.second-wind/mode` forces routing whatever the figures say, and
`touch ~/.second-wind/no-failover` turns handover off. `references/setup.md`,
`references/config.md` and `references/troubleshooting.md` carry the rest.
