#!/bin/sh
# Install second-wind into a Claude Code skills directory.
#
#   ./install.sh                installs to ~/.claude/skills/second-wind
#   ./install.sh <dir>          installs to <dir>/second-wind
#   ./install.sh --link [<dir>] symlinks the checkout instead of copying it
#
# --link is for working on the skill itself: edits in the checkout are live with
# no reinstall. A copy is the default, because a symlink breaks the moment the
# checkout moves.
set -e
SRC=$(cd "$(dirname "$0")" && pwd)
LINK=no
if [ "${1:-}" = "--link" ]; then LINK=yes; shift; fi
DEST=${1:-$HOME/.claude/skills}
mkdir -p "$DEST"
rm -rf "$DEST/second-wind"
if [ "$LINK" = yes ]; then
  ln -s "$SRC/second-wind" "$DEST/second-wind"
  echo "Linked $DEST/second-wind to $SRC/second-wind"
  echo "Edits in the checkout are live. Moving the checkout breaks the link."
else
  cp -R "$SRC/second-wind" "$DEST/second-wind"
  chmod +x "$DEST/second-wind/scripts/"*.sh "$DEST/second-wind/scripts/"*.py 2>/dev/null || true
  echo "Installed to $DEST/second-wind"
  echo "This COPIES the skill. Re-run install.sh after editing the checkout."
fi
echo
echo "Restart Claude Code, then say:  set up second-wind"
echo "Or from a terminal:"
echo "  python3 $DEST/second-wind/scripts/setup.py --detect"
echo "  python3 $DEST/second-wind/scripts/setup.py --write --level relief \\"
echo "      --primary ~/.claude --secondary <dir> --codex on"
echo "  python3 $DEST/second-wind/scripts/setup.py --check"
echo "  python3 $DEST/second-wind/scripts/setup.py --accounts"
