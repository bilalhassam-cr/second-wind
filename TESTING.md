# Testing second-wind

These notes cover repository checks and a clean-install exercise. Record the
operating system, Claude Code and Codex versions, available worker accounts, and
whether the primary session runs in a terminal or the desktop app.

Linux is expected to work but remains untested. Add the distribution and shell to
any Linux result.

## Repository checks

Run from the repository root:

```bash
python3 -m compileall -q second-wind/scripts
for file in second-wind/scripts/*.sh install.sh; do sh -n "$file"; done
shellcheck -s sh second-wind/scripts/*.sh install.sh
```

Then reproduce the policy checks in `.github/workflows/checks.yml`. The tree must
contain no personal email addresses, and the project rule also forbids em dashes.

## Clean-install exercise

Budget about 30 minutes. Use accounts and directories that the tester controls.
Do not repair the checkout while testing the documented path, because a workaround
can hide the first-install fault being measured.

Optionally start a terminal transcript:

```bash
mkdir -p ~/second-wind-test
script -q ~/second-wind-test/session.log
```

Type `exit` when finished. If `script` is unavailable, save the terminal output
another way. Logs and `setup.py --show` can contain account identifiers, so redact
them before sharing.

Follow the README in order:

1. Install the skill.
2. Restart Claude Code, then ask it to set up second-wind.
3. Create and sign in to a secondary profile if required.
4. Discover the available workers and write the config.
5. Restart after the settings change.
6. Run `setup.py --check` and verify that it reports the status line and usage
   guard as installed.

For every step, record what was expected, what happened, the exact error if any,
and any point where the next action was unclear.

## Behaviour checks

Ask for an independent review of a real item without naming second-wind or one of
its commands. Then test these separately:

- delegate a piece of work in `--work` mode;
- ask for the primary usage reading;
- ask whether automatic handover is armed;
- write each of `secondary`, `codex` and `both` to `~/.second-wind/mode`, submit a
  prompt after each change, then remove the file;
- inspect the latest exchange and ledger entries with `scripts/report.py 7`.

In work mode, use a disposable directory. The worker runs unattended with its
permission prompts disabled or automatically approved.

## Expected limitations

- The desktop app does not run status lines. It cannot write the usage cache or
  arm automatic handover, although manual delegation still works.
- Delegated secondary work uses `claude -p`, which runs no status line. A
  `usage-secondary.json` reading is normally absent and is not refreshed by
  delegated work.
- Codex workers are sandboxed and cannot launch a browser. No test should try to
  launch one from a worker.

## Result format

Keep a short report with these headings:

```markdown
## Environment

## What worked

## What broke

## Where the instructions were unclear

## Checks and command output

## Would I keep it
```
