# Setting up the accounts

## Creating a second Claude profile

A Claude Code profile is a config directory. `CLAUDE_CONFIG_DIR` is documented and
read per process, and on macOS credentials are namespaced per directory in the
Keychain, so two accounts coexist without fighting.

```bash
mkdir -p ~/.claude-secondary
CLAUDE_CONFIG_DIR=$HOME/.claude-secondary claude auth login --claudeai
```

The sign-in opens a browser and prints a code to paste back.

**The trap that costs people an hour.** The sign-in completes against whichever
account the browser is already signed into, and **ignores the `--email` hint**. If
the browser holds account A, you get account A no matter what you asked for. It
can also complete in the background before a status check catches up, so a quick
"did it work" can report the old answer.

Two habits fix it:

1. Open the consent URL yourself, in the browser profile that holds the account
   you want. Stop the CLI opening its own with `BROWSER=/usr/bin/true`:

   ```bash
   BROWSER=/usr/bin/true CLAUDE_CONFIG_DIR=$HOME/.claude-secondary \
     claude auth login --claudeai
   # then open the printed URL in the right browser profile, e.g. on macOS:
   # open -na "Google Chrome" --args --profile-directory="Profile 1" "<url>"
   ```

2. Check afterwards, and back up first:

   ```bash
   cp ~/.claude.json ~/.claude.json.backup
   CLAUDE_CONFIG_DIR=$HOME/.claude-secondary claude auth status --json
   ```

If the wrong account lands in a profile, `claude auth logout` in that profile
clears the credential, and `oauthAccount` can be restored from the backup. Restore
that key surgically rather than the whole file, since a running app writes to it.

## Never set CLAUDE_CONFIG_DIR to the default directory

Leaving it unset and setting it to `~/.claude` are **not** the same thing. Unset
uses the un-suffixed Keychain entry; setting it explicitly makes the CLI look for a
hashed entry that does not exist, and an account that is signed in reports
`loggedIn: false`. Verified on macOS 26.5.

`discover.py` and `run.sh` both account for this. Any code you add should too.

## Two traps to avoid entirely

- Never set `ANTHROPIC_API_KEY` in the environment. It overrides subscription auth
  and silently bills the API instead.
- Never set `CLAUDE_CODE_OAUTH_TOKEN`. It has been reported to delete the Keychain
  credential on exit.

Do not symlink one config directory to another. Separate directories are the point.

## Codex

If `codex` is on the PATH and signed in to a ChatGPT plan, setup finds it. Install
with `npm i -g @openai/codex`; if you already use Codex in the ChatGPT desktop app,
the CLI reads that same sign-in and needs no separate login.

`codex doctor` is the fast health check.

Sign in with `codex login` and verify with `codex login status`. Codex needs a
ChatGPT plan and spends that plan's allowance.

## Grok Build

Grok Build is included with SuperGrok and X Premium+. Install and sign in:

```bash
curl -fsSL https://x.ai/cli/install.sh | bash
grok login
```

It installs at `~/.grok/bin/grok`. Verify the sign-in with `grok models`, which
sends no model prompt.

**Never set `XAI_API_KEY`.** The `api.x.ai` developer API is a separate paid
product. Grok Build must use the consumer allowance. Work uses
`grok --permission-mode bypassPermissions -p "<prompt>"`; review uses
`grok --disallowed-tools "Write,Edit,Bash" -p "<prompt>"`. The prompt must follow
`-p` immediately.

Grok `/usage` has one weekly window and no 5-hour window.

## Cursor Agent

Cursor Agent uses Cursor Pro. Install and sign in:

```bash
curl https://cursor.com/install -fsS | bash
cursor-agent login
```

Verify with `cursor-agent status --format json`. Work uses
`cursor-agent -p --trust`. Review must add `--mode ask`. `--trust` by itself is
not read-only and wrote a file during live testing.

Headless runs can authenticate with `CURSOR_API_KEY`, but `/usage` requires the
interactive browser login. Its monthly view reports Included, Auto and API pools
plus a reset date. On-demand may appear unavailable even when the account holds
credit.

## The `agent` command collision

Cursor's installer runs `rm -f ~/.local/bin/agent` and takes that generic name.
That can delete Grok's `agent` alias, but Grok's `grok` command survives. Always
use `grok` and `cursor-agent`, never `agent`.

## Terms of use

Covered once, in the README. Read it before installing.
