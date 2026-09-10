# Configuration

`~/.second-wind/config.json`, version 4, written by setup, `chmod 600`. Edit by hand
or rerun setup. `scripts/setup.py --show` prints it. `examples/config.example.json`
in the repository is a complete sample.

| Key | Meaning |
|---|---|
| `version` | Config schema version. This build writes and expects 4. `--check` fails an older one. |
| `level` | `reviewer`, `worker` or `relief`. Sets `defaults.mode`, `failover.enabled` and which hooks setup installs. |
| `primary.config_dir` | The profile that orchestrates and holds the conversation. Usually `~/.claude`. |
| `primary.label` | What the session brief calls this account. Defaults to the role name. |
| `primary.plan` | Plan reported during discovery. The refreshed cache stays authoritative for usage. |
| `secondary.config_dir` | The second Claude profile. Its own account, its own allowance. |
| `secondary.enabled` | Set false to leave it out entirely. |
| `reader.enabled` | Optional third Claude profile used only to read the primary's usage, so a background reader never shares a credential with the desktop app. When false, the primary reader runs with `CLAUDE_CONFIG_DIR` unset. |
| `codex.enabled` | Whether Codex is available as a worker. |
| `codex.config_dir` | The `CODEX_HOME` this role runs under. Default `~/.codex`, which is also the directory the Codex desktop app writes: switching workspace in the app rewrites its `auth.json`, and a role sharing that directory changes sign-in with it. Give the role a directory of its own to stop that. Only a non-default directory is ever exported. |
| `codex.full_access` | When true, work mode runs Codex with `--dangerously-bypass-approvals-and-sandbox`: no sandbox, no approval prompts, and a browser can start, so the browser guard is not added to the prompt. Default false. Review mode is read-only regardless. Set with `--codex-full-access on`, which applies to every Codex role. |
| `codex2.enabled`, `codex3.enabled` | A second and a third Codex sign-in, each in its own `CODEX_HOME`. Same fields as `codex`; the `config_dir` is required and must differ from every other Codex role's. `codex3` is set with `--codex3 on --codex3-dir`, routes as `second-wind/codex3`, and reads into `usage-codex3.json`. Set with `--codex2 on --codex2-dir ~/.codex-personal`, then `CODEX_HOME=~/.codex-personal codex login`. Its reader writes `usage-codex2.json`, its runner name is `codex2`, and its routing ids are `second-wind/codex2` and the alias `codex-personal`. |
| `<role>.account`, `<role>.plan` | What the config expects the sign-in to be. A reader records what it actually saw, and the panel and `--check` say `signed in as X, not the configured Y` when the two differ on either field. The plan is compared as well as the account because a workspace switch keeps the email and changes the plan. `unknown` on either side is never a drift, and neither is a dead reading. Setup pins these from the newest source first: the live probe, then a reading young enough to show, then the previous config. So after a deliberate change, refresh the reading (`usage-refresh.sh --force --only codex`) and rerun `--write`. |
| `grok.enabled` | Whether Grok Build is available as a worker. Setup checks the login by running `grok models`, which needs a session and sends no prompt. |
| `cursor.enabled` | Whether Cursor Agent is available as a worker. |
| `cursor.auth` | `interactive` or `api_key`. An API key authorises delegation only: the usage panel needs a session, so `refresh.cursor` stays false. The runner clears every vendor API key it knows about before starting any worker, whichever vendor the key belongs to, and reads this field for the one exception: `CURSOR_API_KEY` is kept for a Cursor worker on an `api_key` sign-in, or when the field is missing, and cleared on an `interactive` one so the browser session pays. The exchange records what was cleared and what was kept. |
| `thresholds.five_hour_pct` | Handover trigger for the 5-hour window. Default 90. |
| `thresholds.seven_day_pct` | Handover trigger for the weekly window. Default 80. |
| `refresh.interval_minutes` | How old a reading may be before it counts as stale. Default 15. Also the launchd interval. |
| `refresh.workdir` | `~/.second-wind/workdir`, created empty by setup and pre-trusted for each Claude profile and for Codex. The readers start here so a trust modal cannot block them. |
| `refresh.launchd` | Whether the scheduled refresh agent is installed. macOS only. |
| `refresh.model_picker` | Whether the `/model` rows are relabelled with the live figures. Setup only records the choice; the refresh writes the `modelPicker` key itself, marked as ours so uninstall can remove it. |
| `refresh.cursor` | Whether the Cursor usage reader runs. False for an API-key sign-in. |
| `picker.routes` | Extra routing ids to show in the model picker, as a list. Default empty. Every connected worker already gets a row; this is for parameterised ones such as `second-wind/codex/gpt-5.6/high`, which route and choose a model and an effort in one pick. Set with `--picker-routes id,id`. An id naming a worker that is not connected is dropped from the picker rather than shown. |
| `picker.models` | The model and effort presets `/second-wind` offers per worker, as `{"codex": ["gpt-5.6/high", "gpt-5.6/low"]}`. A worker the block does not name keeps the built-in presets: secondary `opus/high`, `opus/medium`, `sonnet/medium`, `sonnet/low`, `haiku/low`; codex `gpt-5.6/high`, `gpt-5.6/medium`, `gpt-5.6/low`; grok `default/high`, `default/medium`; cursor `default`. `default` in the model position means pass no model. Codex accepts `minimal`, `low`, `medium`, `high` and `xhigh`; Grok's effort goes out as `--reasoning-effort`; Cursor takes no effort flag. Set with `--picker-models worker=model/effort,...`, repeating a worker to add rows. A row that cannot be parsed is dropped rather than shown. |
| `failover.enabled` | Master switch for automatic handover. True at level `relief` only. |
| `failover.announce` | Handover always says so. Kept as a key because silence is never the default. |
| `defaults.mode` | `review` (read-only) or `work` (full access) when no mode is passed. |
| `log.dir` | Where the ledger and the exchange files go. |
| `log.max_exchange_kb` | Size cap per saved exchange. |
| `log.prune_days` | How long exchanges are kept. |
| `timeout_seconds` | Seconds before a delegated call is killed. |
| `tested_versions` | Client versions present when setup ran. `--check` warns when an installed client has moved on, because a moved label is the usual cause of a reading that stops parsing. |
| `skill_dir` | Where the skill lives, so SKILL.md can find its own scripts. |

