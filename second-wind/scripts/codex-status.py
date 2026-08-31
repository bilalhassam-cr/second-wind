#!/usr/bin/env python3
"""Read Codex plan usage from its status panel without sending a prompt."""
import argparse
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import time


def clean(raw):
    return re.sub(
        rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[=>]",
        b"",
        raw,
    ).decode(errors="replace")


def atomic_note(path, message):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as fh:
        fh.write(message.rstrip() + "\n")
    os.replace(tmp, path)


def percentage_used(text, label):
    match = re.search(label + r".*?(\d+)%\s*left", text, re.S | re.I)
    return None if not match else 100 - int(match.group(1))


def reset_time(text, label):
    match = re.search(label + r".*?left\s*\(resets ([^)]+)\)", text, re.S | re.I)
    return match.group(1).strip() if match else None


def parse_panel(raw):
    text = re.sub(r"[ \t]{2,}", " ", clean(raw))
    account = re.search(r"Account:\s*([^\s(]+)", text, re.I)
    plan = re.search(r"Account:\s*[^\r\n(]*\(([^)]+)\)", text, re.I)
    credits = re.search(r"([\d,]+ of [\d,]+ credits used)", text, re.I)
    return {
        "worker": "codex",
        "account": account.group(1) if account else None,
        "plan": plan.group(1) if plan else None,
        "five_hour_pct": percentage_used(text, r"5h limit:"),
        "seven_day_pct": percentage_used(text, r"Weekly limit:"),
        "monthly_credit_pct": percentage_used(text, r"Monthly credit limit:"),
        "five_hour_resets": reset_time(text, r"5h limit:"),
        "seven_day_resets": reset_time(text, r"Weekly limit:"),
        "monthly_credit_resets": reset_time(text, r"Monthly credit limit:"),
        "credits_note": credits.group(1) if credits else None,
        "cached_at": int(time.time()),
    }


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


def send(fd, value):
    try:
        os.write(fd, value)
        return True
    except OSError:
        return False


def login_state():
    try:
        result = subprocess.run(
            ["codex", "login", "status"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = f"{result.stdout}\n{result.stderr}".lower()
    if "not logged in" in text or "login expired" in text:
        return False
    if result.returncode == 0 and "logged in" in text:
        return True
    return None


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("--status", required=True)
    parser.add_argument("--cwd", required=True)
    args = parser.parse_args()
    if not os.path.isdir(args.cwd):
        atomic_note(args.status, "FAILED: the trusted working directory no longer exists.")
        return 1
    logged_in = login_state()
    if logged_in is False:
        try:
            os.unlink(args.output)
        except FileNotFoundError:
            pass
        atomic_note(args.status, "LOGIN EXPIRED: sign in to the Codex CLI, then rerun --accounts.")
        return 1
    if logged_in is None:
        atomic_note(args.status, "FAILED: Codex login status could not be confirmed.")
        return 1

    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.chdir(args.cwd)
        os.execvp("codex", ["codex"])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 60, 220, 0, 0))

    buf = b""
    started = time.time()
    step = 0
    try:
        while time.time() - started < 75:
            ready, _, _ = select.select([fd], [], [], 1)
            if ready:
                try:
                    buf += os.read(fd, 200000)
                except OSError:
                    break
            elapsed = time.time() - started
            panel = "Weekly limit" in clean(buf) and "5h limit" in clean(buf)
            if panel and elapsed > 18:
                break
            if step == 0 and elapsed > 8:
                if not send(fd, b"\x1b"):
                    break
                step = 1
            elif step == 1 and elapsed > 9:
                if not send(fd, b"/status"):
                    break
                step = 2
            elif step == 2 and elapsed > 12:
                if not send(fd, b"\r"):
                    break
                step = 3
            elif step == 3 and elapsed > 28:
                if not send(fd, b"\x1b"):
                    break
                step = 4
            elif step == 4 and elapsed > 30:
                if not send(fd, b"/status"):
                    break
                step = 5
            elif step == 5 and elapsed > 33:
                if not send(fd, b"\r"):
                    break
                step = 6
    finally:
        stop_child(pid, fd)

    data = parse_panel(buf)
    if data["five_hour_pct"] is None and data["seven_day_pct"] is None:
        atomic_note(
            args.status,
            "FAILED: the Codex status panel did not appear after Escape and one retry. "
            "A startup modal is the likely cause. Open Codex once by hand, clear any prompt it shows, then try again.",
        )
        return 1
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    tmp = f"{args.output}.tmp.{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, args.output)
    atomic_note(args.status, "OK")
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
