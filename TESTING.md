# Testing second-wind

These notes cover repository checks and a clean-install exercise. Record the
operating system; Claude Code, Codex, Grok Build and Cursor Agent versions;
available worker accounts; and whether the primary session runs in a terminal or
the desktop app.

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
2. Restart Claude Code, then invoke second-wind setup with no worker choices.
3. Confirm it detects current state first, explains every option's paid cost,
   and asks which combination of second Claude, Codex, Grok Build and Cursor to
   connect.
4. Confirm the Grok API-key warning and Cursor-over-Grok `agent` collision warning
   appear at the choice, not later.
5. Select the available workers. Confirm setup walks them one at a time, omits
   commands already satisfied, and re-checks each install and sign-in before
   moving on.
6. Confirm setup writes the config and finishes with the sorted accounts table.
   A full five-account exercise has rows for primary Claude, secondary Claude,
   Codex, Grok and Cursor.
7. Restart after the settings change.
8. Run `setup.py --check` and verify every configured Claude status line and
   guard plus every enabled worker command is reported as installed.

For every step, record what was expected, what happened, the exact error if any,
and any point where the next action was unclear.

## Behaviour checks

Ask for an independent review of a real item without naming second-wind or one of
its commands. Then test these separately:

- delegate a piece of work in `--work` mode;
- delegate a review to each enabled worker and verify it does not write;
- run `setup.py --accounts` and inspect the sorted headroom table;
- ask whether automatic handover is armed;
- write each enabled worker name and then `all` to `~/.second-wind/mode`, submit a
  prompt after each change, then remove the file;
- inspect the latest exchange and ledger entries with `scripts/report.py 7`.

In work mode, use a disposable directory. The worker runs unattended with its
permission prompts disabled or automatically approved.

## Expected limitations and live costs

- The desktop app does not run status lines. The prompt-free Claude reader opens
  `/usage` directly and spends no model allowance.
- Codex uses `/status`; Grok and Cursor use `/usage`. None sends a model prompt.
- Usage readers start live interactive clients. Do not run them as part of a
  static repository check. Exercise them only in an explicitly authorised live
  account test.
- Grok has a weekly window only. Cursor has Included, Auto and API monthly pools,
  and on-demand may be unavailable even when credit exists.
- Codex workers are sandboxed and cannot launch a browser. No test should try to
  launch one from a worker.

## Result format

Send back the delegation log directory, every `refresh-status-*.txt` file, the
complete `setup.py --accounts` output, and anything learned that is not already
in the documentation. Redact account email addresses and any other account
identifiers before sharing.

Keep a short report with these headings:

```markdown
## Environment

## What worked

## What broke

## Where the instructions were unclear

## Checks and command output

## Would I keep it
```
