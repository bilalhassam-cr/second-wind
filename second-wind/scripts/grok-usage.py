#!/usr/bin/env python3
"""Read Grok Build's weekly limit from its own /usage panel.

Grok reports one window, a weekly one, so the five hour figure is always null.
The reader removes XAI_API_KEY from the child's environment: with that key set
the CLI can bill the developer API instead of the consumer subscription, which
is not what a usage reading is for.

  grok-usage.py [--cwd DIR] [--out PATH] [--status PATH] [--budget 60]
                [--dump PATH]
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ptyreader  # noqa: E402
import swlib  # noqa: E402

CLIENT = "grok"
# Markers allow any whitespace between words: the cleaned text keeps a line
# break wherever the client moved the cursor mid-sentence.
PROMPT = r"Enter:send|Ctrl\+x:shortcuts|Resume\s+session"
# Grok 1.0.13 shows no directory trust dialog. These are the wordings its
# siblings use, so an added one is reported rather than silently answered.
TRUST = (r"Do\s+you\s+trust|Trust\s+this\s+(?:workspace|folder|directory)|"
         r"Workspace\s+Trust\s+Required")
LOGIN = (r"/login\s+to|please\s+(?:sign|log)\s+in|not\s+(?:signed|logged)\s+in|"
         r"session\s+(?:has\s+)?expired|Invalid\s+API\s+key")
MENU = r"View\s+usage"
PANEL = r"Weekly\s+limit"


def parse_panel(text):
    """Read the Usage limit tab. The plan is in brackets after the label and
    the figure is the first percentage painted under it."""
    data = {"plan": None, "seven_day_pct": None, "seven_day_resets": None,
            "client_version": ""}
    plan = re.search(PANEL + r"\s*\(([^)]*)\)", text)
    if plan:
        data["plan"] = " ".join(plan.group(1).split()) or None
    for hit in re.finditer(PANEL, text):
        chunk = text[hit.end():hit.end() + 300]
        found = re.search(r"(\d+)\s*%", chunk)
        if not found:
            continue
        resets = re.search(r"Resets:?\s+([A-Za-z0-9 ,:]{3,32})", chunk)
        data["seven_day_pct"] = int(found.group(1))
        data["seven_day_resets"] = resets.group(1).strip() if resets else None
    version = re.search(r"Grok\s+Build\s+v?(\d[\w.\-]*)", text)
    if version:
        data["client_version"] = version.group(1)
    return data


def read_screen(cwd, budget):
    """Drive the client. Returns (outcome, screen text)."""
    screen = ptyreader.Screen(["grok"], cwd=cwd, env_drop=("XAI_API_KEY",))
    ends = time.time() + budget

    def left(cap):
        """Seconds still allowed for one wait, or None once the budget has
        gone: a reader that keeps waiting past its budget overruns it a second
        at a time, so the caller stops and says which state it stopped in."""
        return ptyreader.budget_left(ends, cap) or None

    def type_when_clear(keys):
        """Send keys only when no dialog is on screen, and name the dialog
        when it refuses. A client can paint its composer before it paints a
        trust modal, so being ready once is not enough: the screen is checked
        again before every keystroke."""
        text = screen.text
        for name, pattern in (("trust", TRUST), ("login", LOGIN)):
            if re.search(pattern, text, re.I):
                return name
        screen.send(keys)
        return None

    try:
        screen.start()
        # Trust first: a dialog can sit on top of a composer that looks ready.
        chance = left(30)
        if chance is None:
            return "budget: the composer", screen.text
        seen = screen.first_of({"trust": TRUST, "login": LOGIN, "ready": PROMPT},
                               timeout=chance)
        if seen in ("trust", "login"):
            return seen, screen.text
        if seen is None:
            return "no panel", screen.text
        # Grok animates a spinner for as long as it is open, so a quiet screen
        # is no signal here. The suggestion list is: it only appears once the
        # composer has the text, so a missing one means retype, not wait.
        stop = type_when_clear(b"/usage")
        if stop:
            return stop, screen.text
        chance = left(10)
        if chance is None:
            return "budget: the suggestion list", screen.text
        if not screen.wait_for(present=[MENU], timeout=chance):
            stop = type_when_clear(b"/usage")
            if stop:
                return stop, screen.text
            chance = left(10)
            if chance is None:
                return "budget: the suggestion list", screen.text
            if not screen.wait_for(present=[MENU], timeout=chance):
                return "no panel", screen.text
        stop = type_when_clear(b"\r")
        if stop:
            return stop, screen.text
        chance = left(20)
        if chance is None:
            return "budget: the usage panel", screen.text
        if not screen.wait_for(present=[PANEL], timeout=chance):
            return "no panel", screen.text
        # A panel already on screen is not thrown away for want of a settle.
        chance = left(3)
        if chance:
            screen.wait_for(present=[PANEL], quiet=1.0, timeout=chance)
        return "panel", screen.text
    finally:
        screen.close()


def tail(text):
    """The last of the screen on one line, for a status that has to explain
    itself in a sentence."""
    return " ".join(text.strip()[-300:].split())


def note(path, message):
    swlib.write_text_atomic(path, message.rstrip() + "\n")
    print(message)


def main():
    os.umask(0o077)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cwd", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--status", default="")
    ap.add_argument("--budget", type=float, default=60)
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

    try:
        outcome, text = read_screen(cwd, a.budget)
    except Exception as exc:
        # A PTY that cannot be allocated, or a client that cannot be executed,
        # must still leave a status behind. A traceback tells the refresh
        # nothing and leaves the account with no reading and no reason for it.
        note(status, "FAILED: %s: %s"
             % (type(exc).__name__, (str(exc).splitlines() or [""])[0]))
        return 1
    if a.dump:
        swlib.write_text_atomic(a.dump, text)
    data = parse_panel(text)
    if outcome.startswith("budget:"):
        note(status, "FAILED: budget exhausted before %s. Last of the screen: %s"
             % (outcome.split(": ", 1)[1], tail(text)))
        return 1
    if outcome == "trust":
        note(status, "TRUST PROMPT: open grok once in %s and accept, or rerun "
                     "setup --write to pre-trust it" % swlib.tilde(cwd))
        return 3
    if outcome == "login":
        note(status, "LOGIN EXPIRED: run grok in a terminal and sign in again.")
        return 2
    if outcome == "no panel":
        note(status, "FAILED: usage panel did not appear. Last of the screen: %s"
             % tail(text))
        return 1
    if data["seven_day_pct"] is None:
        note(status, "PARSER MISMATCH: client %s, expected labels not found"
             % (data["client_version"] or "unknown"))
        return 1

    record = {
        "role": CLIENT, "worker": CLIENT,
        "account": (cfg.get(CLIENT) or {}).get("account") or None,
        "plan": data["plan"] or (cfg.get(CLIENT) or {}).get("plan") or None,
        "five_hour_pct": None,
        "seven_day_pct": data["seven_day_pct"],
        "five_hour_resets": None,
        "seven_day_resets": data["seven_day_resets"],
        "extra": {},
        "client_version": data["client_version"],
        "cached_at": int(time.time()),
    }
    swlib.write_json_atomic(out, record)
    note(status, "OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
