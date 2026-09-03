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

# Three things are run as commands rather than imported: the shell scripts, the
# setup entry point, and the hooks, which Claude Code executes by absolute path
# from settings.json. Everything else is imported by python3 and needs no
# executable bit. Repaired here because a clone copied across a filesystem that
# drops permissions fails later as a permission error nobody traces back to the
# install.
set_exec() {
  chmod +x "$1/scripts/"*.sh "$1/scripts/setup.py" "$1/scripts/hooks/"*.py 2>/dev/null || true
}

mkdir -p "$DEST"
# Resolve the destination before deleting anything. Installing into the checkout,
# or into its parent, makes the target the source, and the rm below would then
# delete the thing being installed. Refuse instead: there is nothing to recover
# from afterwards.
DEST=$(CDPATH= cd "$DEST" && pwd)
if [ "$DEST/second-wind" = "$SRC/second-wind" ] || [ "$DEST/second-wind" = "$SRC" ]; then
  echo "install.sh: $DEST/second-wind is this checkout. Pick a skills directory" >&2
  echo "outside it, such as ~/.claude/skills, and nothing is deleted." >&2
  exit 1
fi
rm -rf "$DEST/second-wind"
if [ "$LINK" = yes ]; then
  ln -s "$SRC/second-wind" "$DEST/second-wind"
  # The link points at the checkout, so the bits have to be set there.
  set_exec "$SRC/second-wind"
  echo "Linked $DEST/second-wind to $SRC/second-wind"
  echo "Edits in the checkout are live. Moving the checkout breaks the link."
else
  cp -R "$SRC/second-wind" "$DEST/second-wind"
  set_exec "$DEST/second-wind"
  echo "Installed to $DEST/second-wind"
  echo "This COPIES the skill. Re-run install.sh after editing the checkout."
fi
echo
echo "Restart Claude Code, then say:  set up second-wind"
echo "Or from a terminal:"
echo "  python3 $DEST/second-wind/scripts/setup.py --detect"
echo "  python3 $DEST/second-wind/scripts/setup.py --write --level relief \\"
echo "      --primary ~/.claude --secondary <dir> \\"
echo "      --codex on --grok off --cursor off"
echo "  python3 $DEST/second-wind/scripts/setup.py --check"
echo "  python3 $DEST/second-wind/scripts/setup.py --accounts"
