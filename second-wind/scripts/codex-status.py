#!/usr/bin/env python3
"""Read Codex plan usage from its own /status panel, without sending a prompt.

Codex needs patience rather than keystrokes. On an untrusted directory it opens
a trust modal, which this reader reports and never answers. On a trusted one it
spends ten to forty seconds starting MCP servers, and a /status typed during
that window is swallowed, so the reader waits for the composer to appear and
the screen to go quiet before it types anything.

  codex-status.py [--cwd DIR] [--out PATH] [--status PATH] [--budget 85]
                  [--dump PATH]
"""
import argparse
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ptyreader  # noqa: E402
import swlib  # noqa: E402

CLIENT = "codex"
# Codex paints this modal a word at a time and the cleaned text keeps a line
# break wherever the cursor moved, so every marker allows any whitespace
# between its words. \s+ matches a newline; a literal space does not.
PROMPT = r"Ask\s+Codex"
TRUST = r"Do\s+you\s+trust\s+the\s+contents\s+of\s+this\s+directory"
LOGIN = (r"Not\s+logged\s+in|Sign\s+in\s+with\s+ChatGPT|/login\s+to|"
         r"session\s+(?:has\s+)?expired")
STARTING = r"Starting\s+MCP\s+servers"
PANEL = r"Weekly\s+limit|5h\s+limit"
MENU = r"show\s+current\s+session\s+configuration"


def _limit(text, label):
    """(percentage used, reset string) for one limit line, or None when the
    panel has no such line. The panel prints what is left, so 100% left is
    nothing used, and only a missing line means unknown."""
    found = None
    for line in text.splitlines():
        if not re.search(label, line, re.I):
            continue
        left = re.search(r"(\d+)%\s*left", line)
        if not left:
            continue
        resets = re.search(r"resets\s+([^)]+)\)", line, re.I)
        found = (100 - int(left.group(1)),
                 resets.group(1).strip() if resets else None)
    return found


def parse_panel(text):
    """Read the /status box. Every field is optional except the limits, and the
    caller decides what a missing limit means."""
    # The panel is drawn in a box and the verticals belong to no value.
    text = re.sub(r"[│┃|]", " ", text)
    account = re.search(r"Account:\s+(\S+)(?:\s+\(([^)]+)\))?\s*$", text, re.M)
    version = re.search(r"OpenAI\s+Codex\s*\(v?([\d][\w.\-]*)\)", text)
    five = _limit(text, r"5h\s+limit")
    week = _limit(text, r"Weekly\s+limit")
    month = _limit(text, r"Monthly\s+credit\s+limit")
    credits = re.search(r"Credits:\s+(.+?)\s*$", text, re.M)
    extra = {}
    if month:
        extra["monthly_credit_pct"], extra["monthly_credit_resets"] = month
    if credits:
        extra["credits"] = credits.group(1).strip()
    return {
        "account": account.group(1) if account else None,
        "plan": account.group(2) if account and account.group(2) else None,
        "five_hour_pct": five[0] if five else None,
        "seven_day_pct": week[0] if week else None,
        "five_hour_resets": five[1] if five else None,
        "seven_day_resets": week[1] if week else None,
        "extra": extra,
        "client_version": version.group(1) if version else "",
    }


def login_state():
    """True, False, or None when the CLI will not say. Codex writes this to
    stderr, so both streams are read."""
    try:
        done = subprocess.run(["codex", "login", "status"], capture_output=True,
                              text=True, timeout=15)
    except Exception:
        return None
    text = ((done.stdout or "") + " " + (done.stderr or "")).lower()
    if "not logged in" in text or "login expired" in text:
        return False
    if done.returncode == 0 and "logged in" in text:
        return True
    return None


