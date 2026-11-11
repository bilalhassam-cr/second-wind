# Second Wind

**Your other AI subscriptions, within reach.**

Get an independent review, delegate a task, or hand work to another account
directly from Claude Code or Codex. Second Wind runs Claude Code, Codex, Grok
Build, Cursor Agent and Google's Antigravity CLI for Gemini through their
official clients, with one named destination per call and a record of what
happened. It can also hand tasks to persistent Grok Bot agents (experimental).

**Works in Claude Code and Codex.** Both harnesses load the same `second-wind`
skill, use the same runner and account configuration, and write to one delegation
history. Claude Code also has a usage panel, session hooks and announced handover
at task boundaries.

## Install

```bash
git clone https://github.com/bilalhassam-cr/second-wind second-wind
cd second-wind
./install.sh
```

The installer detects local harnesses and offers Claude Code, Codex or both in
an interactive terminal. For scripts, or to choose directly:

| Host | Command | Installed skill |
|---|---|---|
| Claude Code | `./install.sh --claude` | `~/.claude/skills/second-wind` |
| Codex CLI or desktop | `./install.sh --codex` | `${CODEX_HOME:-~/.codex}/skills/second-wind` |
| Both | `./install.sh --both` | The same skill in both locations |

Without an interactive terminal, a single detected harness is selected; when
both or neither are detected, supply an explicit flag. `./install.sh DIR` keeps
the legacy custom Claude skills-directory form. Use `--claude-dir DIR` and
`--codex-dir DIR` for explicit destinations. `--link` links this checkout for
development; moving it then breaks the installation. Copies are the default.

The installer says which clients it found, installs the skill and prints the
next step. Run it again at any time to update an existing install; `--dry-run`
shows what it would do. Restart your harness, then ask it to **set up Second
Wind**. Installing does not sign in any account or enable full access. An old Codex Wind installation is
archived outside the skills folder so it cannot appear as a duplicate skill.

## What it looks like

In Claude Code every session opens with the usage of each account, drawn to
scale, and one line for anything that needs you:

```
                 5-hour             weekly
Work Claude      ███░░░░░░░░░  25%  ███▎░░░░░░░░  27%
                 resets 03:00 Nov 16
Personal Claude  ▊░░░░░░░░░░░   6%  ███░░░░░░░░░  25%
Codex            ············  n/a  ██▍░░░░░░░░░  20%
Cursor           login expired, sign in again
Readings 1m to 12m old.
Needs you: Cursor signed out. Fix: Run `cursor-agent` in a terminal and sign in.
```

A figure older than an hour is never drawn. From there, "have Codex review
this" sends the task to the account you name and keeps a record of what came
back.

## One setup, either harness

Both hosts read `~/.second-wind/config.json`. If Second Wind is already set up,
use it directly in either host. Check the official-client sign-ins:

```bash
SW=~/.claude/skills/second-wind       # Claude Code installation
# SW="${CODEX_HOME:-$HOME/.codex}/skills/second-wind"   # Codex installation
python3 "$SW/scripts/second_wind.py" status
```

For delegation without the Claude extras, copy
`second-wind/config.example.json` to `~/.second-wind/config.json`, make it private
with `chmod 600`, and edit the account names, chosen profiles and expected emails.
Keep only subscriptions you hold. Sign in through the official clients, then run
`status`. The [setup guide](second-wind/references/setup.md) explains profile
isolation, sign-in and optional Claude integration.

Existing Second Wind role names (`primary`, `secondary`, `codex`, `codex2`,
`codex3`, `grok`, `cursor`) work in both hosts. Additional named accounts live in
the same file. Aliases retain old account names without creating another sign-in.
`SW_HOME` can select a separate state directory for a deliberate isolated setup.

## Use it

Ask naturally: “Have the personal account review this.” “Get an independent
second opinion.” “Have Codex implement this task.” Name the account and whether
it should review or do the work.

```bash
python3 "$SW/scripts/second_wind.py" run ACCOUNT \
  --prompt-file /absolute/path/to/prompt.txt \
  --workspace /absolute/path/to/project --review
```

`ACCOUNT` is a name from your config: `secondary` after the guided setup,
`claude-personal` in the example file. `status` lists them.

