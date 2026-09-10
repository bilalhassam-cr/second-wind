# Setting up the accounts

Config version 4. `references/config.md` describes every key; this file covers the
accounts themselves and the traps that cost time.

## The wizard, step by step

Run it only when `setup.py --accounts` prints `NOT SET UP`. One question at a
time, one command at a time.

1. **Detect before asking anything**: `python3 "$SW/scripts/setup.py" --detect`.
   The JSON names the Claude profiles and their sign-in state, whether `codex`,
   `grok` and `cursor-agent` are on PATH, the client versions and any config.

2. **State the cost before the choice.** A second Claude worker needs a second
   Claude subscription, Codex needs a ChatGPT plan, Grok Build spends SuperGrok
   or X Premium+, Cursor Agent spends Cursor Pro. Nothing here is free.

3. **Ask with AskUserQuestion, multi-select**: second Claude account, Codex, Grok
   Build, Cursor. Any combination is valid. **If Codex is chosen, ask a second
   multi-select**, because one ChatGPT login can carry more than one allowance:
   a work or team workspace, the personal space on that same login, and a
   separate personal account. Each one chosen becomes a Codex role of its own
   with its own directory; the Codex section below carries the whole flow,
   including the browser side. Two warnings belong in the
   conversation: never set `XAI_API_KEY` for Grok Build, since `api.x.ai` is a
   separate paid developer product, and always call the clients `grok` and
   `cursor-agent`, since Cursor's installer takes the generic name `agent` and
   can delete Grok's alias.

4. **Walk the chosen workers one at a time**, giving only the command that is
   missing, waiting for it, then re-running `--detect` before the next one. A
   Claude sign-in lands on whichever account the browser already holds and
   ignores the `--email` hint, so say which browser profile to open the printed
   URL in. Never set `CLAUDE_CONFIG_DIR` for `~/.claude` itself: it breaks a
   working sign-in.
   Codex sign-ins go one at a time and each needs the browser to hold the right
   account when the consent page loads; follow the Codex section for those.

5. **Ask which Claude profile is primary** if more than one is signed in. Primary
   is where the user works day to day, keeps their history and orchestrates from.
   `~/.claude` is usually it, because the desktop app and every plain `claude`
   command already use that profile.

6. **Ask for the level**, single select. Reviewer: read-only delegation, no
   automatic handover, session brief only. Worker: full-access delegation, plus
   the hooks that notice a limit. Relief: full access, and handover at a task
   boundary when the primary crosses its thresholds.

7. **Write the config with one command**, workers `on` or `off` as chosen, each
   Codex role with its directory and the name the user gave it, then
   tell the user to restart Claude Code and run `--check` and `--accounts`. The
   restart matters twice: settings are read at session start, and a running
   session can write the project list back over the workdir trust when it exits.

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

## The optional reader profile

`--reader ~/.claude-usage` is a third Claude profile, signed into the **same
account as your primary**, used only to read the primary's usage. Create it the
same way as the secondary and sign it into the primary account.

It exists for one measured reason: a background reader that shares one credential
with the desktop app logged the app out every day or two. A separate profile ends
that. Without it, the primary reader runs with `CLAUDE_CONFIG_DIR` unset, which is
correct and slightly more fragile.

## Never set CLAUDE_CONFIG_DIR to the default directory

Leaving it unset and setting it to `~/.claude` are **not** the same thing. Unset
uses the un-suffixed Keychain entry; setting it explicitly makes the CLI look for a
hashed entry that does not exist, and an account that is signed in reports
`loggedIn: false`. Verified on macOS 26.

`discover.py` and `run.sh` both account for this. Any code you add should too.

## Two traps to avoid entirely

- Never set `ANTHROPIC_API_KEY` in the environment. It overrides subscription auth
  and silently bills the API instead.
- Never set `CLAUDE_CODE_OAUTH_TOKEN`. It has been reported to delete the Keychain
  credential on exit.

Do not symlink one config directory to another. Separate directories are the point.

## The workdir, and why it is pre-trusted

`setup.py --write` creates `~/.second-wind/workdir`, an empty directory, and marks
it trusted in each Claude profile's `.claude.json` and in `~/.codex/config.toml`.
Every usage reader runs there.

This is what keeps the readers moving. A client launched in an unknown directory
opens a trust dialog, and no reader ever answers one: a reader that meets a dialog
stops and writes `TRUST PROMPT: ...` to its status file. Pre-trusting is a setting
you asked for at setup, on a directory second-wind created and owns.

