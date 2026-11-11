# Changelog

## 2026.11.10

Found by a review of the whole tool on 10 November 2026, with independent
reads by three other models.

### Fixed

- The scheduled Claude usage reader crashed on every run: the copy it runs from
  lacked a file it imports. A test now reads every reader's imports.
- A crashed reader reports the last line of its error, not "Traceback".
- The model picker no longer leaves a backup of `settings.json` on every
  refresh, and leaves the file alone if it changed while being read.
  Timestamped backups are capped at five per file; the original is kept.
- A delegation no longer forces a usage read when one was made in the last
  five minutes. Fifty jobs a day got an account's usage endpoint rate limited.
- Cursor's sign-in screen is reported as a sign-in fault.
- Log pruning removes only exchange files, not every old `.md` in the folder.
- Setup and uninstall remove a hook only when the file is Second Wind's own.
- The saved original status line is no longer overwritten by our own, and a
  moved install's status line is removed.
- `bridge stop` removes the launchd agent, so it does not return at login.
  Uninstall stops the bridge and removes its stored key and script copies.
- An Antigravity reading older than two hours is not drawn. A config holding
  only named accounts shows no primary row.
- A reset date is placed in the nearest year, so 31 December read just after
  midnight is not a year away.
- The terminal reader does not overwrite a newer status-line reading.
- `route.py --here` uses the session id Claude Code provides.
- A settings file that is a symlink is written through, not replaced.
- Hook commands are quoted when the skill path needs it.
- `run` without a mode flag uses `defaults.mode`, as documented.

### Added

- `health.py`: what needs you and the fix for each, with a line in the session
  brief when an account needs signing in again.
- `update.py`: check for a newer version and install it.
- `install.sh`: says what it found and what to do next, keeps the copy it
  replaces, and has `--dry-run` and `--uninstall`.
- Exit 75 from `run` when an account is out of allowance; `--ignore-usage`.
- `--version`, and this file.

### Changed

- Model presets name Fable 5.2 and Haiku 5.5.
