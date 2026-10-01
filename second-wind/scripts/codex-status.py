#!/usr/bin/env python3
"""Read Codex plan usage from its own /status panel, without sending a prompt.

Codex needs patience rather than keystrokes. On an untrusted directory it opens
a trust modal, which this reader reports and never answers. On a trusted one it
spends ten to forty seconds starting MCP servers, and a /status typed during
that window is swallowed, so the reader waits for the composer to appear and
the screen to go quiet before it types anything. From 0.158 Codex draws full
screen, and the panel is read from the screen it shows: see panel_text.

  codex-status.py [--role codex|codex2] [--cwd DIR] [--out PATH]
                  [--status PATH] [--budget 85] [--dump PATH]

The role decides which sign-in is read: a role with its own config_dir is
driven under that CODEX_HOME, so two Codex accounts on one machine each get
their own reading and neither one is mistaken for the other.
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
# 0.152.1 asked "Do you trust the contents of this directory?". 0.159.2 and
# 0.160.0 ask "Trust this folder?", and only in a git folder, with "Trust and
# continue" already selected, so an Enter typed at it trusts the folder.
TRUST = (r"Do\s+you\s+trust\s+the\s+contents\s+of\s+this\s+directory|"
         r"Trust\s+this\s+folder\?")
LOGIN = (r"Not\s+logged\s+in|Sign\s+in\s+with\s+ChatGPT|/login\s+to|"
         r"session\s+(?:has\s+)?expired")
# A full-screen Codex marks its input line with "›" and shows the placeholder
# or the command just typed: "› Ask Codex to do anything", "› /status". A
# dialog replaces that line, so a key goes only while the line itself is on
# screen. A bare "/status" left in the history, the "› /status  show ..."
# row of the command list and a dialog's "› 1. ..." option all fail to match.
COMPOSER = re.compile("^\\s*›\\s*(?:Ask\\s+Codex\\b.*|/status)\\s*$",
                      re.M | re.I)
STARTING = r"Starting\s+MCP\s+servers"
# A Business or Plus panel prints a 5h and a weekly line. A Free workspace
# prints one monthly line and nothing else, and a reader that did not know that
# reported "no panel" at the exact moment a role had silently dropped to Free.
PANEL = r"Weekly\s+limit|5h\s+limit|Monthly\s+limit"
MENU = r"show\s+current\s+session\s+configuration"
# Codex fetches the limits lazily. A /status that lands before the fetch prints
# "Limits: refresh requested; run /status again shortly" in place of the
# windows, which is a reason to ask again, not a missing panel.
REFRESHING = r"refresh\s+requested"
ASKS = 4


# A panel prints the plan's own windows and, on some plans, windows belonging
# to one model. The model's are labelled two ways: the model's name in front of
# an ordinary label ("gpt-reserve Weekly limit:"), or a heading of its own
# ("GPT-5.3-Codex-Spark limit:") with plain labels beneath it. Read as the
# plan's own, either one replaces the figure that decides where work goes. The
# Pro Lite panel prints its heading last, and a reader that took the final
# Weekly line reported a quarter-spent week as untouched on both windows.
LIMIT = re.compile(r"^\s*(?:(\S.*?)\s+)?"
                   r"(5h|Weekly|Monthly\s+credit|Monthly)\s+limit:\s*(.*)$", re.I)
HEADING = re.compile(r"^\s*(\S.*?)\s+limit:\s*$")
# Every frame of the panel carries one of these above its windows, so a screen
# still holding part of an earlier paint starts each frame at the plan again
# rather than carrying a heading across.
FRAME = re.compile(r"Account:|OpenAI\s+Codex\s*\(")
# Codex prints the address and the plan, the address alone, the plan alone,
# "ChatGPT" when it knows neither, or a sentence for an API key. Run behind its
# background server, as 0.159.2 and 0.160.0 are, it prints the plan alone, and
# a reader that took the first word for the account reported "signed in as
# Pro". Only something shaped like an address is ever the account.
ACCOUNT = re.compile(r"Account:[ \t]+(\S.*?)[ \t]*$", re.M)
ADDRESS = re.compile(r"\S+@\S+")
# Codex has renamed its Pro plans twice, and a bare "Pro" has meant two plans.
# From codex-rs/tui/src/status/helpers.rs, and subscription.rs from 0.160.0:
#
#              up to 0.157    0.158.0 to 0.159.3    from 0.160.0
#   Pro Lite   Pro Lite       Pro                   Pro 100
#   Pro        Pro            Pro (More)            Pro 200
#   Pro Max    not offered    Pro (Max)             Pro 500
#
# The drift check compares the plan read with the plan pinned at setup, so a
# name taken as printed reported a sign-in change on every Pro Lite account
# the day its client updated. Each plan is kept under the first name Codex
# gave it, which is the name a config pinned before the renames already holds.
# Every other plan has kept its name throughout.
RENAMED = {"pro (more)": "Pro", "pro 100": "Pro Lite", "pro 200": "Pro",
           "pro 500": "Pro (Max)"}


def plan_name(printed, version):
    """The plan under one name whichever client printed it, or None when the
    name cannot be told apart: a bare "Pro" from a panel with no version."""
    key = " ".join((printed or "").split()).lower()
    if not key:
        return None
    if key in RENAMED:
        return RENAMED[key]
    if key == "pro":
        number = tuple(int(part) for part in re.findall(r"\d+", version or "")[:3])
        if not number:
            return None
        return "Pro Lite" if (0, 158) <= number < (0, 160) else "Pro"
    return printed


def _windows(text):
    """Every limit window in the panel, as {owner: {label: (used, resets)}},
    where the owner None is the plan's own and any other is a model's.

    The panel prints what is left, so 100% left is nothing used and only a
    missing line means unknown. Within one owner the last line wins, so a
    screen holding a half-painted earlier frame is read from the final one.
    Nothing above the first frame is read: on a full screen that is what is
    left of a panel scrolled off the top, and its heading may have gone."""
    found = {}
    owner, framed = None, False
    for line in text.splitlines():
        if FRAME.search(line):
            owner, framed = None, True
        if not framed:
            continue
        hit = LIMIT.match(line)
        if hit:
            left = re.search(r"(\d+)%\s*left", hit.group(3))
            if not left:
                continue
            resets = re.search(r"resets\s+([^)]+)\)", hit.group(3), re.I)
            name = " ".join(hit.group(1).split()) if hit.group(1) else owner
            label = " ".join(hit.group(2).split()).lower()
            found.setdefault(name, {})[label] = (
                100 - int(left.group(1)),
                resets.group(1).strip() if resets else None)
            continue
        head = HEADING.match(line)
        if head:
            owner = " ".join(head.group(1).split())
    return found


def _model_windows(windows, label):
    """One window per model, under the model's own name, for extra."""
    out = {}
    for name, got in windows.items():
        if name is None or label not in got:
            continue
        out[name] = {"pct": got[label][0], "resets": got[label][1]}
    return out


