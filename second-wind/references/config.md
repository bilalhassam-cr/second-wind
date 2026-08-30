# Configuration

`~/.second-wind/config.json`, written by setup, `chmod 600`. Edit by hand or rerun
setup. `scripts/setup.py --show` prints it.

| Key | Meaning |
|---|---|
| `primary.config_dir` | The profile that orchestrates and holds the conversation. Usually `~/.claude`. |
| `secondary.config_dir` | The second Claude profile. Its own account, its own allowance. |
| `secondary.enabled` | Set false to leave it out entirely. |
| `codex.enabled` | Whether Codex is available as a worker. |
| `codex.can_launch_browser` | Always false for Codex because the runner always passes its own sandbox flag in both review and work mode. The command-line flag overrides `sandbox_mode`, so the user's Codex configuration does not change this. It is null only when Codex is not installed. Nothing is launched. When the value is not true, the runner warns the worker off browser work. |
| `thresholds.five_hour_pct` | Failover trigger for the 5-hour window. Default 90. |
| `thresholds.seven_day_pct` | Failover trigger for the weekly window. Default 80. |
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

## Files it creates

```
~/.second-wind/
├── config.json            the settings above
├── usage-primary.json     last limit reading, written by the status bar
├── usage-secondary.json   last interactive secondary reading, usually absent because delegated print mode runs no status line
├── no-failover            present = automatic handover off
├── mode                   optional manual override: secondary, codex or both
└── log/
    ├── YYYY-MM.jsonl      one line per delegated call
    └── <ts>-<pid>-<worker>-<folder>.md   full prompt and reply
```
