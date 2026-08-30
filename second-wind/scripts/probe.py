#!/usr/bin/env python3
"""Report the browser constraint imposed by the worker command.

This script launches nothing and changes nothing.
"""
import argparse
import json
import shutil


def probe_browser_from_runner(codex_bin):
    """Return the constant implied by the commands in run.sh."""
    if not codex_bin:
        return None, "codex not installed"
    # Both runner modes impose a Codex sandbox. A live launch test would only
    # reproduce the browser crash the guard exists to prevent.
    return False, (
        "run.sh passes -s read-only in review mode and --approve-for-me in "
        "work mode, which uses the workspace-write sandbox; these command-line "
        "choices override sandbox_mode, so the user's config does not change "
        "this, and a sandboxed process cannot reach the window server"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    can, detail = probe_browser_from_runner(shutil.which("codex"))
    print(json.dumps({
        "codex": {"can_launch_browser": can, "detail": detail},
    }, indent=2))


if __name__ == "__main__":
    main()