def read_screen(cwd, budget):
    """Drive the client. Returns (outcome, screen text)."""
    screen = ptyreader.Screen(["codex"], cwd=cwd)
    ends = time.time() + budget

    def left(cap):
        return min(cap, max(1.0, ends - time.time()))

    def type_when_clear(keys):
        """Send keys only when no dialog is on screen, and name the dialog
        when it refuses. Codex paints its composer for a moment before it
        paints the trust modal, so being ready once is not enough: the screen
        is checked again before every keystroke."""
        text = screen.text
        for name, pattern in (("trust", TRUST), ("login", LOGIN)):
            if re.search(pattern, text, re.I):
                return name
        screen.send(keys)
        return None

    try:
        screen.start()
        seen = screen.first_of({"trust": TRUST, "login": LOGIN, "ready": PROMPT},
                               timeout=left(40))
        if seen in ("trust", "login"):
            return seen, screen.text
        if seen is None:
            return "no panel", screen.text
        # The composer appears before the MCP servers finish and a slash
        # command typed in that window is dropped. Wait for both.
        screen.wait_for(present=[PROMPT], absent=[STARTING], quiet=1.2,
                        timeout=left(35))
        stop = type_when_clear(b"/status")
        if stop:
            return stop, screen.text
        if not screen.wait_for(present=[MENU], timeout=left(10)):
            return "no panel", screen.text
        stop = type_when_clear(b"\r")
        if stop:
            return stop, screen.text
        if not screen.wait_for(present=[PANEL], timeout=left(20)):
            # One retry, and only while the panel is still not on screen: the
            # first Enter can land on a completion list rather than the input.
            stop = type_when_clear(b"\r")
            if stop:
                return stop, screen.text
            if not screen.wait_for(present=[PANEL], timeout=left(20)):
                return "no panel", screen.text
        screen.wait_for(present=[PANEL], quiet=1.0, timeout=left(6))
        return "panel", screen.text
    finally:
        screen.close()


def note(path, message):
    swlib.write_text_atomic(path, message.rstrip() + "\n")
    print(message)


def main():
    os.umask(0o077)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cwd", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--status", default="")
    ap.add_argument("--budget", type=float, default=85)
    ap.add_argument("--dump", default="", help="write the cleaned panel text here")
    a = ap.parse_args()

    cfg = swlib.load_config()
    refresh = cfg.get("refresh") or {}
    cwd = swlib.expand(a.cwd or refresh.get("workdir")
                       or refresh.get("working_dir") or "~")
    out = a.out or swlib.usage_path(CLIENT)
    status = a.status or swlib.status_path(CLIENT)
    if not os.path.isdir(cwd):
        note(status, "FAILED: the readers' working directory %s does not exist. "
                     "Rerun setup.py --write." % swlib.tilde(cwd))
        return 1
    if login_state() is False:
        note(status, "LOGIN EXPIRED: run codex in a terminal and sign in again.")
        return 2

    outcome, text = read_screen(cwd, a.budget)
    if a.dump:
        swlib.write_text_atomic(a.dump, text)
    data = parse_panel(text)
    if outcome == "trust":
        note(status, "TRUST PROMPT: open codex once in %s and accept, or rerun "
                     "setup --write to pre-trust it" % swlib.tilde(cwd))
        return 3
    if outcome == "login":
        note(status, "LOGIN EXPIRED: run codex in a terminal and sign in again.")
        return 2
    if outcome == "no panel":
        note(status, "FAILED: status panel did not appear. Last of the screen: %s"
             % " ".join(text.strip()[-300:].split()))
        return 1
    if data["five_hour_pct"] is None and data["seven_day_pct"] is None:
        note(status, "PARSER MISMATCH: client %s, expected labels not found"
             % (data["client_version"] or "unknown"))
        return 1

    record = {
        "role": CLIENT, "worker": CLIENT,
        "account": data["account"] or (cfg.get(CLIENT) or {}).get("account") or None,
        "plan": data["plan"] or (cfg.get(CLIENT) or {}).get("plan") or None,
        "five_hour_pct": data["five_hour_pct"],
        "seven_day_pct": data["seven_day_pct"],
        "five_hour_resets": data["five_hour_resets"],
        "seven_day_resets": data["seven_day_resets"],
        "extra": data["extra"],
        "client_version": data["client_version"],
        "cached_at": int(time.time()),
    }
    swlib.write_json_atomic(out, record)
    note(status, "OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
