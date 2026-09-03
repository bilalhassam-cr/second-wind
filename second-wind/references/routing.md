# Routing: the picker, the presets and the route files

`/second-wind` asks three questions and writes one file. This is what the
answers mean and what the files hold.

## The command

| Typed | What happens |
|---|---|
| `/second-wind` | The picker: destinations, then presets, then review or full access. |
| `/second-wind off` | `route.py --clear`. Back to usage-based handover. |
| `/second-wind <text>` | Does the job now on the worker the words name, or the one with the most headroom. |

In the desktop app the picker is the only way to route: its model menu shows no
rows of ours and fires no hook for a typed id. Typing `second-wind` where a model
goes gets a one-line refusal telling you to use the command instead.

## The destination question, and its four options

AskUserQuestion takes **at most four options**, and "Keep it here" is one of
them. Putting "Keep it here" and four workers in one question silently dropped
the fourth: in a live test Cursor never appeared, so a connected destination
could not be chosen at all.

So the first question is:

| Slot | Option |
|---|---|
| 1 | **Keep it here** |
| 2 | The connected destination with the most headroom |
| 3 | The next one, if there is room |
| 4 | The third destination, or **Another destination** when more are enabled than fit |

**Another destination** leads to a second AskUserQuestion listing the workers
the first page could not show. With three or fewer destinations enabled they all
fit beside "Keep it here", and there is no second page.

Two rules for the first page. The **recommendation stays there**, so the advice
and the options agree. And every destination is shown in the card or table
before the question is asked, because the question cannot list them all: the
options are how to choose, not the full inventory.

## Presets, per subscription

`route.py --destinations` prints these with their descriptions, so the picker
never shows a bare model name.

| Worker | Presets |
|---|---|
| Personal Claude (`secondary`) | `opus/high`, `opus/medium`, `sonnet/medium`, `sonnet/low`, `haiku/low` |
| Codex | `gpt-5.6/high`, `gpt-5.6/medium`, `gpt-5.6/low` |
| Grok Build | `default/high`, `default/medium` |
| Cursor Agent | `default` |

`default` in the model position means pass no model, so the account's own is
used. Grok's effort goes out as `--reasoning-effort`; Cursor takes no effort flag
at all; Codex accepts `minimal`, `low`, `medium`, `high` and `xhigh`, and any of
them can be put in the config. Override per worker with
`setup.py --write --picker-models codex=gpt-5.6/xhigh,codex=gpt-5.6/medium`,
which is carried forward on later writes.

## Keeping the work here

There is no route to write for "keep it here", and **the skill cannot change the
session's model itself**. Offer the presets below and then give the exact lines
to type, because that is the only thing that changes a running session:

| Preset | Type this | For |
|---|---|---|
| Fable 5.1, high | `/model fable` then `/effort high` | The default. Long deliverables. |
| Opus 5, high | `/model opus` then `/effort high` | The hardest reasoning, spends the most. |
| Opus 5, medium | `/model opus` then `/effort medium` | Long jobs at a lower rate. |
| Sonnet 5, medium | `/model sonnet` then `/effort medium` | Everyday tasks, light on the allowance. |
| Haiku 4.5, low | `/model haiku` then `/effort low` | The cheapest pass. |

## The route files

One file per session, `~/.second-wind/routes/<session_id>.json`, written by
`route.py` and by the two hooks and read by the prompt guard and the session
brief. `~/.second-wind/mode` holds the one route meant to apply everywhere: a
bare word, or JSON saying `"session_id": "*"`. Never write either by hand.

Both shapes hold the same fields:

| Field | Meaning |
|---|---|
| `worker` | `secondary`, `codex`, `grok` or `cursor`. |
| `model` | Passed to the runner as `--model`. Null means pass none. |
| `effort` | Passed as `--effort`. Null means pass none. |
| `mode` | `review` or `work`, passed as that flag. Absent means the configured default. |
| `set_at` | Epoch seconds. A route more than 12 hours old is ignored and deleted. |
| `label` | What the brief and the guard call the destination. |
| `source` | `chat`, `desktop`, or absent for the model picker. |
| `session_id` | The session the route applies to, matching the file name. `"*"` in `mode` means every session, and so does an absent one in an older file. |

**One file per session is the point.** Every route used to land in
`~/.second-wind/mode`, so arming one in a second chat overwrote the first: a
desktop arming replaced a picker route somebody was already using. Now a route
applies only in the session that armed it, and two chats can each be routed
somewhere without touching each other.

The guard reads **this session's file first, then the global one**, so a route
armed here beats one left applying everywhere. A route more than 12 hours old is
ignored and its file deleted by whichever reader meets it. `--here` finds the
current session id in the desktop app's own store, by the directory it is
running in, and falls back to every session with a printed note when it cannot.
A bare word in `mode`, such as `codex` on its own, stays global: that is what
somebody echoing into the file expects.

## route.py

```bash
python3 "$SW/scripts/route.py" --destinations
python3 "$SW/scripts/route.py" --set codex --model gpt-5.6 --effort high --here
python3 "$SW/scripts/route.py" --set personal --mode review --all
python3 "$SW/scripts/route.py" --show
python3 "$SW/scripts/route.py" --clear
```

`--here` is the default scope; `--all` writes the global file and says so out
loud in its confirmation, and `--session ID` takes an id you already have.

`--show` lists **every** armed route with its session, label, model, effort,
age, file and whether it applies here. That is the first thing to check when a
route seems to do nothing, or to apply to the wrong chat.

`--clear` clears this session's route, and the global one when that is what was
routing here. It never removes another session's route on a guess: when nothing
can name this session it lists what is armed and asks for `--clear --session ID`
or `--clear --all`. Picking any real model in the terminal picker clears this
session's route the same way.

## The model picker, in a terminal only

`/model second-wind/personal`, or `/model second-wind/codex/gpt-5.6/high`, routes
as well. The switch is refused on purpose: the session keeps its model and the
next tasks go to that worker through the runner, scoped to that session. Picking
any normal model clears that session's route and leaves every other one alone.
`--model-picker on` adds the rows, and `--picker-routes` adds parameterised ones.