**Restart Claude Code after setup.** A session that was already running holds its
own copy of the project list and can write it back over the new entry when it
exits. `setup.py --check` reads the trust back from both files rather than
assuming it survived; if it says `NOT TRUSTED`, restart the client and run
`--write` again, or open the client once in the workdir yourself.

## The scheduled refresh

On macOS, setup installs a launchd agent, `com.second-wind.refresh`, that runs
`~/.second-wind/runtime/usage-refresh.sh --if-claude-running` every
`refresh.interval_minutes`, 15 by default. It runs that mirrored copy rather than
the one in the skill folder because a LaunchAgent cannot read `~/Documents`; see
`troubleshooting.md`. `--if-claude-running` exits immediately unless a `claude` process
or the Claude desktop app is running, so nothing starts a client on an idle
machine. `--no-launchd` at setup skips it; run `usage-refresh.sh` from cron or by
hand instead. `setup.py --check` reports whether the agent is loaded.

## The model picker experiment

`--model-picker on` is opt-in. Each refresh then writes a `modelPicker` key into
the primary profile's `settings.json` with the current figures in the description
of each model row. It needs `replaceBuiltInOptions: true`, so the rows shown are
the four aliases second-wind writes and not the built-in list. The key carries a
`_second_wind` marker, and setup and uninstall only ever remove a key carrying it.

It is an experiment because of what it costs: a background job rewriting a file
Claude Code owns. The refresh reads the key first and writes only when a figure
has actually moved, so an idle machine sees no writes at all, but if Claude Code
saves `settings.json` at the same moment as a refresh, one of the two writes is
lost. Turn it on if you want to try it, and leave it off if that trade is not
one you want on your settings file.

To turn it off, run `--write` again with `--model-picker off`. That records the
setting, which stops the refresh rewriting the rows, and takes out a `modelPicker`
key carrying our marker in the same pass. `python3 scripts/model-picker.py --off`
does the removal on its own if you would rather not rewrite the config, and
`--uninstall` removes it too.

## Codex

If `codex` is on the PATH and signed in to a ChatGPT plan, setup finds it. Install
with `npm i -g @openai/codex`; if you already use Codex in the ChatGPT desktop app,
the CLI reads that same sign-in and needs no separate login.

Sign in with `codex login` and verify with `codex login status`, which writes to
stderr. `codex doctor` is the fast health check. Setup records the Codex account
only when that status text names it; otherwise it records `unknown`. It will not
open `~/.codex/auth.json` to find out, because that file holds a token, and this
repository does not read tokens to fill in a label.

Codex 0.152.1 shows a directory-trust modal on launch in an unknown directory, and
on a trusted one it spends ten to forty seconds starting MCP servers before it
accepts `/status`. The reader waits for the composer and for the screen to go
quiet; it never presses a key to dismiss a modal.

## Codex: which accounts, and signing each one in

One ChatGPT login can carry more than one Codex allowance, and a person can hold
more than one login. Ask, multi-select, which of these to connect:

- **A work or team workspace**, on ChatGPT Business or Enterprise: the seat an
  employer pays for. Its panel prints a weekly limit.
- **The personal space on that same login.** Every Business login also has a
  "Personal account" with its own plan and its own limits. With no paid plan of
  its own it prints a monthly limit only; on Plus it prints a 5-hour and a weekly
  one.
- **A separate personal account**, a different email on Plus or Pro.

Each one chosen is a Codex role of its own, `codex`, `codex2` or `codex3`, in its
own `CODEX_HOME`. Codex keeps everything about a sign-in under that directory:
`auth.json`, `config.toml`, the trust list and the allowance. Tested on 0.153.0: a
fresh `CODEX_HOME` reports `Not logged in` while `~/.codex` stays signed in, and
nothing in the default directory is touched.

Ask two things per role, in one question if you like: **what to call it** (this
is the label on the panel, set with `--codex-label`, `--codex2-label`,
`--codex3-label`) and **where to keep it**, suggesting a directory named for the
account, such as `~/.codex-work`, `~/.codex-work-personal`, `~/.codex-personal`.
`--detect` lists any `~/.codex-*` directory that already exists and whether it is
signed in, so offer those before creating new ones.

**Do not put a role on `~/.codex` itself if the Codex desktop app is in use.** The
app owns that directory and rewrites `auth.json` when its user switches
workspace, keeping the same email and changing the plan; the CLI's own token
refresh can then write it back to the default workspace. Whichever process last
refreshed decides which allowance a role on `~/.codex` is spending, and nothing
tells second-wind. The reader records the plan it saw and `--check` reports the
drift, but a directory of its own is the fix.