def parse_panel(text):
    """Read the /status box. Every field is optional except the limits, and the
    caller decides what a missing limit means."""
    # The panel is drawn in a box and the verticals belong to no value.
    text = re.sub(r"[│┃|]", " ", text)
    version = re.search(r"OpenAI\s+Codex\s*\(v?([\d][\w.\-]*)\)", text)
    version = version.group(1) if version else ""
    values = ACCOUNT.findall(text)
    value = values[-1] if values else ""
    address = ADDRESS.search(value)
    if address:
        # Whatever else is on the line, an address never reaches the plan.
        account = address.group(0)
        bracket = re.search(r"\((.+)\)$", value[address.end():])
        plan = bracket.group(1) if bracket else None
    elif value.lower() == "chatgpt" or value.lower().startswith("api key"):
        account = plan = None
    else:
        account, plan = None, value
    windows = _windows(text)
    # The plan's own windows, as against a model's. "own" and not "plan",
    # because a plan elsewhere in this file is Pro Lite or Business Premium.
    own = windows.get(None) or {}
    five, week = own.get("5h"), own.get("weekly")
    # "Monthly limit" and "Monthly credit limit" are different lines: the first
    # is a Free workspace's only window, the second is purchased credits.
    month, monthly = own.get("monthly credit"), own.get("monthly")
    credits = re.search(r"Credits:\s+(.+?)\s*$", text, re.M)
    extra = {}
    # A model's own week can be the fuller of the two and is worth having, but
    # never in the weekly column, where it would be read as the weekly figure.
    model_weeks = _model_windows(windows, "weekly")
    model_five = _model_windows(windows, "5h")
    if model_weeks:
        extra["model_weeks"] = model_weeks
    if model_five:
        extra["model_five_hours"] = model_five
    if month:
        extra["monthly_credit_pct"], extra["monthly_credit_resets"] = month
    if monthly:
        extra["monthly_pct"], extra["monthly_resets"] = monthly
    if credits:
        extra["credits"] = credits.group(1).strip()
    if plan:
        # The plan field holds one name per plan; this is what was printed.
        extra["plan_printed"] = plan
    return {
        "account": account,
        "plan": plan_name(plan, version),
        "five_hour_pct": five[0] if five else None,
        "seven_day_pct": week[0] if week else None,
        "five_hour_resets": five[1] if five else None,
        "seven_day_resets": week[1] if week else None,
        "extra": extra,
        "client_version": version,
    }


