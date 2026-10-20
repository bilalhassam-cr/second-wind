#!/usr/bin/env python3
"""Install either harness skill, preserving the legacy install.sh interface."""
import argparse
import os
from pathlib import Path
import shutil
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
            if item.is_file() and item.suffix in ('.sh', '.py'):
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
    choice.add_argument('--claude', action='store_true', help='Second Wind only (default)')
    choice.add_argument('--codex', action='store_true', help='Codex Wind only')
    choice.add_argument('--both', action='store_true', help='both harnesses')
    parser.add_argument('--link', action='store_true', help='link this checkout for development')
    parser.add_argument('--claude-dir', type=Path, default=Path.home()/'.claude/skills')
    parser.add_argument('--codex-dir', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))/'skills')
    parser.add_argument('directory', nargs='?', type=Path, help='legacy skills directory for the selected harness')
    args = parser.parse_args()
    if args.both and args.directory:
        parser.error('use --claude-dir and --codex-dir with --both')
    root = Path(__file__).resolve().parent
    try:
        if not args.codex:
            install(root/'second-wind', (args.directory or args.claude_dir).expanduser(), args.link)
        if args.codex or args.both:
            install(root/'codex-wind', (args.directory or args.codex_dir).expanduser(), args.link)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'install: {exc}\n')
    print('Restart the selected harness to load its skill. See README.md for account setup.')


if __name__ == '__main__':
    main()
