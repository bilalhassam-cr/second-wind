#!/usr/bin/env python3
"""StopFailure hook, matcher ``rate_limit``: the turn ended because the account
ran out of allowance.

This is the only moment the tool learns a limit for certain rather than by
reading a percentage, so it does three things: asks for a fresh primary reading,
tells the user out of band, and leaves a note the prompt guard picks up on the
next prompt. Claude Code discards this hook's output, so everything here is a
side effect.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import swlib  # noqa: E402


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


def level(cfg):
    """Config version 2 has no level key. Infer it from the switch that used to
    carry the same meaning, so an installation from before the level existed
    still hands over."""
    named = cfg.get("level")
    if named in ("reviewer", "worker", "relief"):
        return named
    return "relief" if (cfg.get("failover") or {}).get("enabled") is True else "worker"


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
    # The matcher already narrows this to rate limits. Check anyway, in case the
    # hook was installed by hand without one.
    error = str(payload.get("error") or "")
    if error and error != "rate_limit":
        return 0

    cfg = swlib.load_config()
    if not cfg:
        return 0

    spawn_refresh("--force", "--only", "primary")
    swlib.notify("second-wind",
                 "This account hit its usage limit. The next prompt will offer "
                 "the handover.", "rate-limit-primary")

    if level(cfg) == "relief":
        try:
            swlib.write_text_atomic(
                os.path.join(swlib.sw_home(), "handover-pending"),
                "%d\n" % int(time.time()))
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)
