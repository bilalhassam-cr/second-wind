#!/usr/bin/env python3
"""Summarise delegated work. Built for a weekly or monthly review.

    report.py [days]        default 7
    report.py [days] --share
    report.py [days] --share --with-details

The reverse check is the point: work handed out and never verified is the risk
this log exists to expose. A delegated job that half-worked still returns text
that reads like success, and its exit code is still zero.

`--share` writes a single file you can hand to someone else, or paste into an
issue, describing what this machine is running and what the last few
delegations did. Email addresses become `<account>` and the home directory
becomes `~`, so the file says what went wrong without saying who you are. By
default it also leaves out every directory name and every line a worker wrote:
it describes how the accounts were reached and how that went, not the work.
`--with-details` adds the failed replies' last lines and the exchange paths
back, for a report to yourself.
"""
import calendar
import glob
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import swlib  # noqa: E402

SHARE_PATH = os.path.join(os.path.expanduser("~"), "second-wind-test-report.md")
FAIL_TAIL_LINES = 40

# Deliberately loose. A pattern that only catches well-formed addresses leaves
# the malformed ones in, and it is the leftovers that get published.
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# A reset time printed as "(Africa/Johannesburg)" places the person. The hour is
# useful, the city is not.
TIMEZONE = re.compile(r"\((?:[A-Z][A-Za-z_]+/)+[A-Za-z_]+\)")


def redact(text):
    """Strip the two things that identify a person: their address and their home
    directory. Both appear in ordinary log output, so this runs over everything
    the share file carries, including output captured from other scripts."""
    if not text:
        return ""
    home = os.path.expanduser("~")
    if home and home != "/":
        text = text.replace(home, "~")
    text = TIMEZONE.sub("(local time)", text)
    return EMAIL.sub("<account>", text)


# ---------------------------------------------------------------- the ledger


def log_dir():
    """Where the ledgers and exchanges live. `log.dir` is the current key; the
    old top-level `log_dir` is still read so an older config keeps working."""
    cfg = swlib.load_config()
    for value in ((cfg.get("log") or {}).get("dir"), cfg.get("log_dir")):
        if value:
            return os.path.expanduser(value)
    return os.path.join(swlib.sw_home(), "log")


def parse_ts(ts):
    """The log stamps UTC, so convert as UTC. mktime treats the fields as local
    and time.timezone is the standard-time offset, so subtracting it was an hour
    out on every row logged during summer time, which quietly moved rows across
    the cutoff."""
    try:
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return 0


def load_rows(logdir, days):
    cutoff = time.time() - days * 86400
    rows = []
    for path in sorted(glob.glob(os.path.join(logdir, "*.jsonl"))):
        try:
            handle = open(path)
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if parse_ts(row.get("ts", "")) >= cutoff:
                    rows.append(row)
    return rows


