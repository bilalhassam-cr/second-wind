#!/usr/bin/env python3
"""Notification hook, matchers ``quota_auto_resume_fired|stale|disabled``.

Claude Code can pause on a usage limit and pick the work up again by itself.
Those three notifications are the only record that it happened, so they are
logged here. When the work actually resumed, the cached percentage is wrong by
definition, so a forced primary reading follows.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import swlib  # noqa: E402

EVENTS = "events.jsonl"
# Roughly a thousand events. The log is a breadcrumb trail, not an archive, and
# a hook that appends for ever is a slow leak.
MAX_BYTES = 256 * 1024
KEEP_LINES = 500


def spawn_refresh(*args):
    """Start the refresh wrapper and forget about it. Detached with its stdio
    closed, so the hook can exit while the reader is still running."""
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "usage-refresh.sh")
    if not os.path.exists(script):
        return
    try:
        subprocess.Popen(["/bin/sh", script] + list(args),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True,
                         cwd="/")
    except Exception:
        pass


def log(record):
    path = os.path.join(swlib.sw_home(), EVENTS)
    try:
        os.makedirs(swlib.sw_home(), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
            with open(path) as handle:
                tail = handle.readlines()[-KEEP_LINES:]
            swlib.write_text_atomic(path, "".join(tail))
        with open(path, "a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        os.chmod(path, 0o600)
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

    kind = str(payload.get("notification_type") or "")
    log({"at": int(time.time()),
         "event": "Notification",
         "notification_type": kind,
         "message": str(payload.get("message") or "")[:500],
         "session_id": str(payload.get("session_id") or "")})

    if kind == "quota_auto_resume_fired":
        spawn_refresh("--force", "--only", "primary")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)
