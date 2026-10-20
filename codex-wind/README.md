# Codex Wind

Run another subscription's official client from Codex. Supports named Claude
profiles, isolated Codex homes, Grok Build and Cursor Agent. Requires Python 3.9+
and whichever official clients you choose. No API key is required.

From the repository root, run `./install.sh --codex`, or `./install.sh --both`
to install the Claude Code integration too. Restart Codex to load the skill.
Installation copies files by default; `--link` is for editing this checkout.

## Configure accounts

If Second Wind is already configured:

```bash
CW="${CODEX_HOME:-$HOME/.codex}/skills/codex-wind"
python3 "$CW/scripts/codex_wind.py" import-second-wind
python3 "$CW/scripts/codex_wind.py" status
```

Import is an explicit snapshot. Repeat it after adding accounts in Second Wind.
It reads only that tool's config, never credentials, and writes only Codex Wind's
config. Existing aliases for the same profiles survive. Import keeps existing
Codex full-access choices but never enables that setting for a new account.

Without Second Wind:

```bash
mkdir -p ~/.codex-wind
cp "$CW/config.example.json" ~/.codex-wind/config.json
chmod 600 ~/.codex-wind/config.json
```

Edit the JSON: keep only accounts you hold, choose labels, enter the actual
profile directories and expected emails. Accounts without `kind` are treated as
Claude for compatibility with the original Codex Wind config. `CODEX_WIND_HOME`
can select a different config and exchange directory.

Sign in through each official client, one at a time:

```bash
claude auth login                              # default Claude profile
CLAUDE_CONFIG_DIR=~/.claude-personal claude auth login
CODEX_HOME=~/.codex-work codex login
grok login
cursor-agent login
```

Check the browser's identity before authorising: Claude's `--email` is a hint,
not an account selection. Never set `CLAUDE_CONFIG_DIR=~/.claude` for the default
profile; leave the variable unset. A desktop app sign-in may not sign in its CLI.
Codex homes must already exist. Do not read or copy OAuth tokens yourself.

`status` uses the clients' own authentication commands and sends no model prompt.
Claude's email is mandatory and checked. Cursor's email is checked when reported.
Codex and Grok can omit identity; the output uses `AUTH` and says
`not reported by client`.
That is authenticated access, not proof of account identity, plan or headroom.
No usage readers, automatic handover hooks or UI panel are installed in Codex.
Second Wind supplies those features in Claude Code.

## Run a task

Ask Codex to use Codex Wind with an account name, task and review/work mode, or:

```bash
python3 "$CW/scripts/codex_wind.py" run claude-personal \
  --prompt-file /absolute/path/to/prompt.txt \
  --workspace /absolute/path/to/project --review
```

Use `--work` for authorised edits. See `SKILL.md` for each client's access mode.
There is no automatic account rotation. Each call has one named destination.
The runner clears vendor API keys, so Cursor must have an interactive sign-in.

Exchanges contain full prompts and responses. They stay locally under
`~/.codex-wind/exchanges` (directory 700, files 600) until you remove them. Do not
publish these logs. Timeout is 7200 seconds unless configured or overridden by
`--timeout`; a timeout, empty response or non-zero exit is a failed delegation.

## Verify and remove

```bash
python3 -m unittest discover -s codex-wind/tests
```

These tests use stub clients and temporary installs; they do not prove vendor
availability. A live review and a bounded file-write in a disposable directory
are separate checks. A clean machine has not yet been tested.

To remove Codex Wind, remove only `~/.codex/skills/codex-wind` (or the skills
folder under your custom `CODEX_HOME`). Your config and exchanges remain in
`~/.codex-wind`; remove those separately only when you no longer need them.
Second Wind's installation and sign-ins are unaffected.

The calling Codex session needs network and profile access plus permission to
write its exchange folder. In a restricted sandbox, use the harness's normal
approval flow for the blocked operation. Live results in this repository were
collected from a Codex desktop session with full local access.

Cursor wrote a file during the 20 October 2026 live test in a live `--mode ask` boundary test. Both
runners use `--mode plan` for review after the same test was blocked there.
