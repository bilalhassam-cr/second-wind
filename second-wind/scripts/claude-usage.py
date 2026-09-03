#!/usr/bin/env python3
"""Read one Claude profile's limits from its own /usage panel.

The reader opens the terminal UI in the configured working directory, waits for
the prompt box, runs /usage, parses the panel and exits. It never sends a model
prompt and it never answers a dialog: a trust prompt is reported, not accepted.

  claude-usage.py [--role primary|secondary] [--config-dir ~/.claude-usage]
                  [--cwd DIR] [--out PATH] [--status PATH] [--budget 60]
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

CLIENT = "claude"
# These clients paint a dialog a word at a time, and the cleaned text keeps a
# line break wherever the cursor moved, so every marker allows any whitespace
# between its words. \s+ matches a newline; a literal space does not.
PROMPT = r"for\s+shortcuts|Try\s+\"|Ask\s+Claude"
# Claude 2.1 asks the safety question; the older wording is kept for older
# clients. A trust dialog is reported, never answered.
TRUST = (r"Yes,\s+I\s+trust\s+this\s+folder|Quick\s+safety\s+check|"
         r"Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder")
LOGIN = (r"Login:\s*Expired|/login\s+to|please\s+(?:sign|log)\s+in|"
         r"OAuth\s+token\s+(?:has\s+)?expired|credentials\s+(?:have\s+)?expired")
USED = r"\d+%\s*used"
RIGHT = b"\x1b[C"
# Claude's own names for the two windows second-wind reports.
FIVE_HOUR = r"Current\s+session"
SEVEN_DAY = r"Current\s+week\s+\(all\s+models\)"


def parse_panel(text):
    """Pull the two windows out of the panel text, by label.

    The panel repaints while it scans local sessions, so the buffer holds
    several renders and the later ones can be half drawn. Read every render and
    keep the last one that carried a figure for that label.
    """
    data = {"five_hour_pct": None, "five_hour_resets": None,
            "seven_day_pct": None, "seven_day_resets": None,
            "plan": None, "client_version": ""}
    for key, label in (("five_hour", FIVE_HOUR), ("seven_day", SEVEN_DAY)):
        for hit in re.finditer(label, text):
            chunk = text[hit.end():hit.end() + 240]
            found = re.search(r"(\d+)%\s*used", chunk)
            if not found:
                continue
            resets = re.search(r"Resets\s+([^\n]{1,60})", chunk)
            data[key + "_pct"] = int(found.group(1))
            data[key + "_resets"] = resets.group(1).strip() if resets else None
    plan = re.search(r"·\s*(Claude\s+[A-Za-z]+)", text)
    if plan:
        data["plan"] = " ".join(plan.group(1).split())
    version = re.search(r"Claude\s+Code\s+v?(\d[\w.\-]*)", text)
    if version:
        data["client_version"] = version.group(1)
    return data


def profile_dir(role, cfg):
    """Which profile this role's usage is read through. A configured reader
    profile exists so a background reader never shares the desktop app's
    credential, and when it is on, the primary is read through it."""
    reader = cfg.get("reader") or {}
    if role == "primary" and reader.get("enabled") and reader.get("config_dir"):
        return reader["config_dir"]
    return (cfg.get(role) or {}).get("config_dir") or "~/.claude"


def read_screen(config_dir, cwd, budget):
    """Drive the client. Returns (outcome, screen text). Outcome is 'panel',
    'trust', 'login' or 'no panel'."""
    resolved = swlib.expand(config_dir)
    default_dir = resolved == os.path.join(swlib.HOME, ".claude")
    # Setting CLAUDE_CONFIG_DIR for the default profile breaks its Keychain
    # lookup, so the default profile is read with the variable unset.
    screen = ptyreader.Screen(
        ["claude", "--model", "haiku"], cwd=cwd,
        env_set={} if default_dir else {"CLAUDE_CONFIG_DIR": resolved},
        # A nested session inherits markers that change how the CLI behaves.
        # A key in the environment would bill the API instead of the plan
        # this reader is measuring, so those go too.
        env_drop=("CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID",
                  "CLAUDE_CODE_HOST_SESSION_ID", "CLAUDECODE",
                  "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
                  "ANTHROPIC_AUTH_TOKEN") +
                 (("CLAUDE_CONFIG_DIR",) if default_dir else ()))
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
        # Order matters: the trust dialog also shows a prompt-like line, so it
        # is tested first and wins.
        chance = left(30)
        if chance is None:
            return "budget: the prompt box", screen.text
        seen = screen.first_of({"trust": TRUST, "login": LOGIN, "ready": PROMPT},
                               timeout=chance)
        if seen in ("trust", "login"):
            return seen, screen.text
        if seen is None:
            return "no panel", screen.text
        stop = type_when_clear(b"/usage\r")
        if stop:
            return stop, screen.text
        # Only ever Right, only while no figure is on screen, five at most.
        for _ in range(5):
            chance = left(6)
            if chance is None:
                return "budget: the usage figures", screen.text
            if screen.wait_for(present=[USED], timeout=chance):
                break
            stop = type_when_clear(RIGHT)
            if stop:
                return stop, screen.text
        if not re.search(USED, screen.text):
            return "no panel", screen.text
        # The figures move while the panel scans local sessions; let it settle,
        # but a panel already on screen is not thrown away for want of one.
        chance = left(8)
        if chance:
            screen.wait_for(present=[USED], quiet=1.0, timeout=chance)
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
    ap.add_argument("--role", default="primary", choices=list(swlib.CLAUDE_ROLES))
    ap.add_argument("--config-dir", default="")
    ap.add_argument("--cwd", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--status", default="")
    ap.add_argument("--budget", type=float, default=60)
    ap.add_argument("--dump", default="", help="write the cleaned panel text here")
    a = ap.parse_args()

    cfg = swlib.load_config()
    config_dir = a.config_dir or profile_dir(a.role, cfg)
    refresh = cfg.get("refresh") or {}
    cwd = swlib.expand(a.cwd or refresh.get("workdir")
                       or refresh.get("working_dir") or "~")
    out = a.out or swlib.usage_path(a.role)
    status = a.status or swlib.status_path(a.role)
    if not os.path.isdir(swlib.expand(config_dir)):
        note(status, "FAILED: the %s profile directory %s does not exist."
             % (a.role, swlib.tilde(swlib.expand(config_dir))))
        return 1
    if not os.path.isdir(cwd):
        note(status, "FAILED: the readers' working directory %s does not exist. "
                     "Rerun setup.py --write." % swlib.tilde(cwd))
        return 1

    try:
        outcome, text = read_screen(config_dir, cwd, a.budget)
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
    version = data["client_version"] or ""
    if outcome.startswith("budget:"):
        note(status, "FAILED: budget exhausted before %s. Last of the screen: %s"
             % (outcome.split(": ", 1)[1], tail(text)))
        return 1
    if outcome == "trust":
        note(status, "TRUST PROMPT: open claude once in %s and accept, or rerun "
                     "setup --write to pre-trust it" % swlib.tilde(cwd))
        return 3
    if outcome == "login":
        note(status, "LOGIN EXPIRED: run claude in a terminal for %s and sign in "
                     "again." % swlib.tilde(swlib.expand(config_dir)))
        return 2
    if outcome == "no panel":
        note(status, "FAILED: usage panel did not appear. A profile that has "
                     "never been opened shows onboarding first, so run claude "
                     "in it once by hand. Last of the screen: %s"
             % tail(text))
        return 1
    if data["five_hour_pct"] is None or data["seven_day_pct"] is None:
        note(status, "PARSER MISMATCH: client %s, expected labels not found"
             % (version or "unknown"))
        return 1

    profile = cfg.get(a.role) or {}
    record = {
        "role": a.role, "worker": CLIENT,
        "account": profile.get("account") or None,
        "plan": data["plan"] or profile.get("plan") or None,
        "five_hour_pct": data["five_hour_pct"],
        "seven_day_pct": data["seven_day_pct"],
        "five_hour_resets": data["five_hour_resets"],
        "seven_day_resets": data["seven_day_resets"],
        "extra": {"config_dir": swlib.tilde(swlib.expand(config_dir))},
        "client_version": version,
        "cached_at": int(time.time()),
    }
    swlib.write_json_atomic(out, record)
    note(status, "OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
