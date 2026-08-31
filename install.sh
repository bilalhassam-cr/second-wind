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
echo
echo "Note: this COPIES the skill. Re-run install.sh after editing the checkout."
echo
echo "Next, in a Claude Code session, say:  set up second-wind"
echo "or from a terminal:"
echo "  python3 $DEST/second-wind/scripts/setup.py --detect"
echo "  python3 $DEST/second-wind/scripts/setup.py --write --primary ~/.claude --secondary <dir>"
echo "  python3 $DEST/second-wind/scripts/setup.py --accounts"
echo "  python3 $DEST/second-wind/scripts/setup.py --check"