## Service levels

| Level | Mode | Automatic handover | Hooks installed |
|---|---|---|---|
| `reviewer` | review | off | SessionStart brief, PreModelSwitch routing |
| `worker` | work | off | those two, plus StopFailure, Notification, PostModelSwitch |
| `relief` | work | on | the five above plus the UserPromptSubmit guard |

The guard also goes into the secondary profile, where it exits at once because it is
not the primary session. The status line goes into both.

Two hooks are installed with a matcher so they fire only on the events that mean the
allowance ran out: `StopFailure` on `rate_limit`, and `Notification` on
`quota_auto_resume_fired|quota_auto_resume_stale|quota_auto_resume_disabled`.

## Choosing thresholds

The primary account needs headroom left to orchestrate the handover, which is why the
default trips at 90% rather than 100%. Lower it if you routinely run long sessions and
want the switch earlier; raising it much above 90 risks having too little left to hand
anything over.

The weekly threshold sits lower at 80% on purpose. A weekly window recovers slowly, so
crossing it matters more than a 5-hour window that refills within the day.

## Staleness, one rule

Every hook, `--check` and `--accounts` ask the same function:

- **fresh**: younger than `refresh.interval_minutes`.
- **stale**: older than that. Still shown, always with its age.
- **dead**: older than 60 minutes. The guard ignores it and the brief says "no current
  reading" rather than quoting an hour-old percentage.
- **none**: no reading at all.

## Headroom

