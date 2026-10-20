#!/bin/sh
# Install Second Wind for Claude Code, Codex Wind for Codex, or both.
set -eu
# shellcheck disable=SC1007
SOURCE=$(CDPATH= cd "$(dirname "$0")" && pwd)
exec python3 "$SOURCE/install.py" "$@"
