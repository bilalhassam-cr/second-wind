#!/usr/bin/env python3
"""Install, update or remove the Second Wind skill for Claude Code, Codex or both.

    ./install.sh                 look at this machine, then ask where to install
    ./install.sh --both          no questions: --claude, --codex or --both
    ./install.sh --dry-run       say what would happen and change nothing
    ./install.sh --uninstall     remove the skill, its hooks and its background jobs

Running it again over an existing install updates that install. The copy it
replaces is kept once, under ~/.second-wind/backups, until the next install.
Installing never signs in to anything and never changes an account.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

CLIENTS = (("claude", "Claude Code"), ("codex", "Codex"), ("grok", "Grok Build"),
           ("cursor-agent", "Cursor Agent"), ("agy", "Antigravity (Gemini)"))
GEMINI_APP_AGY = "/Applications/Gemini.app/Contents/Helpers/agy"


def state_dir():
    return Path(os.environ.get("SW_HOME") or str(Path.home() / ".second-wind"))


def record_path():
    return state_dir() / "install.json"


def read_record():
    try:
        data = json.loads(record_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def git(root, *args):
    """One git answer about the checkout, or an empty string. Never raises:
    a download with no git folder installs just as well."""
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def version(root):
    try:
        return (root / "second-wind" / "VERSION").read_text().strip()
    except OSError:
        return "unknown"


def write_record(root, harness, target, link):
    """Remember where each install came from, so `update` can find it again."""
    data = read_record()
    data["source"] = str(root)
    data["url"] = git(root, "remote", "get-url", "origin") or data.get("url") or ""
    data["commit"] = git(root, "rev-parse", "HEAD")
    data["version"] = version(root)
    data["installed_at"] = int(time.time())
    data.setdefault("installs", {})[harness] = {"path": str(target), "link": bool(link)}
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(dir=folder, prefix=".install-")
    with os.fdopen(handle, "w") as out:
        json.dump(data, out, indent=2)
        out.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, record_path())


def install(source, parent, link, harness=None, keep_previous=True):
    if not (source / "SKILL.md").is_file():
        raise ValueError(f"{source} has no SKILL.md")
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / source.name
    resolved = target.resolve()
    if resolved == source.resolve() and not target.is_symlink():
        raise ValueError(f'{target} is the source checkout; choose a skills directory outside it')
    if resolved == source.parent.resolve():
        raise ValueError(f'{target} contains the source checkout')
    # Refuse a skills directory inside the skill itself, including via symlinks.
    if source.resolve() in parent.resolve().parents or parent.resolve() == source.resolve():
        raise ValueError('destination is inside the source skill')
    updated = target.exists() or target.is_symlink()
    with tempfile.TemporaryDirectory(prefix='.wind-install-', dir=parent) as temporary:
        stage = Path(temporary) / source.name
        if link:
            stage.symlink_to(source, target_is_directory=True)
        else:
            shutil.copytree(source, stage, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for item in (stage / 'scripts').rglob('*'):
            if item.is_file() and (item.suffix == '.sh' or item.name == 'setup.py' or item.parent.name == 'hooks'):
                item.chmod(item.stat().st_mode | 0o111)
        backup = Path(temporary) / 'previous'
        if updated:
            target.rename(backup)
        try:
            stage.rename(target)
        except OSError:
            if updated:
                backup.rename(target)
            raise
        # A copy that was replaced is kept once, so a hand edit or a bad update
        # can be got back. A link has nothing in it to keep.
        if updated and keep_previous and backup.is_dir() and not backup.is_symlink():
            kept = state_dir() / 'backups' / f'previous-install-{harness or "skill"}'
            kept.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.rmtree(kept, ignore_errors=True)
            shutil.move(str(backup), str(kept))
    word = 'Linked' if link else 'Updated' if updated else 'Installed'
    print(f'{word} {target}')
    return target


def look(args):
    """What is on this machine, as lines to print and the harnesses found."""
    lines, found = [], []
    for command, name in CLIENTS:
        path = shutil.which(command)
        if not path and command == "agy" and os.access(GEMINI_APP_AGY, os.X_OK):
            path = GEMINI_APP_AGY
        lines.append(f"  {'found  ' if path else 'missing'}  {name}")
    if shutil.which('claude') or args.claude_dir.parent.exists():
        found.append('claude')
    if shutil.which('codex') or args.codex_dir.parent.exists():
        found.append('codex')
    if not shutil.which('jq'):
        lines.append("  missing  jq (only the Claude Code usage panel needs it)")
    return lines, found


def removable(target, root):
    """Whether a folder is an installed copy of the skill and nothing more.

    Not this checkout or anything holding it, not a git checkout of any kind
    (somebody cloned the repository straight into their skills folder, and
    their work is in it), and not a folder without the skill's own SKILL.md.
    """
    here, checkout = target.resolve(), root.resolve()
    if here == checkout or here in checkout.parents or checkout in here.parents:
        return False
    if (target / '.git').exists():
        return False
    try:
        return 'name: second-wind' in (target / 'SKILL.md').read_text()[:400]
    except OSError:
        return False


def uninstall(args, root):
    """Remove the skill and everything it put on this machine except the
    config, the history and the sign-ins, which are the user's."""
    record = read_record().get("installs") or {}
    targets = {(args.directory or args.claude_dir).expanduser() / 'second-wind',
               args.codex_dir.expanduser() / 'second-wind'}
    targets.update(Path(entry["path"]) for entry in record.values() if entry.get("path"))
    present = sorted(target for target in targets if target.exists() or target.is_symlink())
    print("This removes:")
    for target in present:
        print(f"  {target}")
    print("  Second Wind's hooks and status line from Claude Code's settings")
    print("  its background refresh and, if it was on, the Grok Bot bridge")
    print(f"It keeps {state_dir()} (config and history) and every sign-in.")
    if args.dry_run:
        print("Dry run: nothing was changed.")
        return
    if not args.yes:
        if not sys.stdin.isatty():
            raise ValueError('pass --yes to uninstall without a terminal')
        if input('Remove Second Wind? [y/N] ').strip().lower() not in ('y', 'yes'):
            print('Nothing was changed.')
            return
    # The settings clean-up lives in the skill. The checkout's copy is used, so
    # this works even when the installed folder has already been deleted.
    setup = root / 'second-wind' / 'scripts' / 'setup.py'
    if (state_dir() / 'config.json').exists() and setup.is_file():
        subprocess.run([sys.executable, str(setup), '--uninstall'], check=False)
    for target in present:
        if target.is_symlink():
            target.unlink()
        elif removable(target, root):
            shutil.rmtree(target)
        else:
            print(f'Left alone, it is not a copy this installer made: {target}')
            continue
        print(f'Removed {target}')
    try:
        record_path().unlink()
    except OSError:
        pass
    print(f"Second Wind is removed. Delete {state_dir()} yourself if you also want the history gone.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--claude', action='store_true', help='install Second Wind in Claude Code')
    choice.add_argument('--codex', action='store_true', help='install Second Wind in Codex')
    choice.add_argument('--both', action='store_true', help='both harnesses')
    choice.add_argument('--uninstall', action='store_true',
                        help='remove the skill, its hooks and its background jobs')
    parser.add_argument('--link', action='store_true', help='link this checkout for development')
    parser.add_argument('--dry-run', action='store_true', help='say what would happen and change nothing')
    parser.add_argument('--yes', action='store_true', help='answer yes to the uninstall question')
    parser.add_argument('--quiet', action='store_true', help='print only what was installed')
    parser.add_argument('--claude-dir', type=Path, default=Path.home()/'.claude/skills')
    parser.add_argument('--codex-dir', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))/'skills')
    parser.add_argument('directory', nargs='?', type=Path, help='legacy skills directory for the selected harness')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if sys.version_info < (3, 9):
        parser.exit(2, 'install: Second Wind needs Python 3.9 or newer; this is %d.%d\n' % sys.version_info[:2])
    if args.uninstall:
        try:
            uninstall(args, root)
        except (ValueError, OSError) as exc:
            parser.exit(2, f'install: {exc}\n')
        return
    lines, found = look(args)
    chosen = args.claude or args.codex or args.both
    if not args.quiet:
        print(f'Second Wind {version(root)}')
        print('On this machine:')
        print('\n'.join(lines))
    if not chosen:
        if args.directory:
            args.claude = True
        else:
            print('Detected harnesses: ' + (', '.join(found) if found else 'none'))
            if sys.stdin.isatty():
                print('Install Second Wind in: 1 Claude Code, 2 Codex, 3 both')
                answer = input('Choose 1, 2 or 3: ').strip()
                selected = {'1': 'claude', '2': 'codex', '3': 'both'}.get(answer)
                if not selected: parser.error('choose 1, 2 or 3, or use an explicit flag')
                setattr(args, selected, True)
            elif len(found) == 1:
                setattr(args, found[0], True)
            else:
                parser.error('choose --claude, --codex or --both for a non-interactive install')
    if args.both and args.directory:
        parser.error('use --claude-dir and --codex-dir with --both')
    plan = []
    if not args.codex:
        plan.append(('claude', (args.directory or args.claude_dir).expanduser()))
    if args.codex or args.both:
        plan.append(('codex', (args.directory or args.codex_dir).expanduser()))
    if args.dry_run:
        for harness, parent in plan:
            target = parent / 'second-wind'
            print(f"Would {'link' if args.link else 'update' if target.exists() else 'install'} {target}")
        print('Dry run: nothing was changed.')
        return
    was_set_up = (state_dir() / 'config.json').exists()
    try:
        for harness, parent in plan:
            target = install(root/'second-wind', parent, args.link, harness)
            write_record(root, harness, target, args.link)
            if harness == 'codex':
                legacy = parent / 'codex-wind'
                if legacy.exists() or legacy.is_symlink():
                    backup_root = state_dir() / 'backups'
                    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                    backup = backup_root / ('codex-wind-install-' + str(time.time_ns()))
                    shutil.move(str(legacy), str(backup))
                    print(f'Archived former Codex Wind installation to {backup}')
    except (ValueError, OSError) as exc:
        parser.exit(2, f'install: {exc}\n')
    print('Restart the selected harness to load its skill. See README.md for account setup.')
    if args.quiet:
        return
    skill = plan[0][1] / 'second-wind'
    print()
    print('Next:')
    if was_set_up:
        print(f'  1. Restart {" and ".join("Claude Code" if h == "claude" else "Codex" for h, _ in plan)}.')
        print(f'  2. Check everything still works:  python3 "{skill}/scripts/health.py"')
    else:
        print(f'  1. Restart {" and ".join("Claude Code" if h == "claude" else "Codex" for h, _ in plan)}.')
        print('  2. Say: set up Second Wind. It finds your accounts and asks before it changes anything.')
        print(f'  3. Any time, see what needs you:  python3 "{skill}/scripts/health.py"')
    print(f'Later, to update:  python3 "{skill}/scripts/update.py"')


if __name__ == '__main__':
    main()