def summary_lines(rows, days, details=True):
    """The review itself, as lines. Same text on the terminal and in the share
    file, so nobody has to reconcile two versions of the same finding. Without
    details, the directory names and exchange paths stay out: the exchange file
    is named after the directory it ran in, so the path names the project."""
    if not rows:
        return ["PASS second-wind: nothing delegated in %d days "
                "(the primary account carried all the work)" % days]

    by_worker = Counter(r.get("worker", "?") for r in rows)
    by_mode = Counter(r.get("mode", "?") for r in rows)
    fails = [r for r in rows if r.get("exit", 0) != 0]
    missing = [r for r in rows if not os.path.exists(r.get("exchange", ""))]
    secs = sum(r.get("duration_s", 0) for r in rows)

    parts = ", ".join("%d %s" % (n, w) for w, n in by_worker.most_common())
    modes = ", ".join("%d %s" % (n, m) for m, n in by_mode.most_common())
    status = "WARN" if (fails or missing) else "PASS"
    out = ["%s second-wind: %d calls in %d days (%s; %s), %dm of work moved off "
           "the primary account, %d failed"
           % (status, len(rows), days, parts, modes, secs // 60, len(fails))]

    if fails:
        out.append("")
        out.append("Do this: failed delegations to look at")
        for r in fails[-3:]:
            if details:
                out.append("  - %s %s exit=%s in %s"
                           % (r.get("ts", "?")[:16], r.get("worker", "?"),
                              r.get("exit"), os.path.basename(r.get("cwd", "?"))))
                out.append("    %s" % r.get("exchange", "?"))
            else:
                out.append("  - %s %s exit=%s, %s mode, %ss"
                           % (r.get("ts", "?")[:16], r.get("worker", "?"),
                              r.get("exit"), r.get("mode", "?"), r.get("duration_s", "?")))

    if missing:
        out.append("")
        out.append("WARN: %d ledger entries point at an exchange file that is gone"
                   % len(missing))

    capped = [r for r in rows if r.get("truncated") is True]
    if capped:
        out.append("")
        out.append("Note: %d exchange file(s) were capped on disk. The reply the "
                   "caller saw was complete; the logged copy names the gap."
                   % len(capped))

    big = [r for r in rows if r.get("reply_bytes", 0) > 3000 and r.get("exit", 0) == 0]
    if big:
        out.append("")
        out.append("Worth a look: %d substantial replies came back (over 3KB). "
                   "Confirm each finding landed in a memory file or a project file, "
                   "not just here. Neither worker's own memory is ever read by the "
                   "primary session." % len(big))
        for r in big[-3:]:
            out.append("  - %s %s %sb%s"
                       % (r.get("ts", "?")[:16], r.get("worker", "?"),
                          r.get("reply_bytes"),
                          " -> %s" % os.path.basename(r.get("exchange", ""))
                          if details else ""))

    thin = [r for r in rows if r.get("prompt_bytes", 0) > 4000
            and r.get("reply_bytes", 0) < 500 and r.get("exit", 0) == 0]
    if thin:
        out.append("")
        out.append("Worth a look: %d call(s) sent a large prompt and got a thin "
                   "reply. Delegating costs a prompt that has to carry context the "
                   "worker lacks; when that prompt is bigger than the answer, the "
                   "job was cheaper done on the primary account." % len(thin))
    return out


# ---------------------------------------------------------------- share file


def environment_lines(details=True):
    cfg = swlib.load_config()
    versions = swlib.client_versions()
    clients = ", ".join("%s %s" % (name, version or "not installed")
                        for name, version in sorted(versions.items()))
    roles = swlib.enabled_roles(cfg)
    lines = [
        "- OS: %s" % platform.platform(),
        "- Python: %s" % platform.python_version(),
        "- Clients: %s" % clients,
        "- Level: %s" % (cfg.get("level") or "not set in config"),
        "- Config version: %s" % (cfg.get("version") or "unknown"),
        "- Enabled workers: %s" % (", ".join(roles) if roles else "none"),
    ]
    if details:
        lines.append("- Log directory: %s" % swlib.tilde(log_dir()))
    return lines


def status_lines(details=True):
    """Every refresh-status file. In full with details, because one stale line
    explains most of the faults people report. Without them, the kind of status
    only: a FAILED line can quote the client's screen or an exception's path."""
    out = []
    for path in sorted(glob.glob(os.path.join(swlib.sw_home(),
                                              "refresh-status-*.txt"))):
        role = os.path.basename(path)[len("refresh-status-"):-len(".txt")]
        try:
            with open(path) as handle:
                text = handle.read().strip()
        except OSError as exc:
            out.append("- %s: unreadable (%s)" % (role, exc))
            continue
        age = swlib.short_age(time.time() - os.path.getmtime(path))
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            out.append("- %s: empty (%s ago)" % (role, age))
            continue
        if not details:
            out.append("- %s: %s (%s ago)" % (role, swlib.status_kind(message=lines[0]), age))
            continue
        out.append("- %s: %s (%s ago)" % (role, lines[0], age))
        for extra in lines[1:]:
            out.append("    %s" % extra)
    return out or ["- no status files; the readers have not run"]


def accounts_table(details=True):
    script = os.path.join(HERE, "setup.py")
    if not os.path.exists(script):
        return "setup.py is not next to report.py, so no table could be produced."
    try:
        done = subprocess.run([sys.executable, script, "--accounts"],
                              capture_output=True, text=True, timeout=120)
    except Exception as exc:
        return "The accounts table could not be produced (%s)." % exc
    text = (done.stdout or "").strip()
    if done.returncode != 0:
        if not details:
            # stderr from a failed run can carry a path; the exit code is enough.
            return "The accounts table could not be produced (exit %d)." % done.returncode
        text = "%s\n[exit %d]\n%s" % (text, done.returncode,
                                      (done.stderr or "").strip())
    return keep_columns(text) or "The accounts table came back empty."


def keep_columns(text):
    """Redact the accounts table without wrecking its columns. The table is
    padded to the width of the real account names, so swapping in a shorter
    placeholder shifts every column after it. Padding the placeholder back to
    the same width keeps the table readable, which is the point of including
    it."""
    def swap(match):
        placeholder = "<account>"
        pad = len(match.group(0)) - len(placeholder)
        # Only pad inside a column. An address in a sentence is followed by
        # punctuation, and padding that leaves a gap in the middle of a line.
        after = text[match.end():match.end() + 1]
        if pad > 0 and after == " ":
            return placeholder + " " * pad
        return placeholder
    return EMAIL.sub(swap, text)


def fail_tail(path, lines=FAIL_TAIL_LINES):
    """The end of an exchange, which is where a failure explains itself: the
    timeout notice, the last thing the worker tried, the error it printed. The
    reply is the section worth reading, so the tail is taken from there when the
    file has one, and from the whole file when it does not. Capped at 40 lines,
    because this goes in a file somebody else reads."""
    try:
        with open(path, errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        return "(exchange file unreadable: %s)" % exc
    marker = "\n## Reply\n"
    at = text.find(marker)
    body = text[at + len(marker):] if at >= 0 else text
    kept = body.splitlines()[-lines:]
    tail = "\n".join(kept).strip()
    return tail or "(no reply recorded)"


def field_note_lines():
    """The diary, summarised. Setup steps and notes in order, reader outcomes
    tallied per role, routes and handovers counted. Nothing here is copied from
    a prompt or a reply, because nothing of the kind is ever written to it."""
    rows = swlib.load_field_notes()
    if not rows:
        return ["Field notes are off, or nothing has been recorded. Turn them on "
                "with `setup.py --write ... --field-notes on`."]
    out = []
    steps = [r for r in rows if r.get("event") in ("setup", "write", "check", "note")]
    if steps:
        out += ["Setup and notes, in order:", ""]
        for r in steps:
            stamp = str(r.get("ts", "?"))[:16].replace("T", " ")
            kind = r.get("event")
            if kind == "setup":
                out.append("- %s  setup --%s  (%s)%s" % (
                    stamp, r.get("command"), " ".join(r.get("args") or []),
                    "" if r.get("result") in (0, None) else "  exit %s" % r.get("result")))
            elif kind == "write":
                out.append("- %s  wrote config: level %s, workers %s%s%s" % (
                    stamp, r.get("level"), ", ".join(r.get("workers") or []) or "none",
                    ", forced" if r.get("forced") else "",
                    "; changed: " + "; ".join(r.get("changed") or []) if r.get("changed") else ""))
            elif kind == "check":
                faults, warnings = r.get("faults") or [], r.get("warnings") or []
                out.append("- %s  check: %d fault(s), %d warning(s)" % (stamp, len(faults), len(warnings)))
                for line in faults:
                    out.append("    fault: %s" % line)
                for line in warnings:
                    out.append("    warning: %s" % line)
            elif kind == "note":
                out.append("- %s  NOTE: %s" % (stamp, r.get("text", "")))
        out.append("")
    refreshes = [r for r in rows if r.get("event") == "refresh"]
    if refreshes:
        tally = {}
        last = {}
        for r in refreshes:
            for role, kind in (r.get("outcomes") or {}).items():
                tally.setdefault(role, Counter())[kind] += 1
                last[role] = kind
        out += ["Reader outcomes over %d refresh run(s):" % len(refreshes), ""]
        for role in sorted(tally):
            counts = ", ".join("%d %s" % (n, k) for k, n in tally[role].most_common())
            out.append("- %s: %s; last %s" % (role, counts, last[role]))
        out.append("")
    routes = [r for r in rows if r.get("event") == "route"]
    handovers = [r for r in rows if r.get("event") == "handover"]
    if routes or handovers:
        out += ["Routing:", ""]
        if routes:
            by = Counter((r.get("source"), r.get("worker")) for r in routes)
            for (source, worker), n in by.most_common():
                out.append("- %d route(s) to %s from the %s" % (n, worker, source))
        if handovers:
            by = Counter(r.get("reason") for r in handovers)
            for reason, n in by.most_common():
                out.append("- %d handover(s) announced, reason: %s" % (n, reason))
        out.append("")
    return [line for line in out] or ["Nothing recorded yet."]


PRIVACY = [
    "## What this file contains, and what it does not",
    "",
    "In: the operating system and client versions, which workers are enabled and",
    "at what level, the kind of status each reader last reported, the accounts",
    "table with addresses replaced and timezones removed, how many delegations",
    "went to each worker in each mode and how many failed, and the field notes:",
    "setup commands with flags and fixed values only, which settings changed",
    "(names, not values), --check faults and warnings reduced to a kind, reader",
    "outcomes over time, routes and handovers by worker, and any lines you added",
    "with `setup.py --note`.",
    "",
    "Out: every prompt, every reply, every directory or project name, every label",
    "you gave an account, every email address, and your home directory path. The",
    "exchange logs stay on this machine. Your own notes are included as you wrote",
    "them, so read them before sending.",
]


def share_file(rows, days, logdir, path=SHARE_PATH, details=False):
    parts = ["# second-wind test report", ""]
    parts.append("Written %s. Home directory shown as ~ and email addresses "
                 "replaced with <account>.%s" % (
                     time.strftime("%Y-%m-%d %H:%M"),
                     " Reply tails and paths included (--with-details)." if details else ""))
    parts += [""] + PRIVACY
    parts += ["", "## Environment", ""] + environment_lines(details=details)
    parts += ["", "## Reader status", ""] + status_lines(details=details)
    parts += ["", "## Accounts", "", "```", accounts_table(details=details), "```"]
    parts += ["", "## Delegations, last %d days" % days, ""]
    if not os.path.isdir(logdir):
        parts.append("There is no log directory at %s." % swlib.tilde(logdir))
    else:
        parts += ["```"] + summary_lines(rows, days, details=details) + ["```"]

    parts += ["", "## Field notes", ""] + field_note_lines()

    fails = [r for r in rows if r.get("exit", 0) != 0]
    parts += ["", "## Failed exchanges", ""]
    if not fails:
        parts.append("None in this period.")
    elif not details:
        parts.append("%d failed; the workers' replies are not copied into this file. "
                     "Run report.py with --with-details for their last lines, and read "
                     "them before sending." % len(fails))
        fails = []
    for r in fails:
        parts.append("### %s %s, exit %s, %s mode"
                     % (r.get("ts", "?")[:16], r.get("worker", "?"),
                        r.get("exit"), r.get("mode", "?")))
        parts.append("")
        parts.append("Last %d lines of the reply:" % FAIL_TAIL_LINES)
        parts.append("")
        parts.append("```")
        parts.append(fail_tail(r.get("exchange", "")))
        parts.append("```")
        parts.append("")

    text = redact("\n".join(parts).rstrip() + "\n")
    swlib.write_text_atomic(path, text)
    return path


# ---------------------------------------------------------------- entry point


def main(argv):
    days, share, details = 7, False, False
    for arg in argv:
        if arg == "--share":
            share = True
        elif arg == "--with-details":
            details = True
        elif arg.isdigit() and int(arg) > 0:
            days = int(arg)
        else:
            sys.stderr.write("usage: report.py [days] [--share] [--with-details]\n")
            return 2

    logdir = log_dir()
    rows = load_rows(logdir, days) if os.path.isdir(logdir) else []

    if share:
        path = share_file(rows, days, logdir, details=details)
        print(path)
        return 0

    if not os.path.isdir(logdir):
        print("WARN second-wind: no log directory at %s" % logdir)
        return 0
    for line in summary_lines(rows, days):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
