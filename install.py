#!/usr/bin/env python3
"""Install either harness skill, preserving the legacy install.sh interface."""
import argparse
import os
from pathlib import Path
import shutil
import sys
import time
import tempfile


def install(source, parent, link):
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
        exists = target.exists() or target.is_symlink()
        if exists:
            target.rename(backup)
        try:
            stage.rename(target)
        except OSError:
            if exists:
                backup.rename(target)
            raise
    print(f'{"Linked" if link else "Installed"} {target}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--claude', action='store_true', help='install Second Wind in Claude Code')
    choice.add_argument('--codex', action='store_true', help='install Second Wind in Codex')
    choice.add_argument('--both', action='store_true', help='both harnesses')
    parser.add_argument('--link', action='store_true', help='link this checkout for development')
    parser.add_argument('--claude-dir', type=Path, default=Path.home()/'.claude/skills')
    parser.add_argument('--codex-dir', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))/'skills')
    parser.add_argument('directory', nargs='?', type=Path, help='legacy skills directory for the selected harness')
    args = parser.parse_args()
    if not (args.claude or args.codex or args.both):
        if args.directory:
            args.claude = True
        else:
            found = []
            if shutil.which('claude') or args.claude_dir.parent.exists(): found.append('claude')
            if shutil.which('codex') or args.codex_dir.parent.exists(): found.append('codex')
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
    root = Path(__file__).resolve().parent
    try:
        if not args.codex:
            install(root/'second-wind', (args.directory or args.claude_dir).expanduser(), args.link)
        if args.codex or args.both:
            install(root/'second-wind', (args.directory or args.codex_dir).expanduser(), args.link)
            legacy = (args.directory or args.codex_dir).expanduser() / 'codex-wind'
            if legacy.exists() or legacy.is_symlink():
                backup_root = Path(os.environ.get('SW_HOME', str(Path.home()/'.second-wind'))) / 'backups'
                backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                backup = backup_root / ('codex-wind-install-' + str(time.time_ns()))
                shutil.move(str(legacy), str(backup))
                print(f'Archived former Codex Wind installation to {backup}')
    except (ValueError, OSError) as exc:
        parser.exit(2, f'install: {exc}\n')
    print('Restart the selected harness to load its skill. See README.md for account setup.')


if __name__ == '__main__':
    main()