def has_limits(data):
    """Whether a parsed panel carries any window the reading can use."""
    return not (data["five_hour_pct"] is None and data["seven_day_pct"] is None
                and data["extra"].get("monthly_pct") is None)


def panel_text(screen):
    """The text a reading is taken from. A client drawing full screen is read
    from the screen it shows, because it sends only the cells that changed:
    read in the order painted, a row of the Codex 0.159.2 panel came in three
    pieces and the plan's week was lost. A client that prints its panel
    inline, as Codex did up to 0.157, is read from what it printed."""
    shown, full_screen = screen.display
    return shown if full_screen else screen.text


def role_env(home):
    """(env for subprocesses, env_set and env_drop for the PTY) for one role.

    A role with its own home gets CODEX_HOME set to it. The role on the default
    home gets the variable removed, whatever the shell had, or an inherited
    CODEX_HOME would point both the login check and the panel at another role's
    sign-in and file that reading under this role's name.
    """
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env_set, env_drop = {}, ["OPENAI_API_KEY"]
    if home:
        env["CODEX_HOME"] = home
        env_set["CODEX_HOME"] = home
    else:
        env.pop("CODEX_HOME", None)
        env_drop.append("CODEX_HOME")
    return env, env_set, env_drop


def login_state(env=None):
    """True, False, or None when the CLI will not say. Codex writes this to
    stderr, so both streams are read."""
    try:
        done = subprocess.run(["codex", "login", "status"], capture_output=True,
                              text=True, timeout=15, env=env)
    except Exception:
        return None
    text = ((done.stdout or "") + " " + (done.stderr or "")).lower()
    if "not logged in" in text or "login expired" in text:
        return False
    if done.returncode == 0 and "logged in" in text:
        return True
    return None


