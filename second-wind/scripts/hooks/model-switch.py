#!/usr/bin/env python3
"""PostModelSwitch hook: a model change is a good moment to top up the cache.

Somebody switching model is usually reacting to what the allowance is doing, and
an automatic fallback means the session already ran into something. Either way
the next reading matters more than usual, so ask for one when the cache is not
fresh. This hook says nothing to the session.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import swlib  # noqa: E402


def spawn_refresh(*args):
    """Start the refresh wrapper and forget about it. Detached with its stdio
    closed, so the hook can exit while the readers are still running."""
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


def main():
    try:
        sys.stdin.read()
    except Exception:
        pass
    cfg = swlib.load_config()
    if not cfg:
        return 0
    if any(swlib.freshness(role, cfg=cfg) != "fresh"
           for role in swlib.enabled_roles(cfg)
           if swlib.reading_enabled(role, cfg)):
        spawn_refresh()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)
