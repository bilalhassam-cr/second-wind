# Testing second-wind: a pack for a first-time installer

You are the first person to install this who did not build it. That is exactly
what makes your feedback useful. Please do not fix anything, do not read the
source first, and do not work around problems. Follow the README as written and
record what happens, including the parts that go wrong.

Budget about 30 minutes.

## Before you start

Say what you are starting from:

- Operating system and version
- Do you have one Claude account or two?
- Do you have Codex signed in to a ChatGPT plan?
- Do you use Claude Code in the terminal, in the desktop app, or both?

That last one matters more than it looks.

## Start the log

```bash
mkdir -p ~/second-wind-test
script -q ~/second-wind-test/session.log
```

That records everything you type and everything you see, into one file. Type
`exit` when you are done. If `script` is not available, just copy and paste your
terminal output into a file instead.

## The run

Work through the README from the top. At each step, note:

1. What you expected to happen
2. What actually happened
3. Whether you had to think, guess, or look something up

The steps are: install, create a second profile if you do not have one, discover,
write the config, restart, then `--check`.

**Please record every moment where you were unsure what to do next.** Those are
worth more than the errors, because errors announce themselves and confusion does
not.

## Then try to use it

Ask Claude, in your own words, for a second opinion on something real you are
working on. Do not use the phrase "second wind" and do not name any command. We
need to know whether it works when nobody is helping it along.

Then try these, in your own words:

- Have the other account do a piece of actual work, not just review it
- Ask what your usage is
- Ask it to check whether the automatic handover is armed

## What to send back

One file, `~/second-wind-test/report.md`, with these five headings. Blunt is
better than polite; the point is to find what is broken.

```markdown
## Where I started
(OS, accounts, terminal or desktop app)

## What worked
(one line each)

## What broke
(what you did, what you expected, what happened, the exact error)

## Where I was confused
(the moments you had to guess, even if you guessed right)

## Would I keep it
(yes or no, and the single change that would most improve it)
```

Send that file plus `session.log`, plus the output of these three commands:

```bash
python3 ~/.claude/skills/second-wind/scripts/setup.py --check
python3 ~/.claude/skills/second-wind/scripts/setup.py --show
python3 ~/.claude/skills/second-wind/scripts/report.py 7
```

`--show` includes your account email addresses. Redact them if you would rather
not share those; nothing else in it is sensitive.

## Two things that are known to be shaky

Tell us if you hit either, but they are not surprises:

- The status bar and the automatic handover are inert until you restart Claude
  Code. If `--check` says NONE YET after a restart and one message, say so: that
  is the failure we most want to hear about.
- Everything so far has only ever run on one Mac. Anything on Linux is untested.
