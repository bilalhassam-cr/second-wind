#!/usr/bin/env python3
"""Check for a newer Second Wind and install it.

    update.py            show what is new, ask, then update every install
    update.py --check    only say whether an update exists
    update.py --yes      update without asking

It updates from the checkout the skill was installed from, with git and
nothing else: a fetch, then a fast-forward only. A checkout with local changes
or on a branch with no upstream is left alone and the reason is given. A copied
install is then reinstalled from the checkout, and the copy it replaces is kept
once under ~/.second-wind/backups. Sign-ins, config and history are not touched.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swlib  # noqa: E402

# Where a copied install with no checkout left gets its source from.
REPOSITORY = "https://github.com/bilalhassam-cr/second-wind"
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Stop(Exception):
    """A reason the update cannot go ahead, worded for the user."""


def record_path():
    return os.path.join(swlib.sw_home(), "install.json")


def check_path():
    return os.path.join(swlib.sw_home(), "update-check.json")


def read_record():
    try:
        with open(record_path()) as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def git(root, *args, timeout=60):
    """(exit code, stdout, stderr) for one git command in the checkout."""
    try:
        done = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                              timeout=timeout, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except FileNotFoundError:
        raise Stop("git is not installed, and updating needs it.")
    except subprocess.SubprocessError:
        return 124, "", "git timed out"
    return done.returncode, done.stdout.strip(), done.stderr.strip()


def toplevel(folder):
    if not folder or not os.path.isdir(folder):
        return ""
    code, out, _ = git(folder, "rev-parse", "--show-toplevel")
    return out if code == 0 and os.path.isfile(os.path.join(out, "install.py")) else ""


def find_source(record=None, clone=False):
    """The checkout to update from: the one this skill is linked to, the one
    the installer recorded, or a clone kept in the state folder for a copied
    install whose checkout has gone."""
    record = read_record() if record is None else record
    for folder in (os.path.realpath(SKILL_DIR), record.get("source") or "",
                   os.path.join(swlib.sw_home(), "source")):
        top = toplevel(folder)
        if top:
            return top
    if not clone:
        raise Stop("the checkout this was installed from is gone. Run update.py without "
                   "--check to fetch a fresh one into ~/.second-wind/source.")
    target = os.path.join(swlib.sw_home(), "source")
    url = record.get("url") or REPOSITORY
    # Whatever is there is not a checkout, or the loop above would have taken
    # it: the remains of a clone that was cut short. It is our own folder.
    shutil.rmtree(target, ignore_errors=True)
    try:
        done = subprocess.run(["git", "clone", "--quiet", url, target], capture_output=True,
                              text=True, timeout=300, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except FileNotFoundError:
        raise Stop("git is not installed, and updating needs it.")
    except subprocess.SubprocessError:
        shutil.rmtree(target, ignore_errors=True)
        raise Stop("fetching %s took too long and was stopped. Try again on a better connection." % url)
    if done.returncode:
        shutil.rmtree(target, ignore_errors=True)
        raise Stop("could not fetch %s: %s" % (url, done.stderr.strip()[-200:]))
    return target


def state(source, fetch=True):
    """What the checkout is behind by: ``{behind, subjects, upstream, version}``."""
    code, upstream, _ = git(source, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if code:
        code, branch, _ = git(source, "rev-parse", "--abbrev-ref", "HEAD")
        raise Stop("%s is on %s, which follows no remote branch, so there is nothing to "
                   "update from. Switch to main to update." % (swlib.tilde(source), branch or "a detached commit"))
    if fetch:
        code, _, err = git(source, "fetch", "--quiet", upstream.split("/", 1)[0], timeout=120)
        if code:
            raise Stop("could not reach the repository: %s" % (err[-200:] or "no reason given"))
    _, count, _ = git(source, "rev-list", "--count", "HEAD..@{u}")
    _, subjects, _ = git(source, "log", "--format=%s", "--max-count=12", "HEAD..@{u}")
    return {"behind": int(count or 0), "subjects": subjects.splitlines(), "upstream": upstream}


def save_check(behind, error=""):
    """Record the answer for the health report. A check that could not reach
    the repository says nothing about it, so it keeps the last known count:
    an update found yesterday is still there when today starts offline."""
    try:
        if behind is None:
            try:
                with open(check_path()) as handle:
                    behind = int(json.load(handle).get("behind") or 0)
            except Exception:
                behind = 0
        swlib.write_json_atomic(check_path(), {"checked_at": int(time.time()),
                                               "behind": behind, "error": error})
    except Exception:
        pass


def digest(skill):
    """What an install contains, as one hash: its scripts, SKILL.md and
    VERSION. Two installs with the same hash are the same version, whatever
    any record says about either."""
    found = hashlib.sha256()
    names = []
    for folder, _, files in os.walk(os.path.join(skill, "scripts")):
        if "__pycache__" in folder:
            continue
        names += [os.path.join(folder, name) for name in files if name.endswith((".py", ".sh"))]
    names += [os.path.join(skill, "SKILL.md"), os.path.join(skill, "VERSION")]
    for path in sorted(names):
        try:
            with open(path, "rb") as handle:
                found.update(os.path.relpath(path, skill).encode() + b"\0" + handle.read())
        except OSError:
            continue
    return found.hexdigest()


def known_installs(source, record):
    """Every install on this machine as ``{harness: {path, link}}``: what the
    installer recorded, and for an install older than the record, the default
    skill folders and this one."""
    installs = dict(record.get("installs") or {})
    codex_home = os.environ.get("CODEX_HOME") or os.path.join(swlib.HOME, ".codex")
    defaults = {"claude": os.path.join(swlib.HOME, ".claude", "skills", "second-wind"),
                "codex": os.path.join(codex_home, "skills", "second-wind")}
    recorded = {os.path.realpath(entry.get("path") or "") for entry in installs.values()}
    if os.path.realpath(SKILL_DIR) not in recorded | {os.path.realpath(path) for path in defaults.values()}:
        defaults["custom"] = SKILL_DIR  # a skills folder given to the installer by hand
    inside = os.path.realpath(os.path.join(source, "second-wind"))
    for harness, path in defaults.items():
        if harness in installs or not os.path.isfile(os.path.join(path, "SKILL.md")):
            continue
        installs[harness] = {"path": path,
                             "link": os.path.islink(path) or os.path.realpath(path) == inside}
    return installs


def stale_copies(source, record):
    """The copied installs whose contents differ from the checkout's. A copy
    falls behind when a reinstall was cut short, when the checkout was pulled
    by hand, or when the checkout is a fresh clone: none of which shows as
    the checkout being behind its remote."""
    current = digest(os.path.join(source, "second-wind"))
    return sorted(harness for harness, entry in known_installs(source, record).items()
                  if not entry.get("link") and not os.path.islink(entry.get("path") or "")
                  and os.path.isdir(entry.get("path") or "")
                  and digest(entry["path"]) != current)


def installed_version():
    try:
        with open(os.path.join(SKILL_DIR, "VERSION")) as handle:
            return handle.read().strip()
    except OSError:
        return "unknown"


def reinstall(source, record):
    """Reinstall every copied install from the updated checkout. A linked one
    is already the checkout. Returns the lines to print."""
    lines = []
    for harness, entry in sorted(known_installs(source, record).items()):
        path = entry.get("path") or ""
        if entry.get("link") or os.path.islink(path):
            lines.append("%s is linked to the checkout, so it is already up to date." % swlib.tilde(path))
            continue
        flag = "--codex" if harness == "codex" else "--claude"
        where = "--codex-dir" if harness == "codex" else "--claude-dir"
        done = subprocess.run([sys.executable, os.path.join(source, "install.py"), flag, "--quiet",
                               where, os.path.dirname(path)], capture_output=True, text=True)
        if done.returncode:
            raise Stop("the new version was fetched but %s could not be reinstalled: %s"
                       % (swlib.tilde(path), done.stderr.strip()[-200:]))
        lines.append((done.stdout.strip().splitlines() or ["Updated %s" % path])[0])
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="only say whether an update exists")
    ap.add_argument("--yes", action="store_true", help="update without asking")
    ap.add_argument("--quiet", action="store_true",
                    help="with --check: print nothing, only record the answer for the health report")
    a = ap.parse_args(argv)
    record = read_record()
    try:
        source = find_source(record, clone=not a.check)
        found = state(source)
    except Stop as stop:
        save_check(None, str(stop))
        if not a.quiet:
            print("second-wind: %s" % stop, file=sys.stderr)
        return 0 if a.quiet else 2
    stale = stale_copies(source, record)
    save_check(found["behind"] or len(stale))
    if a.quiet:
        return 0
    if not found["behind"] and not stale:
        print("Second Wind %s is up to date." % installed_version())
        return 0
    if found["behind"]:
        print("Second Wind %s: %d update%s available from %s." % (
            installed_version(), found["behind"], "" if found["behind"] == 1 else "s", found["upstream"]))
        for subject in found["subjects"]:
            print("  - %s" % subject)
        if found["behind"] > len(found["subjects"]):
            print("  and %d more" % (found["behind"] - len(found["subjects"])))
    if stale:
        print("The installed copy for %s is older than its checkout at %s and will be reinstalled."
              % (" and ".join(stale), swlib.tilde(source)))
    if a.check:
        print('Install with: python3 "%s"' % os.path.abspath(__file__))
        return 0
    try:
        code, dirty, _ = git(source, "status", "--porcelain", "--untracked-files=no")
        if dirty and found["behind"]:
            raise Stop("%s has changes that are not committed, so it was left alone. Commit or "
                       "discard them, then update again." % swlib.tilde(source))
        if not a.yes:
            if not sys.stdin.isatty():
                raise Stop("pass --yes to update without a terminal.")
            if input("Update now? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Nothing was changed.")
                return 0
        if found["behind"]:
            code, _, err = git(source, "merge", "--ff-only", "@{u}")
            if code:
                raise Stop("the checkout has commits of its own, so it cannot simply move forward: %s"
                           % (err[-200:] or "git refused"))
        for line in reinstall(source, record):
            print(line)
    except Stop as stop:
        print("second-wind: %s" % stop, file=sys.stderr)
        return 2
    save_check(len(stale_copies(source, record)))
    try:
        # The scheduled refresh runs a copy of the readers; bring it along.
        swlib.sync_runtime(os.path.join(source, "second-wind"))
    except Exception:
        pass
    print("Updated. Restart Claude Code or Codex to load it.")
    if os.path.exists(os.path.join(swlib.sw_home(), "bridge", "heartbeat.json")) and \
            os.path.exists(os.path.join(swlib.HOME, "Library", "LaunchAgents", "com.second-wind.bot-bridge.plist")):
        print('The Grok Bot bridge runs its own copy: refresh it with second_wind.py bridge start.')
    print('Then check it: python3 "%s"' % os.path.join(SKILL_DIR, "scripts", "health.py"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
