#!/usr/bin/env python3
"""Read Cursor's monthly pools from its own /usage panel.

Cursor reports monthly pools rather than a five hour or weekly window, so both
of those come back null and the pools go in `extra`. The panel needs the
interactive browser login: CURSOR_API_KEY can authenticate delegated work but
it does not produce a usage panel.

  cursor-usage.py [--cwd DIR] [--out PATH] [--status PATH] [--budget 70]
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

CLIENT = "cursor"
# Markers allow any whitespace between words: the cleaned text keeps a line
# break wherever the client moved the cursor mid-sentence.
PROMPT = r"Plan,\s+search,\s+build\s+anything|Tip:\s+Use"
TRUST = (r"Workspace\s+Trust\s+Required|Trust\s+this\s+workspace|"
         r"Do\s+you\s+trust\s+the\s+contents\s+of\s+this\s+directory")
LOGIN = (r"not\s+(?:signed|logged)\s+in|please\s+(?:sign|log)\s+in|"
         r"/login\s+to|session\s+(?:has\s+)?expired|Invalid\s+API\s+key")
MENU = r"Show\s+plan\s+and\s+on-demand\s+usage"
PANEL = r"Monthly\s+plan\s+and\s+on-demand\s+usage|\d+%\s*used"
POOLS = (("included_pct", r"Included"), ("auto_pct", r"Auto"),
         ("api_pct", r"API"))


def parse_panel(text):
    """Read the pool rows. Each row is `<name> <n>% used` on one line, so the
    rows are matched line by line and Auto never takes Included's figure."""
    data = {"plan": None, "on_demand": None, "resets": None,
            "client_version": ""}
    for key, _ in POOLS:
        data[key] = None
    for line in text.splitlines():
        for key, label in POOLS:
            found = re.match(r"\s*" + label + r"\s+(\d+)%\s*used", line)
            if found:
                data[key] = int(found.group(1))
        demand = re.match(r"\s*On-Demand\s+(\S+)", line)
        if demand:
            data["on_demand"] = demand.group(1).strip()
    plan = re.search(r"Usage\s*[•·]\s*([A-Za-z][\w+ ]{0,20}?)\s{2,}", text)
    if plan:
        data["plan"] = " ".join(plan.group(1).split()) or None
    resets = re.search(r"Resets\s+([A-Za-z]{3}\s+\d{1,2}|[^\n]{3,24})", text)
    if resets:
        data["resets"] = " ".join(resets.group(1).split())
    version = re.search(r"Cursor\s+Agent\s+v?([\w.\-]+)", text)
    if version:
        data["client_version"] = version.group(1)
    return data


def read_screen(cwd, budget):
    """Drive the client. Returns (outcome, screen text)."""
    screen = ptyreader.Screen(["cursor-agent"], cwd=cwd)
    ends = time.time() + budget

    def left(cap):
        return min(cap, max(1.0, ends - time.time()))

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
        # Trust first: the modal sits on top of a composer that looks ready.
        seen = screen.first_of({"trust": TRUST, "login": LOGIN, "ready": PROMPT},
                               timeout=left(30))
        if seen in ("trust", "login"):
            return seen, screen.text
        if seen is None:
            return "no panel", screen.text
        screen.wait_for(present=[PROMPT], quiet=1.0, timeout=left(15))
        stop = type_when_clear(b"/usage")
        if stop:
            return stop, screen.text
        if not screen.wait_for(present=[MENU], timeout=left(10)):
            return "no panel", screen.text
        stop = type_when_clear(b"\r")
        if stop:
            return stop, screen.text
        if not screen.wait_for(present=[PANEL], timeout=left(15)):
            # One retry, gated on the panel still being absent: an Enter can
            # land on the completion list rather than on the input.
            stop = type_when_clear(b"\r")
            if stop:
                return stop, screen.text
            if not screen.wait_for(present=[PANEL], timeout=left(15)):
                return "no panel", screen.text
        screen.wait_for(present=[r"\d+%\s*used"], quiet=1.0, timeout=left(8))
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
    ap.add_argument("--budget", type=float, default=70)
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

    outcome, text = read_screen(cwd, a.budget)
    if a.dump:
        swlib.write_text_atomic(a.dump, text)
    data = parse_panel(text)
    if outcome == "trust":
        note(status, "TRUST PROMPT: open cursor once in %s and accept, or rerun "
                     "setup --write to pre-trust it" % swlib.tilde(cwd))
        return 3
    if outcome == "login":
        note(status, "LOGIN EXPIRED: run cursor-agent in a terminal and sign in "
                     "again. An API key alone does not show usage.")
        return 2
    if outcome == "no panel":
        note(status, "FAILED: usage panel did not appear. Last of the screen: %s"
             % " ".join(text.strip()[-300:].split()))
        return 1
    if data["included_pct"] is None and data["auto_pct"] is None:
        note(status, "PARSER MISMATCH: client %s, expected labels not found"
             % (data["client_version"] or "unknown"))
        return 1

    record = {
        "role": CLIENT, "worker": CLIENT,
        "account": (cfg.get(CLIENT) or {}).get("account") or None,
        "plan": data["plan"] or (cfg.get(CLIENT) or {}).get("plan") or None,
        "five_hour_pct": None, "seven_day_pct": None,
        "five_hour_resets": None, "seven_day_resets": None,
        "extra": {"included_pct": data["included_pct"],
                  "auto_pct": data["auto_pct"],
                  "api_pct": data["api_pct"],
                  "on_demand": data["on_demand"],
                  "resets": data["resets"]},
        "client_version": data["client_version"],
        "cached_at": int(time.time()),
    }
    swlib.write_json_atomic(out, record)
    note(status, "OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
