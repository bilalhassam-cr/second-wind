#!/usr/bin/env python3
"""Read one Claude profile's limits from its own /usage panel.

The reader opens the terminal UI, runs the local usage command, parses the
panel, and exits. It never sends a model prompt.
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


RIGHT = b"\x1b[C"


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


def read_panel(config_dir, budget=60):
    env_dir = os.path.expanduser(config_dir) if config_dir else None
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        if env_dir:
            os.environ["CLAUDE_CONFIG_DIR"] = env_dir
        else:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        # Parent session markers change the behaviour of a nested Claude CLI.
        for key in (
            "CLAUDE_CODE_CHILD_SESSION",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_HOST_SESSION_ID",
            "CLAUDECODE",
        ):
            os.environ.pop(key, None)
        os.execvp("claude", ["claude", "--model", "haiku"])

    # The panel will not render wide enough to parse without a real PTY size.
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 55, 200, 0, 0))
    buf = b""
    started = time.time()
    step = 0
    presses = 0
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
            if step == 0 and elapsed > 7:
                os.write(fd, b"/usage\r")
                step = 1
            elif step == 1 and elapsed > 11:
                # /usage opens a tabbed dialog and may not land on Usage.
                if re.search(r"\d+%\s*used", text):
                    step = 2
                elif presses < 5:
                    os.write(fd, RIGHT)
                    presses += 1
                    time.sleep(2)
                else:
                    step = 2
            elif step == 2 and elapsed > 45:
                break
    finally:
        stop_child(pid, fd)
    return re.sub(r"[ \t]{2,}", " ", clean(buf))


def parse_panel(text):
    """Match each used percentage with the reset line that follows it."""
    pairs = re.findall(r"(\d+)%\s*used(?:.*?Resets\s*([^\n]{0,40}))?", text, re.S)
    data = {}
    if pairs:
        data["five_hour_pct"] = int(pairs[0][0])
        data["five_hour_resets"] = (pairs[0][1] or "").strip() or None
    if len(pairs) >= 2:
        data["seven_day_pct"] = int(pairs[1][0])
        data["seven_day_resets"] = (pairs[1][1] or "").strip() or None
    if re.search(r"Login:\s*Expired", text):
        data["login_expired"] = True
    return data


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("--config-dir", default="")
    args = parser.parse_args()

    data = parse_panel(read_panel(args.config_dir))
    if data.get("login_expired"):
        print("claude-usage: this profile's terminal login has expired", file=sys.stderr)
        return 2
    if "five_hour_pct" not in data:
        print(
            "claude-usage: could not read the usage panel. Open a brand-new "
            "profile once by hand if a first-run dialog is still present.",
            file=sys.stderr,
        )
        return 1
    data.update({
        "worker": "claude",
        "config_dir": args.config_dir or "~/.claude",
        "cached_at": int(time.time()),
    })
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    tmp = f"{args.output}.tmp.{os.getpid()}"
    with open(tmp, "w") as handle:
        json.dump(data, handle, indent=2)
    os.replace(tmp, args.output)
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
