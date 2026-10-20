---
name: codex-wind
description: Delegate a bounded task from Codex to a named Claude Code, Codex, Grok Build or Cursor Agent subscription, for an independent review or authorised implementation. Also use to set up Codex Wind or check connected accounts.
---

# Codex Wind

Use the bundled runner. It owns `~/.codex-wind/config.json` and private exchanges.
It never changes Second Wind's configuration, hooks, routes or usage readers.
Both skills can share the same official-client sign-ins.

## Resolve and check first

Resolve this installed skill (use `CODEX_WIND_SKILL` when set; otherwise
`$CODEX_HOME/skills/codex-wind`, falling back to `~/.codex/skills/codex-wind`).
Use its absolute path as `$CW` in every command.

```bash
python3 "$CW/scripts/codex_wind.py" status
```

Names come from the user's configuration, never from this document. Claude must
report `READY`, the expected email and `claude.ai` authentication before spending
its allowance. Stop on an identity mismatch. A signed-in desktop app alone is
not a CLI sign-in. For Codex, Grok and Cursor the official client may report no
identity: `not reported by client` means authentication was checked but identity
was not proved. State that limit; do not claim the configured email was verified.
A successful status is not proof that a model request will succeed.

## Setup

See [README.md](README.md). Copy the bundled example to
`~/.codex-wind/config.json` and enter the user's chosen profiles, or explicitly
import an existing Second Wind version 4 account list:

```bash
python3 "$CW/scripts/codex_wind.py" import-second-wind
python3 "$CW/scripts/codex_wind.py" status
```

Import copies labels, profile directories and expected emails only. It backs up
an existing Codex Wind config, keeps matching account aliases and does not grant
new Codex full access. It reads no credentials. Do not change expected emails
to make a mismatch disappear. Complete the official client's sign-in against
the intended account, then check again.

## Delegate

Write a self-contained prompt to a private temporary file. Include objective,
workspace, constraints, evidence required, and whether the worker owns changes
or review only. Announce the selected account and mode.

```bash
python3 "$CW/scripts/codex_wind.py" run ACCOUNT \
  --prompt-file /absolute/path/to/prompt.txt \
  --workspace /absolute/path/to/project --review

python3 "$CW/scripts/codex_wind.py" run ACCOUNT \
  --prompt-file /absolute/path/to/prompt.txt \
  --workspace /absolute/path/to/project --work
```

`--review` gives Claude only Read, Glob and Grep, disables its hooks and loads no
MCP servers; Codex gets its read-only sandbox with all MCP servers returned by its config
listing disabled for that invocation, Grok gets only Read, Glob and Grep,
and Cursor uses plan mode. Review restrictions differ by client; Grok and Cursor
are not an operating-system sandbox. Do not promise a stronger boundary.

`--work` is unattended: Claude and Grok bypass permission prompts, Cursor uses
`--force`, and Codex uses workspace-write with automatic approval review. Only a
Codex account explicitly configured with `full_access: true` bypasses both
approvals and its sandbox. Use work mode only for changes authorised in the
current scope. It does not authorise unrelated systems or external messages.
Sandboxed Codex workers must return browser verification to the calling session.

Optional `--model`, `--effort` and `--timeout` select one run's settings. Cursor
has no separate effort flag. Leave the model unset unless the task calls for a
specific model; the official client then uses its configured default.

Only one writer per checkout. Parallel reviews are acceptable; implementation
in parallel requires separate worktrees. After work, inspect the actual diff and
verify it yourself. A reply is not proof of completion.

The runner removes vendor API keys and endpoint overrides, checks authentication
again, applies a process-group timeout, prints the reply and writes a private
exchange. Codex's final-message file is the answer; startup chatter is not.
Non-zero exit, timeout (124), interruption (130), or an empty response (4) means
failure. Report it as failure, never as the worker's opinion or agreement.

The calling Codex session must itself be allowed to use the network, the chosen
profile and the private exchange folder. A restricted sandbox may require the
harness's normal approval flow. Do not disable its protections automatically;
report the actual blocked operation.