Use `--work` for authorised implementation. Optional `--model`, `--effort` and
`--timeout` apply to that call. Cursor accepts a parameterised model rather than
a separate effort flag. The existing `run.sh ACCOUNT PROMPT-FILE ...` interface
calls the same Python runner. Neither host silently rotates accounts.

Review restricts Claude to read/search tools with hooks and MCP disabled; Codex
uses its read-only sandbox with resolved MCP servers disabled; Grok gets a read
allow list; Cursor uses plan mode; Antigravity uses plan mode with its terminal
sandbox. Grok, Cursor and Antigravity tool modes are not operating-system
sandboxes. Cursor ask mode wrote a file in a live boundary test, so it is
not used for review. Work mode is unattended and must stay within the task you
have authorised. Codex work is sandboxed unless that account explicitly enables
`full_access`; keep browser verification on the calling session when sandboxed.

Authentication checks verify Claude's expected email and subscription sign-in.
Other clients may omit identity, which is shown as AUTH rather than verified
identity. A login check is not proof of model availability or plan headroom.
API keys and provider overrides are removed from workers, except a Cursor key
explicitly configured as that client's own sign-in.

## Health and updates

```bash
python3 "$SW/scripts/health.py"            # what needs you, and the fix for each
python3 "$SW/scripts/health.py" --tidy     # also clear its own surplus backups
python3 "$SW/scripts/update.py" --check    # is there a newer version
python3 "$SW/scripts/update.py"            # show what is new, ask, then update
```

`health.py` reads what the usage readers and the timer left behind and turns it
into one line per fault: which account to sign in to again and the command for
it, which client to update, when the fault is in Second Wind itself. It asks no
vendor anything and reads no credential. It exits 1 when something needs you.

`update.py` uses git and nothing else: a fetch, then a fast-forward of the
checkout the skill was installed from. A checkout with local changes is left
alone. A copied install is reinstalled from it and the copy it replaces is kept
once under `~/.second-wind/backups`. In Claude Code a background check runs at
most once a day: a `git fetch` in that checkout, which records whether an update
exists and installs nothing; set
`"update": {"check": false}` in the config to stop it. Changes are listed in
[CHANGELOG.md](CHANGELOG.md).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The worker replied. Verify the work yourself. |
| 2 | Nothing was sent: bad arguments, config or sign-in. |
| 3 | `status` found an account that is not ready. |
| 4 | The worker ran and returned nothing. |
| 75 | The account is out of allowance. Its last reading said so and nothing was sent, or the vendor refused the run. `--ignore-usage` sends anyway. |
| 124 | Timed out. |
| 130 | Interrupted. |

Any other code is the client's own.

## Shared history

Every call from either harness writes a private exchange and a ledger entry to
`~/.second-wind/log` (or the configured `log.dir`). Logs include prompts and replies;
keep them private. Failures, empty replies, timeouts and interrupted runs are
recorded as failures. The caller must verify the actual work.

```bash
python3 "$SW/scripts/report.py" 7
python3 "$SW/scripts/report.py" 7 --share
```

The share report removes account emails and home paths and omits prompt/reply
content by default. Read it before sending. Logged exchanges are capped at the
configured size and pruned after the configured retention period; ledger rows
remain. Previously retained logs stay available after migration.

## Claude Code integration

Claude Code can also show usage across configured roles, route from a picker,
and request an announced handover at a task boundary. The reviewer, worker and
relief levels control those optional hooks. Codex uses the shared delegation
commands; it does not get Claude's UI or hooks.

See the [Claude Code guide](second-wind/references/claude-code.md),
[setup](second-wind/references/setup.md),
[routing](second-wind/references/routing.md) and
[troubleshooting](second-wind/references/troubleshooting.md).

## Gemini through the Antigravity CLI

