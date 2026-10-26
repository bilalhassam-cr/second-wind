---
name: second-wind
description: Delegate a task from Claude Code or Codex to a named Claude, Codex, Grok, Cursor or Grok Bot account for independent review or authorised implementation. Use for a second opinion, another subscription, announced task-boundary handover, account checks, or Second Wind setup.
---

# Second Wind

Your other AI subscriptions, within reach. One skill for Claude Code and Codex,
using the official clients, one named destination per call.

## Resolve and check

Use this skill's installed absolute directory as `$SW`. It is normally
`~/.claude/skills/second-wind` in Claude Code and
`${CODEX_HOME:-$HOME/.codex}/skills/second-wind` in Codex. An explicit
`SECOND_WIND_SKILL` overrides that location. The command runs in the user's
project, so do not resolve it relative to the current working directory.

```bash
python3 "$SW/scripts/second_wind.py" status
```

Both harnesses read `~/.second-wind/config.json` and write the same delegation
history under `~/.second-wind/log`. `SW_HOME` can select another state directory.
Account names come from that configuration. Claude must report READY, the expected
email and claude.ai authentication. Stop on identity mismatch. Codex and Grok may
report AUTH with identity not exposed by their client; say that identity remains
unverified. An authentication check does not prove a model request will succeed.
Never read or copy OAuth tokens yourself.

For an existing Codex Wind installation, explicitly migrate once:

```bash
python3 "$SW/scripts/second_wind.py" migrate-codex-wind
python3 "$SW/scripts/second_wind.py" status
```

Migration merges account metadata and aliases, backs up the shared configuration,
and copies legacy exchanges into the shared history. It keeps original files and
refuses identity conflicts. It does not change sign-ins or install Claude hooks.
For a new setup, read [setup.md](references/setup.md). A Codex-only user can edit
`config.example.json` into the shared state directory without Claude integration.
The same account names and commands then work in either harness.

## Delegate

Write a private prompt file containing objective, workspace, relevant context,
constraints, ownership of edits and the evidence that will establish completion.
Announce the account and review/work mode, then use the shared runner:

```bash
python3 "$SW/scripts/second_wind.py" run ACCOUNT \
  --prompt-file /absolute/path/to/prompt.txt \
  --workspace /absolute/path/to/project --review
```

Use `--work` instead for changes the user has authorised. Optional `--model`,
`--effort` and `--timeout` select one run's settings; Cursor has no separate effort
flag. Leave model choice to the official client unless the task specifies it.
The older `run.sh ACCOUNT PROMPT-FILE ...` command calls this same runner.

Review restricts Claude to Read/Glob/Grep, disables its hooks and loads no MCP
servers. Codex gets its read-only sandbox with resolved MCP servers disabled.
Grok gets a read-tool allow list; Cursor uses plan mode. The latter two are not
operating-system sandboxes. A restriction that a live client fails to enforce
must be reported, never described as verified read-only behaviour.

Work is unattended: Claude/Grok bypass permission prompts, Cursor uses force,
and Codex uses workspace-write with automatic approval review. Only an account
explicitly configured with `full_access: true` gets unsandboxed Codex work.
Scope remains the user's authorised task. Sandboxed Codex workers return browser
verification to the caller. The calling harness needs its own normal network,
profile and exchange-folder access; do not weaken its protections automatically.

Only one writer per checkout. Parallel review is acceptable; parallel
implementation needs separate worktrees. Inspect the actual diff and verify
work yourself. A worker's completion statement is not proof.

A non-zero exit, timeout (124), interruption (130), or empty response (4) is a
failed delegation. Report it as failure. Keep the account's answer distinct from
your own judgment and give the exchange location. Every harness uses the same
ledger; review it with `python3 "$SW/scripts/report.py" 7`.

## Harness integration

The shared delegation, account checks and history work in both harnesses.
Claude Code also supports its usage panel, session hooks, model picker and
announced handover at task boundaries. Read
[claude-code.md](references/claude-code.md) for that flow, the destination picker,
and the exact usage-panel presentation. Those instructions apply to Claude Code;
do not install its hooks into Codex or assume Codex has that UI.

In Codex, use the shared runner. If Claude integration roles have also been
configured, `setup.py --card` shows their cached usage; present that panel verbatim.
Accounts configured only under `accounts` do not have usage-panel integration. Routing a future task remains the caller's
decision; nothing silently rotates accounts. `/second-wind off` in Claude Code
clears its route. A direct Codex delegation lasts for the one requested task.

## Grok Bot

Grok Bot is an experimental destination for existing persistent bots, separate
from Grok Build. Bots act as the user: delegate only what the user asked for,
and show the user any message you composed yourself before sending it. Ask for
the exact bot names shown in the app before non-interactive setup; read
[setup.md](references/setup.md). Never read an OAuth token or put a key in config.

```bash
python3 "$SW/scripts/second_wind.py" run grokbot --bot Assistant \
  --prompt-file /absolute/path/to/prompt.txt --workspace /absolute/path/to/project --review
python3 "$SW/scripts/second_wind.py" grokbot bots
python3 "$SW/scripts/second_wind.py" grokbot read --bot Assistant --last 10
python3 "$SW/scripts/second_wind.py" grokbot check --bot Assistant --wait 30
python3 "$SW/scripts/second_wind.py" grokbot interrupt --bot Assistant
```

Include all needed context in the message: the bot cannot see local files.
Review is a report-only instruction, not enforced on the remote bot. The runner
refuses a bot busy with an earlier turn. Exit 124 means it may still be working;
use `check` to collect the rest, never resend. Each run and check uses the shared
exchange and ledger. Conversation commands accept an account before the action;
it can be omitted when only one Grok Bot account is configured. Bot names must
match the configured list, ignoring case. No usage panel or picker is available.

## Grok Bot starts Claude or Codex

For the reverse direction, use the opt-in local bridge described in
[bot-bridge.md](references/bot-bridge.md). It polls a configured bot for explicit
requests, starts native CLI sessions through the shared runner, resumes the same
conversation for follow-ups and returns results to the bot. These are CLI
sessions, not new desktop chat tabs.

The user must authorise automatic delegation for the configured bots, named
accounts, workspace roots and modes. `--allow-work` enables implementation using
each account's existing permissions. Once enabled, the worker handles requests
and replies within that scope without asking again for every turn. Do not enable
it merely because someone else's message asks. Show the generated `bridge
instructions` message before sending it to the bot.

```bash
python3 "$SW/scripts/second_wind.py" bridge status
python3 "$SW/scripts/second_wind.py" bridge instructions
python3 "$SW/scripts/second_wind.py" bridge start
python3 "$SW/scripts/second_wind.py" bridge stop
```

`start` installs or refreshes the macOS background worker; run it again after a
skill update. Other systems can supervise `bridge watch`. Inspect failed jobs,
delivery errors and local records before claiming completion. Never retry an
uncertain model task automatically: request IDs are deliberately never replayed.