def read_screen(cwd, budget, env_set=None, env_drop=("OPENAI_API_KEY",)):
    """Drive the client. Returns (outcome, screen text)."""
    # A key left in the environment makes the CLI bill it rather than the
    # ChatGPT subscription this reader exists to measure. Codex prefers
    # OPENAI_API_KEY over the login whenever it is set, so it goes. The caller
    # adds CODEX_HOME to the drop list for the role on the default directory,
    # or a variable inherited from the shell would point the panel at another
    # role's sign-in and the reading would be filed under the wrong name.
    screen = ptyreader.Screen(["codex"], cwd=cwd, env_set=env_set,
                              env_drop=tuple(env_drop))
    ends = time.time() + budget

    def left(cap):
        """Seconds still allowed for one wait, or None once the budget has
        gone: a reader that keeps waiting past its budget overruns it a second
        at a time, so the caller stops and says which state it stopped in."""
        return ptyreader.budget_left(ends, cap) or None

    def type_when_clear(keys):
        """Send keys only when no dialog is on screen, and name the dialog
        when it refuses. Codex paints its composer for a moment before it
        paints the trust modal, so being ready once is not enough: the screen
        is checked again before every keystroke. A known dialog is looked for
        in both readings, because a full-screen client can leave its words in
        pieces in the painted text, and a full-screen client must be showing
        its input line as well, so a dialog nobody has seen yet gets no key."""
        shown, full_screen = screen.display
        for name, pattern in (("trust", TRUST), ("login", LOGIN)):
            if re.search(pattern, screen.text, re.I) or re.search(pattern, shown, re.I):
                return name
        if full_screen and not COMPOSER.search(shown):
            return "unknown dialog"
        screen.send(keys)
        return None

    def count(pattern):
        return len(re.findall(pattern, screen.text, re.I))

    def wait_new(patterns, before, timeout, usable=False):
        """The key of the first pattern printed more often than `before`
        counted, or None on the timeout. A second /status repaints text the
        first one left on screen, so presence alone proves nothing. With
        `usable`, a panel whose figures can now be read is a reply as well: a
        full-screen client never resends a letter already in its cell, so a
        label can arrive without ever being painted whole."""
        end = time.time() + timeout
        while True:
            screen.pump(0.4)
            for key, pattern in patterns.items():
                if count(pattern) > before.get(key, 0):
                    return key
            if usable and has_limits(parse_panel(panel_text(screen))):
                return "panel"
            if time.time() >= end:
                return None

    def ask_status():
        """Type /status and take it from the completion list. None once sent,
        otherwise the outcome to return."""
        for attempt in (1, 2):
            menus = {"menu": count(MENU)}
            stop = type_when_clear(b"/status")
            if stop:
                return stop
            chance = left(10)
            if chance is None:
                return "budget: the completion list"
            if wait_new({"menu": MENU}, menus, chance):
                break
            if attempt == 2:
                return "no panel"
            stop = type_when_clear(b"\x1b")
            if stop:
                return stop
            screen.wait_for(present=[PROMPT], quiet=2.0, timeout=left(8) or 1)
        return type_when_clear(b"\r")

    try:
        screen.start()
        chance = left(40)
        if chance is None:
            return "budget: the composer", panel_text(screen)
        seen = screen.first_of({"trust": TRUST, "login": LOGIN, "ready": PROMPT},
                               timeout=chance)
        if seen in ("trust", "login"):
            return seen, panel_text(screen)
        if seen is None:
            return "no panel", panel_text(screen)
        # The composer appears before the MCP servers finish and a slash
        # command typed in that window is dropped. Wait for both.
        chance = left(35)
        if chance is None:
            return "budget: the MCP servers", panel_text(screen)
        # A server that fails to start leaves "MCP startup incomplete" on
        # screen for good, so the gate is the screen going quiet, not the
        # startup line disappearing. One retry after Escape covers a swallowed
        # first attempt.
        screen.wait_for(present=[PROMPT], quiet=2.5, timeout=chance)
        replies = {"panel": PANEL, "refresh": REFRESHING}
        for ask in range(1, ASKS + 1):
            before = {key: count(p) for key, p in replies.items()}
            stop = ask_status()
            if stop:
                return stop, panel_text(screen)
            chance = left(20)
            if chance is None:
                return "budget: the status panel", panel_text(screen)
            got = wait_new(replies, before, chance, usable=True)
            if got is None:
                # One more Enter, and only while nothing has answered: the
                # first can land on a completion list rather than the input.
                stop = type_when_clear(b"\r")
                if stop:
                    return stop, panel_text(screen)
                chance = left(20)
                if chance is None:
                    return "budget: the status panel", panel_text(screen)
                got = wait_new(replies, before, chance, usable=True)
            if got is None:
                return "no panel", panel_text(screen)
            if got == "panel":
                # A panel already on screen is not thrown away for want of a
                # settle. The panel has answered, so the settle is silence alone.
                chance = left(6)
                if chance:
                    screen.wait_for(quiet=1.0, timeout=chance)
                if has_limits(parse_panel(panel_text(screen))) or ask == ASKS:
                    return "panel", panel_text(screen)
            # Codex has sent for the figures. Give the fetch a moment, then ask
            # again.
            if left(15) is None:
                return "budget: the status panel", panel_text(screen)
            screen.wait_for(quiet=4.0, timeout=left(6) or 1)
        # Every ask came back "refresh requested"; main says so by name.
        return "panel", panel_text(screen)
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
    ap.add_argument("--role", default="codex", choices=swlib.CODEX_ROLES)
    ap.add_argument("--cwd", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--status", default="")
    ap.add_argument("--budget", type=float, default=85)
    ap.add_argument("--dump", default="", help="write the cleaned panel text here")
    a = ap.parse_args()

    cfg = swlib.load_config()
    role = a.role
    refresh = cfg.get("refresh") or {}
    cwd = swlib.expand(a.cwd or refresh.get("workdir")
                       or refresh.get("working_dir") or "~")
    out = a.out or swlib.usage_path(role)
    status = a.status or swlib.status_path(role)
    # The same environment for the login check and the panel, so both look at
    # the one sign-in this role owns.
    home = swlib.codex_home(role, cfg)
    env, env_set, env_drop = role_env(home)
    if not os.path.isdir(cwd):
        note(status, "FAILED: the readers' working directory %s does not exist. "
                     "Rerun setup.py --write." % swlib.tilde(cwd))
        return 1
    if home and not os.path.isdir(home):
        note(status, "FAILED: %s does not exist. Create it and sign in with "
                     "CODEX_HOME=%s codex login." % (swlib.tilde(home), swlib.tilde(home)))
        return 1
    if login_state(env) is False:
        note(status, "LOGIN EXPIRED: run codex in a terminal%s and sign in again."
             % (" with CODEX_HOME=%s" % swlib.tilde(home) if home else ""))
        return 2

    try:
        outcome, text = read_screen(cwd, a.budget, env_set=env_set, env_drop=env_drop)
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
        note(status, "TRUST PROMPT: open codex once in %s and accept, or rerun "
                     "setup --write to pre-trust it" % swlib.tilde(cwd))
        return 3
    if outcome == "unknown dialog":
        note(status, "TRUST PROMPT: codex is showing a screen this reader does "
                     "not recognise, so it pressed nothing. Open codex in %s and "
                     "see what it asks. Last of the screen: %s"
             % (swlib.tilde(cwd), tail(text)))
        return 3
    if outcome == "login":
        note(status, "LOGIN EXPIRED: run codex in a terminal and sign in again.")
        return 2
    if outcome == "no panel":
        note(status, "FAILED: status panel did not appear. Last of the screen: %s"
             % tail(text))
        return 1
    if not has_limits(data):
        if re.search(REFRESHING, text, re.I):
            note(status, "FAILED: Codex was still fetching the limits after %d "
                         "asks. Last of the screen: %s" % (ASKS, tail(text)))
            return 1
        note(status, "PARSER MISMATCH: client %s, expected labels not found"
             % (data["client_version"] or "unknown"))
        return 1

    # Only what the panel said. Seeding a silent field from the config would
    # make the later comparison of reading against config a comparison of the
    # config with itself.
    record = {
        "role": role, "worker": CLIENT,
        "account": data["account"] or None,
        "plan": data["plan"] or None,
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
