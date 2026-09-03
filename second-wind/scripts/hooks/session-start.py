#!/usr/bin/env python3
"""SessionStart hook: put one honest usage brief in front of the session.

It runs on a new session, a resume and a fork, and stays out of the way on a
clear or a compact, where the session has already had its brief. When a reading
is missing or stale it asks the refresh wrapper for a fresh one in the
background and never waits for it, because a session must not be held up by a
usage panel.

Switch it off:  touch ~/.second-wind/no-brief
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import swlib  # noqa: E402

# Verbatim, and deliberately so. The wording is the contract between this hook
# and whatever renders the brief: what to show, what not to offer, and what to
# do when the same message already carries a task.
INSTRUCTION = (
    "Present these as the session brief before anything else. If a widget or "
    "inline HTML rendering tool is available in this session, render them as "
    "one compact card: one row per account, two small bars (5h, weekly), the "
    "age in a footnote, no other decoration. Otherwise print them as a short "
    "list. Do not offer a model or effort picker unless asked. Do not repeat "
    "this brief later in the session. If this message already contains a task, "
    "keep the brief to one line and start the task."
)

SOURCES = ("", "startup", "resume", "fork")


def refresh_script():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "usage-refresh.sh")


def spawn_refresh(*args):
    """Start the refresh wrapper and forget about it. Detached with its stdio
    closed, so the hook can exit while the readers are still running."""
    script = refresh_script()
    if not os.path.exists(script):
        return
    try:
        subprocess.Popen(["/bin/sh", script] + list(args),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True,
                         cwd="/")
    except Exception:
        pass


def main():
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    source = str(payload.get("source") or "")
    if source not in SOURCES:
        return 0
    if os.path.exists(os.path.join(swlib.sw_home(), "no-brief")):
        return 0
    cfg = swlib.load_config()
    if not cfg:
        return 0

    if any(swlib.freshness(role, cfg=cfg) != "fresh"
           for role in swlib.enabled_roles(cfg)
           if swlib.reading_enabled(role, cfg)):
        spawn_refresh()

    lines = swlib.brief_lines(cfg)
    if not lines:
        return 0
    # Where the next tasks are going, and how to change it. The route is read
    # for this session only, so a route armed in another window is not
    # announced here as though it applied.
    route = None
    try:
        route = swlib.active_route(payload.get("session_id"), cfg)
    except Exception:
        route = None
    if route:
        lines.append("Routing to %s is on for this session; /second-wind off "
                     "stops it." % route["label"])
    else:
        lines.append("Type /second-wind to pick where the next tasks run.")
    context = "\n".join(lines) + "\n\n" + INSTRUCTION
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context,
    }}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        # A hook that raises is a broken session. Silence beats a traceback.
        raise SystemExit(0)