Claude and Codex: `100 - max(the windows the client reported)`. Both windows are used when
both are printed, so a nearly full weekly window cannot hide behind a quiet 5-hour one, and
a window the panel does not print is skipped rather than fatal. The Codex status panel on
some plans prints the weekly limit and no 5-hour line, and treating that as unreadable made
Codex unroutable. Headroom is unknown only when neither window is present. Grok reports a
weekly window only, so it is `100 - weekly`. Cursor is `100 - max(Included, Auto, API)`.
On-demand availability is context and does not override those pools. Unknown sorts last.

## Files it creates

```
~/.second-wind/
├── config.json                the settings above
├── workdir/                   empty, pre-trusted, where every reader starts
├── runtime/                   the refresh script, swlib, the readers and the
│                              picker, mirrored here because a launchd agent
│                              cannot read a skill kept under ~/Documents
├── usage-primary.json         last primary reading
├── usage-secondary.json       last secondary reading
├── usage-codex.json           last Codex status-panel reading
├── usage-grok.json            last Grok weekly reading
├── usage-cursor.json          last Cursor monthly-pools reading
├── refresh-status-*.txt       one outcome line per role: OK, LOGIN EXPIRED,
│                              TRUST PROMPT, PARSER MISMATCH or FAILED
├── replaced-statusline.json   any status line we replaced, restored on uninstall
├── no-failover                present = automatic handover off
├── mode                       the one route armed for every session
├── routes/
│   └── <session_id>.json      one route per session: where its next tasks go
└── log/
    ├── YYYY-MM.jsonl          one line per delegated call
    └── <ts>-<pid>-<worker>-<folder>.md   full prompt and reply
```

A status file newer than its reading wins: it describes the attempt that came after
the cached figures.

## The route files

`routes/<session_id>.json` and `mode` are written by `scripts/route.py` and by
the two routing hooks, and read by the prompt guard and the session brief. Never
write either by hand. Both hold the same fields.

| Field | Meaning |
|---|---|
| `worker` | `secondary`, `codex`, `codex2`, `codex3`, `grok` or `cursor`. |
| `model` | Passed to the runner as `--model`. Null means pass none. |
| `effort` | Passed as `--effort`. Null means pass none. |
| `mode` | `review` or `work`, passed as that flag. Absent means `defaults.mode`. |
| `set_at` | Epoch seconds. A route older than 12 hours is ignored and deleted. |
| `label` | What the brief and the guard call the destination. |
| `source` | `chat`, `desktop`, or absent for the model picker. |
| `session_id` | The session the route applies to. `"*"` means every session, and an older file naming no session is read the same way. |

A route applies only in the session that armed it, which is what stops a
destination picked in one chat from telling every other session to send its work
away. One file each is what makes that true: while every route shared `mode`, the
second arming overwrote the first. A session's own file is read before `mode`, so
a route armed here beats one armed for every session, and a bare word in `mode`,
such as `codex` on its own, stays global. `references/routing.md` carries the
rest.

## Files it edits, and the backups it leaves

Setup edits the `settings.json` of the primary and secondary profiles, the `.claude.json`
of each Claude profile it pre-trusts, and the `config.toml` in each Codex role's
`CODEX_HOME` (`~/.codex/config.toml` by default). Before the first edit of
any of them it writes `<file>.second-wind-original`, which is never overwritten, and it
writes a timestamped copy on every later write.

Restart Claude Code after `--write`. A session that was already running holds its own copy
of the project list and can write it back on exit, which drops the trust entry setup just
added. `--check` reads the trust store back for every Claude profile and for Codex and says
`trusted` or `NOT TRUSTED`, so a pre-trust that did not survive is visible rather than
discovered by a reader sitting on a modal.

`--uninstall` removes our hooks, our status line, our `modelPicker` key, the routing
override at `~/.second-wind/mode`, the per-session routes in `~/.second-wind/routes/`,
the runtime mirror and the launchd agent, restores a status line it
replaced, and leaves accounts and logins alone. It leaves
the workdir trust entries in place, because removing them means editing files a running
client may be writing, and it prints where to delete them by hand.
