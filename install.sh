#!/bin/sh
# Install second-wind into a Claude Code skills directory.
#   ./install.sh              installs to ~/.claude/skills/second-wind
#   ./install.sh <dir>        installs to <dir>/second-wind
set -e
SRC=$(cd "$(dirname "$0")" && pwd)
DEST=${1:-$HOME/.claude/skills}
mkdir -p "$DEST"
rm -rf "$DEST/second-wind"
cp -R "$SRC/second-wind" "$DEST/second-wind"
chmod +x "$DEST/second-wind/scripts/"*.sh "$DEST/second-wind/scripts/"*.py 2>/dev/null || true
echo "Installed to $DEST/second-wind"
echo
echo "Next, in a Claude Code session:   /second-wind setup"
echo "or from a terminal:               python3 $DEST/second-wind/scripts/setup.py --discover"
