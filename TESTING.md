# Testing second-wind

These notes cover the repository checks and a clean-install exercise. Record the
operating system; the Claude Code, Codex, Grok Build and Cursor Agent versions;
which worker accounts you hold; and whether your primary session runs in a
terminal or the desktop app.

Linux is expected to work and is untested. Add the distribution and shell to any
Linux result.

## Repository checks

Run from the repository root:

```bash
python3 -m compileall -q second-wind/scripts
for file in second-wind/scripts/*.sh install.sh; do sh -n "$file"; done
python3 -m unittest discover -s second-wind/tests
shellcheck -s sh second-wind/scripts/*.sh install.sh      # if shellcheck is installed
```

Then reproduce the policy checks in `.github/workflows/checks.yml`: no email
addresses outside the fixtures, no absolute home paths, no em dashes.

**Check after this phase:** compileall silent, every `sh -n` silent, unit tests
all pass, policy greps find nothing.

## Clean-install exercise

Budget about 30 minutes. Use accounts and directories you control. Do not repair
the checkout while testing the documented path: a workaround hides the first
install fault you are trying to measure.

Optionally record the terminal:

```bash
mkdir -p ~/second-wind-test
script -q ~/second-wind-test/session.log
```

Type `exit` when finished. Raw logs and `setup.py --show` carry account
identifiers, so do not send those without reading them first.

Follow the README in order:

1. Install the skill with `./install.sh`.
2. Restart Claude Code, then invoke second-wind setup with no choices supplied.
3. Confirm it detects the current state before asking anything, and states the
   paid cost of every option before the choice.
4. Confirm the worker question is a multi-select, and that the Grok API-key
   warning and the Cursor `agent` name collision warning appear at that choice.
5. Confirm the level question is a single select, with Reviewer, Worker and
   Relief each described in a line.
6. Select the workers you have. Confirm setup walks them one at a time, gives
   only the command that is missing, and rechecks before moving on.
7. Confirm it writes one `--write` command, creates and pre-trusts
   `~/.second-wind/workdir`, and tells you to restart Claude Code.
8. Restart, then run `setup.py --check`.

**Check after this phase:** `--check` reports the level, the hooks for that
level, the status line, the workdir trust for every Claude profile and for Codex,
the launchd agent as loaded, and a final `READY` or `ARMED` line. Any `MISSING`,
`NOT TRUSTED` or `NOT READY` line is a result worth reporting exactly as printed.

## Behaviour checks

Ask for an independent review of something real without naming second-wind or any
of its commands, and see whether the skill loads. Then, separately:

- delegate a piece of work in `--work` mode, in a disposable directory;
- delegate a review to each enabled worker and confirm it wrote nothing;
- run `setup.py --accounts`, then `--accounts --live`, and compare;
- start a new session and read the brief;
- ask whether automatic handover is armed;
- write each enabled worker name, then `all`, to `~/.second-wind/mode`, submit a
  prompt after each change, then remove the file;
- read the latest exchange and ledger entries with `scripts/report.py 7`.

**Check after this phase:** every delegation appears in the ledger with a
sensible exit code and duration; no review-mode run changed a file; the accounts
table sorts by headroom with unknowns last; the brief line matches the table.

In work mode, use a directory you would let an unattended agent modify. The
worker runs with its permission prompts disabled or auto-approved.

## Expected limitations and live costs

- The desktop app runs no status line. The prompt-free readers are how figures
  get there, and they spend no model allowance.
- Codex uses `/status`; Claude, Grok and Cursor use `/usage`. None sends a prompt.
- Readers start live interactive clients. Never run them as part of a static
  repository check. A reader that meets a trust modal stops and reports
  `TRUST PROMPT`; one whose panel labels have moved reports `PARSER MISMATCH`.
- Codex 0.152.1 spends ten to forty seconds starting MCP servers before it will
  accept `/status`, so its reading is the slow one.
- Grok has a weekly window only. Cursor has Included, Auto and API monthly pools,
  and on-demand may read as unavailable while credit exists.
- Codex workers are sandboxed and cannot launch a browser. No test should try.

## Sending the result back

Turn the field notes on for the whole test (`--field-notes on` on the `--write`
line, or say yes when setup asks). While testing, record anything you had to
do, change or get around, at the moment it happens:

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --note "device-code login was blocked by the workspace admin; used the standard flow"
```

The assistant running setup should add these itself whenever a step needed a
workaround, and ask you at the end of setup for anything it missed. Then:

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --field-report
```

That writes `~/second-wind-test-report.md`: the environment, every reader status,
the accounts table, the delegation summary by worker and mode, and the field
notes: setup steps in order, reader outcomes over time, routes and handovers,
and your notes. It opens with a section saying what is in it and what is not.
Email addresses become `<account>`, your home directory becomes `~`, and no
prompt, reply, directory name or project name is copied into it. Your own notes
are included as written, so **read the file before you send it**. Add
`--with-details` to `report.py 30 --share` only for a report to yourself: that
version quotes the last lines of failed replies and names the exchange files.

Exchanges are capped at 200 KB each. Old ones are deleted at the end of the next
delegation, not on a clock: the runner prunes anything older than 30 days as it
finishes, so a machine that has delegated nothing for months still holds every
exchange it had. The ledger line survives the prune, so an old month still says
what was delegated.

Send that file, plus anything you learned that is not already in the
documentation, under these headings:

```markdown
## Environment

## What worked

## What broke

## Where the instructions were unclear

## Checks and command output

## Would I keep it
```
