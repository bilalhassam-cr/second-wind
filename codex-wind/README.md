# Codex Wind is now Second Wind

Use **Second Wind** in either Claude Code or Codex. Both installations load the
same `second-wind/SKILL.md`, runner, account configuration and history.

From this repository root, run `./install.sh --codex`, `--claude` or `--both`.
The old `scripts/codex_wind.py` path is a compatibility command for source
checkouts. It now uses the shared Second Wind configuration.

To preserve an existing Codex Wind account list and exchange history:

```bash
python3 second-wind/scripts/second_wind.py migrate-codex-wind
```

Original configuration and exchanges remain untouched. See the root README and
`second-wind/references/setup.md` for current setup and migration instructions.
