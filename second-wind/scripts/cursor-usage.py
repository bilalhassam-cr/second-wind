#!/usr/bin/env python3
"""Read Cursor Pro's monthly pools from its own /usage panel.

The interactive browser login is required for this panel. CURSOR_API_KEY can
authenticate headless delegated work, but it is not enough for usage reading.
"""
import argparse
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time


def clean(raw):
    return re.sub(
        rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[=>]",
        b"",
        raw,
    ).decode(errors="replace")


def stop_child(pid, fd):
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except OSError:
        pass


def read_panel(budget=60):
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.execvp("cursor-agent", ["cursor-agent"])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 55, 200, 0, 0))
    buf = b""
    started = time.time()
    trusted = False
    step = 0
    try:
        while time.time() - started < budget:
            ready, _, _ = select.select([fd], [], [], 1)
            if ready:
                try:
                    buf += os.read(fd, 200000)
                except OSError:
                    break
            text = clean(buf)
            elapsed = time.time() - started
            # This is a known Cursor workspace-trust prompt, not a blind attempt
            # to dismiss an unknown first-run dialog.
            if not trusted and "Trust this workspace" in text:
                os.write(fd, b"a")
                trusted = True
                time.sleep(3)
            elif not trusted and elapsed > 8:
                trusted = True
            if trusted:
                if step == 0 and elapsed > 12:
                    os.write(fd, b"/usage")
                    step = 1
                elif step == 1 and elapsed > 15:
                    os.write(fd, b"\r")
                    step = 2
                elif step == 2 and elapsed > 18:
                    # Cursor needs a second Enter to execute the palette item.
                    os.write(fd, b"\r")
                    step = 3
                elif step == 3 and "Monthly plan and on-demand" in text and elapsed > 24:
                    break
                elif step == 3 and elapsed > 45:
                    break
    finally:
        stop_child(pid, fd)
    return re.sub(r"[ \t]{2,}", " ", clean(buf))


def parse_panel(text):
    def pool(name):
        match = re.search(name + r"\s+(\d+)%\s*used", text)
        return int(match.group(1)) if match else None

    plan_match = re.search(r"Usage\s*.\s*(\w+)", text)
    reset_match = re.search(r"Resets\s+([A-Za-z]{3}\s+\d{1,2})", text)
    included = pool("Included")
    return {
        "worker": "cursor",
        "plan": plan_match.group(1) if plan_match else None,
        "five_hour_pct": None,
        "included_pct": included,
        "auto_pct": pool("Auto"),
        "api_pct": pool("API"),
        "on_demand": None if "On-demand limit unavailable" in text else "available",
        "resets": reset_match.group(1) if reset_match else None,
    }


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    args = parser.parse_args()
    data = parse_panel(read_panel())
    if data["included_pct"] is None and data["auto_pct"] is None:
        print(
            "cursor-usage: could not read the usage panel. The interactive "
            "login is required; an API key is not enough.",
            file=sys.stderr,
        )
        return 1
    data["cached_at"] = int(time.time())
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    tmp = f"{args.output}.tmp.{os.getpid()}"
    with open(tmp, "w") as handle:
        json.dump(data, handle, indent=2)
    os.replace(tmp, args.output)
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