Google's Antigravity CLI, `agy`, is a destination for Gemini models on your own
Google AI plan. It installs on its own or comes bundled in the Gemini macOS app,
and Second Wind finds either. Which models an account has depends on its plan,
so pass an exact ID from `agy models`. See
[setup](second-wind/references/setup.md#antigravity-cli).

Review runs agy in plan mode with its terminal sandbox, and work skips its
permission prompts. Neither is an operating-system sandbox. A second Google
account gets its own folder (`home`), and Second Wind keeps that account's
sign-in in agy's own file rather than the keychain, where agy stores every
account under the same name.

Limits come from agy's own `/usage`, which costs no tokens. Each account has a
usage-panel row with its Gemini 5-hour and weekly windows, and any other pool it
has used, such as a separate Argon week, in the note beneath.

Verified live on macOS on 27 and 28 October 2026 with two Google accounts side
by side: status, a review on each, a work-mode write, the usage panel and both
of its refreshes, and an hourly sign-in renewal with no keychain prompt.

## Grok Bot (experimental)

Grok Bot is an **experimental** destination for existing persistent agents in
xAI's desktop app. It uses an undocumented API whose official client Cursor
withdrew on 23 October 2026. Routes may disappear without notice. This is separate
from Grok Build and uses a Cursor user API key, stored outside configuration.
See [setup](second-wind/references/setup.md#grok-bot-experimental).

Bots act as the user through their connected apps. Delegate only what the user
asked for, and show the user any message the calling agent composed before
sending it. Review restrictions are instructions and cannot be enforced remotely.
The bot receives message context, not access to the caller's files.

The local fake-server tests cover named delegation, busy refusal, reply collection,
private cursors, timeout recovery, identity checks and key redaction. Grok Bot was
verified live on macOS on 26 October 2026 (setup, status, read, delegation, the
unknown-name refusal, and no key in logs). There is no usage panel or picker integration.

## Grok Bot can start local sessions

An opt-in [return bridge](second-wind/references/bot-bridge.md) lets an existing
Grok Bot request a named Claude or Codex CLI session, send follow-ups to its native
session UUID and receive results. It runs locally with no inbound listening port.
Enable the source bot, destination accounts, workspace roots and review/work modes
explicitly. The macOS worker runs through launchd; other systems can supervise the
foreground command. These are native CLI conversations, not desktop chat tabs.

The automated bridge tests exercise duplicate requests, native resume arguments,
workspace validation, identity failures, cancellation, worker failures, crash
recovery and reply delivery retries against synthetic clients and a local HTTP
server. On macOS, live testing on 26 October 2026 verified Grok Bot initiating
a Codex session and a Claude session, each writing a bounded test file, resuming
the same native UUID on a follow-up and returning the remembered value to the
bot. The test also exercised the launchd worker with the configured user API key.

## Moving from Codex Wind

```bash
python3 "$SW/scripts/second_wind.py" migrate-codex-wind
python3 "$SW/scripts/second_wind.py" status
```

Migration reads the former `~/.codex-wind/config.json`, merges account metadata
and aliases, and backs up the shared config before writing it. It copies old
exchanges into the shared log and indexes them once; repeated migration does not
duplicate the history. Original files remain in place. Conflicting account
identities are refused. No tokens, hooks or sign-ins are copied or changed.

Codex Wind is now a compatibility name, not a second product or skill.

## Requirements and verification

Python 3.9+ and the official clients you choose. Claude's optional UI/usage
integration also needs `jq`. Live checks were performed on macOS with Claude
Code 2.1.304, Codex 0.163.0, Grok 1.0.62 and Cursor 2026.10.19. Automated tests run
on Linux; clean-machine setup and live Linux clients remain untested.

[TESTING.md](TESTING.md) covers the shared runner, installer, migration, usage
readers, review boundaries and remaining limits. A restricted calling harness
may need its normal approval flow for network, profile or log-directory access.

## Account use

Second Wind uses official clients and separate sign-ins, with no proxy or token
pool. Every delegation names its destination. Check your own plan's terms:
[Anthropic consumer terms](https://www.anthropic.com/legal/consumer-terms),
[Anthropic usage policy](https://www.anthropic.com/legal/aup) and
[OpenAI terms](https://openai.com/policies/terms-of-use). This project makes no
claim about how a provider will interpret or enforce them.

## Uninstall

```bash
./install.sh --uninstall
```

It lists what it will remove, asks, then removes the skill folders, Second
Wind's hooks and status line (restoring the one it replaced), its background
refresh and the Grok Bot bridge with its stored key. It works even when the
skill folder has already been deleted. The shared configuration, history,
sign-ins and archived legacy files remain until you delete `~/.second-wind`.

MIT. See [LICENSE](LICENSE).
