# Second Wind

**Your other AI subscriptions, within reach.**

Get an independent review, delegate a task, or hand work to another account
directly from Claude Code or Codex. Second Wind runs Claude Code, Codex, Grok
Build and Cursor Agent through their official clients, with one named destination
per call and a record of what happened.

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

Restart your harness, then ask it to **set up Second Wind**. Installing does not
sign in any account or enable full access. An old Codex Wind installation is
archived outside the skills folder so it cannot appear as a duplicate skill.

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

### Moving from Codex Wind

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

## Use it

Ask naturally: “Have the personal account review this.” “Get an independent
second opinion.” “Have Codex implement this task.” Name the account and whether
it should review or do the work.

```bash
python3 "$SW/scripts/second_wind.py" run secondary \
  --prompt-file /absolute/path/to/prompt.txt \
  --workspace /absolute/path/to/project --review
```

Use `--work` for authorised implementation. Optional `--model`, `--effort` and
`--timeout` apply to that call. Cursor accepts a parameterised model rather than
a separate effort flag. The existing `run.sh ACCOUNT PROMPT-FILE ...` interface
calls the same Python runner. Neither host silently rotates accounts.

Review restricts Claude to read/search tools with hooks and MCP disabled; Codex
uses its read-only sandbox with resolved MCP servers disabled; Grok gets a read
allow list; Cursor uses plan mode. Grok and Cursor tool modes are not operating-
system sandboxes. Cursor ask mode wrote a file in a live boundary test, so it is
not used for review. Work mode is unattended and must stay within the task you
have authorised. Codex work is sandboxed unless that account explicitly enables
`full_access`; keep browser verification on the calling session when sandboxed.

Authentication checks verify Claude's expected email and subscription sign-in.
Other clients may omit identity, which is shown as AUTH rather than verified
identity. A login check is not proof of model availability or plan headroom.
API keys and provider overrides are removed from workers, except a Cursor key
explicitly configured as that client's own sign-in.

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

Remove `second-wind` from the selected harness's skills folder. If Claude hooks
were enabled, first run `python3 "$SW/scripts/setup.py" --uninstall` to remove
its hooks, restore the previous status line and unload its refresh agent.
The shared configuration, history, sign-ins and archived legacy files remain
until you choose to remove them.

MIT. See [LICENSE](LICENSE).

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
private cursors, timeout recovery, identity checks and key redaction. No live
Grok Bot verification is claimed here. There is no usage panel or picker integration.