### Before the sign-ins: the browser

`codex login` completes against whichever ChatGPT account the browser already
holds, and a login with several workspaces lands on the one chosen on the
consent page. So, before any command:

- **One login, two workspaces** (a work workspace and its personal space): one
  browser is enough. The consent page has a workspace chooser, work workspaces
  listed by name and the personal space as "Personal account", and that choice
  is what the directory gets bound to.
- **Two logins**: the browser has to be signed into the right one when the
  consent page loads. A browser profile per ChatGPT account is the clean way and
  worth setting up first: in Chrome, the profile icon, then Add, then sign that
  profile into chatgpt.com as the second account. A private window, or signing
  out and back in, also works but has to be repeated every time.
- **If the assistant can drive the browser**, through the Claude in Chrome
  extension, it can open the printed URL in the named profile and click through.
  Connected browsers may show as unnamed "Browser 1" and "Browser 2": open
  chatgpt.com in one and read the account button to tell them apart, and ask
  the user rather than guess if that does not settle it.

### The sign-in, one directory at a time

Try the device-code flow first, because it opens nothing and works from any
browser the user chooses:

```bash
mkdir -p ~/.codex-work
CODEX_HOME=~/.codex-work codex login --device-auth
```

It prints a URL and a one-time code; the user opens the URL where the right
account is signed in and enters the code. Two things stop it, both shown on the
consent page: a work workspace whose admin has not enabled device authorisation
("contact your workspace admin"), and a personal account that has not turned it
on under ChatGPT Security settings. Do not change that setting for the user.
Fall back to the standard flow:

```bash
CODEX_HOME=~/.codex-work codex login
```

It prints an `auth.openai.com/oauth/authorize` URL, opens the default browser
with it whatever `BROWSER` says, and holds port 1455 until it finishes, so
logins go one at a time. If the default browser is not signed into the right
account, leave the tab it opened alone and paste the URL into the right profile.
The page asks for the account, then the workspace (the work one is selected by
default; pick "Personal account" for the personal space), then Continue. The
consent page always needs that click, so a tab in the wrong profile cannot
finish the sign-in by itself. Close the stray tab afterwards.

Then verify, never assume:

```bash
CODEX_HOME=~/.codex-work codex login status
```

and, once the config is written, run that role's reader
(`usage-refresh.sh --force --only codex`) and read the `Account:` line and the
plan in parentheses back from `~/.second-wind/usage-codex.json`. The panel is the
only place this repository reads an identity from; it does not open `auth.json`.

### Writing the config

```bash
python3 scripts/setup.py --write ... \
  --codex on  --codex-dir  ~/.codex-work          --codex-label  "Codex work" \
  --codex2 on --codex2-dir ~/.codex-work-personal --codex2-label "Codex work personal" \
  --codex3 on --codex3-dir ~/.codex-personal      --codex3-label "Codex personal" \
  --force
```

`--force` is needed on the first write when a directory is not signed in yet;
setup creates it, pre-trusts the workdir in it, and prints the login line for
it. Sign in, run the readers, then rerun `--write` without `--force`: setup pins
the account and plan each reader saw, and from then on a reading that disagrees
with the pin shows on the panel as `signed in as X, not the configured Y`.

A new `CODEX_HOME` starts empty: no MCP servers, no plugins, and no
`project_doc_fallback_filenames`, so a worker there reads `AGENTS.md` and not
`CLAUDE.md` unless that line is added to its `config.toml`, above the
`[projects]` table (a TOML key after a table header belongs to that table). For
a headless worker the empty start is mostly a gain: it is what makes the reader
fast.

The desktop app's own session store is account-agnostic: a thread carries a
working directory and an originator, and no account id, which is why its chats
survive a workspace switch. Nothing in it says which allowance paid for a turn.

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
`-p` immediately, or Grok reports that `--single` needs a value.

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
interactive browser login, so an API-key sign-in is delegation only and
`refresh.cursor` stays false. The monthly view reports Included, Auto and API
pools plus a reset date. On-demand may appear unavailable even when the account
holds credit.

## The `agent` command collision

Cursor's installer runs `rm -f ~/.local/bin/agent` and takes that generic name.
That can delete Grok's `agent` alias, but Grok's `grok` command survives. Always
use `grok` and `cursor-agent`, never `agent`.

## Terms of use

Covered once, in the README. Read it before installing.
