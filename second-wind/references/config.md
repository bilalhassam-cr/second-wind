# Configuration

`~/.second-wind/config.json`, written by setup, `chmod 600`. Edit by hand or rerun
setup. `scripts/setup.py --show` prints it.

| Key | Meaning |
|---|---|
| `primary.config_dir` | The profile that orchestrates and holds the conversation. Usually `~/.claude`. |
| `primary.plan` | Plan reported during discovery. The refreshed cache remains authoritative for usage. |
| `secondary.config_dir` | The second Claude profile. Its own account, its own allowance. |
| `secondary.enabled` | Set false to leave it out entirely. |
| `codex.enabled` | Whether Codex is available as a worker. |
| `codex.plan` | Fallback plan label. The latest Codex cache supplies the live plan. |
| `codex.can_launch_browser` | Always false for Codex because the runner always passes its own sandbox flag in both review and work mode. The command-line flag overrides `sandbox_mode`, so the user's Codex configuration does not change this. It is null only when Codex is not installed. Nothing is launched. When the value is not true, the runner warns the worker off browser work. |
| `grok.enabled` | Whether Grok Build is available as a worker. |
| `cursor.enabled` | Whether Cursor Agent is available as a worker. |
| `thresholds.five_hour_pct` | Failover trigger for the 5-hour window. Default 90. |
| `thresholds.seven_day_pct` | Failover trigger for the weekly window. Default 80. |
| `refresh.interval_minutes` | How old a reading may be before another refresh is attempted. Default 15. |
| `refresh.working_dir` | A directory already trusted by the primary profile. Usage readers start here so a trust prompt cannot block them. |
| `refresh.codex_enabled` | Whether the Codex `/status` reader runs for an enabled Codex worker. |
| `refresh.grok_enabled` | Whether the Grok `/usage` reader runs for an enabled Grok worker. |
| `refresh.cursor_enabled` | Whether the Cursor `/usage` reader runs for an enabled Cursor worker. |
| `failover.enabled` | Master switch for automatic handover. |
| `defaults.mode` | `review` (read-only) or `work` (full access) when no mode is passed. |
| `log_dir` | Where the ledger and exchange files go. |

## Choosing thresholds

The primary account needs headroom left to orchestrate the handover, which is why
the default trips at 90% rather than 100%. Lower it if you routinely run long
sessions and want the switch earlier; raising it much above 90 risks having too
little left to hand anything over.

The weekly threshold sits lower at 80% on purpose. A weekly window recovers slowly,
so crossing it matters more than a 5-hour window that refills within the day.

## Usage refresh and headroom

`usage-refresh.sh` opens each enabled official client's usage panel in the
recorded trusted directory. It sends no model prompt. The interval is
configurable; 15 minutes is the default. One failed client does not hide the
others, and missing or unreadable figures remain unknown.

Claude and Codex headroom is `100 - max(5-hour used, weekly used)`, and both
figures are required. Codex percentages remaining are converted to percentages
used. Its monthly credits cap overage only and do not decide headroom.

Grok has a weekly window only, so its headroom is the weekly percentage
remaining. Cursor headroom uses the most spent reported monthly pool among
Included, Auto and API. On-demand availability is shown as context and does not
override those pools. Unknown readings sort last.

## Files it creates

```
~/.second-wind/
├── config.json            the settings above
├── usage-primary.json     last primary status-line or usage-panel reading
├── usage-secondary.json   last secondary reading
├── usage-codex.json       last Codex status-panel reading
├── usage-grok.json        last Grok weekly reading
├── usage-cursor.json      last Cursor monthly-pools reading
├── refresh-status-*.txt   one outcome file per profile, including login failures
├── no-failover            present = automatic handover off
├── mode                   optional manual worker override
└── log/
    ├── YYYY-MM.jsonl      one line per delegated call
    └── <ts>-<pid>-<worker>-<folder>.md   full prompt and reply
```
