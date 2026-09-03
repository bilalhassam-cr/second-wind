# Routing: the picker, the presets and the route file

`/second-wind` asks three questions and writes one file. This is what the
answers mean and what the file holds.

## The command

| Typed | What happens |
|---|---|
| `/second-wind` | The picker: destinations, then presets, then review or full access. |
| `/second-wind off` | `route.py --clear`. Back to usage-based handover. |
| `/second-wind <text>` | Does the job now on the worker the words name, or the one with the most headroom. |

In the desktop app the picker is the only way to route: its model menu shows no
rows of ours and fires no hook for a typed id. Typing `second-wind` where a model
goes gets a one-line refusal telling you to use the command instead.

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

## The route file

`~/.second-wind/mode`, written by `route.py` and by the two hooks, read by the
prompt guard and the session brief. Never write it by hand.

| Field | Meaning |
|---|---|
| `worker` | `secondary`, `codex`, `grok` or `cursor`. |
| `model` | Passed to the runner as `--model`. Null means pass none. |
| `effort` | Passed as `--effort`. Null means pass none. |
| `mode` | `review` or `work`, passed as that flag. Absent means the configured default. |
| `set_at` | Epoch seconds. A route more than 12 hours old is ignored and deleted. |
| `label` | What the brief and the guard call the destination. |
| `source` | `chat`, `desktop`, or absent for the model picker. |
| `session_id` | The session the route applies to. `"*"` means every session. Absent, in an older file, also means every session. |

**Scope is the point.** A route applies only in the session that armed it, so
picking a destination in one chat no longer tells every other session to send its
work away. `--here` finds the current session id in the desktop app's own store,
by the directory it is running in, and falls back to every session with a printed
note when it cannot. A bare word in the file, such as `codex` on its own, stays
global: that is what somebody echoing into the file expects.

## route.py

```bash
python3 "$SW/scripts/route.py" --destinations
python3 "$SW/scripts/route.py" --set codex --model gpt-5.6 --effort high --here
python3 "$SW/scripts/route.py" --set personal --mode review --all
python3 "$SW/scripts/route.py" --show
python3 "$SW/scripts/route.py" --clear
```

`--here` is the default scope; `--all` says so out loud in its confirmation, and
`--session ID` takes an id you already have. `--show` names the session the route
belongs to and says whether it applies here, which is the first thing to check
when a route seems to do nothing.

## The model picker, in a terminal only

`/model second-wind/personal`, or `/model second-wind/codex/gpt-5.6/high`, routes
as well. The switch is refused on purpose: the session keeps its model and the
next tasks go to that worker through the runner, scoped to that session. Picking
any normal model deletes the route. `--model-picker on` adds the rows, and
`--picker-routes` adds parameterised ones.
