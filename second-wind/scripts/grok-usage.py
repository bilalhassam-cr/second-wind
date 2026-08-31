#!/usr/bin/env python3
"""Read Grok Build's weekly limit from its own /usage panel.

The reader never sends a model prompt and deliberately removes XAI_API_KEY so
the consumer subscription cannot be replaced by paid developer API billing.
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


def read_panel(budget=55):
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.environ.pop("XAI_API_KEY", None)
        os.execvp("grok", ["grok"])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 55, 200, 0, 0))
    buf = b""
    started = time.time()
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
            if step == 0 and elapsed > 8:
                os.write(fd, b"/usage")
                step = 1
            elif step == 1 and elapsed > 11:
                os.write(fd, b"\r")
                step = 2
            elif step == 2 and "Weekly limit" in text and "%" in text and elapsed > 16:
                break
            elif step == 2 and elapsed > 30:
                break
    finally:
        stop_child(pid, fd)
    return re.sub(r"[ \t]{2,}", " ", clean(buf))


def parse_panel(text):
    plan_match = re.search(r"Weekly limit\s*\(([^)]*)\)", text)
    segment = text.split("Weekly limit", 1)[-1] if "Weekly limit" in text else ""
    percent_match = re.search(r"(\d+)\s*%", segment)
    # A spinner is painted immediately after the date, so the match is bounded
    # to characters that can actually form a date and time.
    reset_match = re.search(r"Resets:\s*([A-Za-z0-9 ,:]{3,32})", segment)
    return {
        "worker": "grok",
        "plan": plan_match.group(1).strip() if plan_match else None,
        "five_hour_pct": None,
        "seven_day_pct": int(percent_match.group(1)) if percent_match else None,
        "seven_day_resets": reset_match.group(1).strip() if reset_match else None,
    }


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    args = parser.parse_args()
    data = parse_panel(read_panel())
    if data["seven_day_pct"] is None:
        print("grok-usage: could not read the usage panel", file=sys.stderr)
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
