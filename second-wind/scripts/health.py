#!/usr/bin/env python3
"""Is Second Wind itself healthy, and what should be done about it if not.

    health.py            what needs the user, what is only worth knowing, what is fine
    health.py --json     the same as data
    health.py --tidy     also remove our own surplus backups and stray temp files

Every reader fails quietly by design, so a fault could sit for weeks behind
"the last refresh failed". This reads what the readers and the timer left
behind and turns it into one line per fault with the thing to do about it:
which account to sign in to again and the command for it, which client to
update, when the tool itself is at fault.

Nothing here asks a vendor anything or reads a credential. It reads Second
Wind's own state files, and with the full report the timer's state. Exit 0 when
nothing needs the user, 1 when something does.
"""
import argparse
import calendar
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swlib  # noqa: E402

FIX, NOTE = "fix", "note"
# A plain failure is only worth the user's time once it has lasted this long.
PERSISTENT_SECONDS = 2 * 3600


SCRIPTS = os.path.dirname(os.path.abspath(__file__))
# What a crashed Python reader leaves as the last line of its error.
CRASH = re.compile(r"(?i:traceback|no module named)|\b[A-Z][A-Za-z]*(?:Error|Exception)\b\s*:")


def script(name, runner="python3"):
    """A command the user can paste: the real path of one of our scripts, not
    a variable only the skill's own instructions define."""
    path = os.path.join(SCRIPTS, name)
    if path.startswith(swlib.HOME + os.sep):
        path = "$HOME" + path[len(swlib.HOME):]
    return '%s "%s"' % (runner, path)


def failures_path():
    return os.path.join(swlib.sw_home(), "log", "refresh-failures.log")


def update_check_path():
    return os.path.join(swlib.sw_home(), "update-check.json")


def failure_streak(role, since):
    """(count, first epoch) of this role's logged failures after ``since``,
    which is when it last had a good reading. The log holds failures only, so
    everything after the last good reading is one unbroken run."""
    count, first = 0, None
    try:
        with open(failures_path()) as handle:
            lines = handle.readlines()[-4000:]
    except OSError:
        return 0, None
    for line in lines:
        parts = line.split(" ", 2)
        if len(parts) < 3 or parts[1] != role:
            continue
        try:
            stamp = calendar.timegm(time.strptime(parts[0], "%Y-%m-%dT%H:%M:%SZ"))
        except ValueError:
            continue
        if stamp > since:
            count += 1
            first = stamp if first is None else min(first, stamp)
    return count, first


def day(epoch):
    return time.strftime("%-d %b", time.localtime(epoch)) if epoch else "an unknown date"


def login_fix(role, cfg):
    """The command that signs this role in again, for its own profile."""
    profile = cfg.get(role) or {}
    account = swlib._known(profile.get("account"))
    who = " as %s" % account if account and "@" in account else ""
    if role in swlib.CLAUDE_ROLES:
        # The primary is read through the reader profile when there is one,
        # so that is the profile whose sign-in the reader found gone.
        reader = cfg.get("reader") or {}
        through = reader.get("config_dir") if role == "primary" and reader.get("enabled") else None
        folder = swlib.expand(through or profile.get("config_dir") or "~/.claude")
        default = os.path.realpath(folder) == os.path.realpath(os.path.join(swlib.HOME, ".claude"))
        command = "claude auth login" if default else \
            "CLAUDE_CONFIG_DIR=%s claude auth login" % swlib.tilde(folder)
        return ("Run `%s`%s. The sign-in completes as whichever account your browser "
                "already holds, so check the browser first." % (command, who))
    if role in swlib.CODEX_ROLES:
        home = swlib.codex_home(role, cfg)
        command = "CODEX_HOME=%s codex login" % swlib.tilde(home) if home else "codex login"
        return "Run `%s`%s." % (command, who)
    if role == "grok":
        return "Run `grok` in a terminal and sign in%s." % who
    if role == "cursor":
        return "Run `cursor-agent` in a terminal and sign in%s." % who
    return "Sign in to this account again."


def said(message):
    """A status line without its leading kind, cut to a sentence or two."""
    text = re.sub(r"^(?:FAILED|LOGIN EXPIRED|NO CLI SIGN-IN|TRUST PROMPT|PARSER MISMATCH):\s*",
                  "", (message or "").strip())
    return swlib.scrub(text)[:220]


def classify_failure(message):
    """(level, code, what to do) for a reader that said FAILED, by its reason."""
    low = (message or "").lower()
    if "press any key to log in" in low or "sign in" in low or "log in" in low:
        return FIX, "login", None
    if "rate limit" in low:
        return NOTE, "rate-limited", ("The vendor is limiting how often usage can be read. "
                                      "It clears by itself; nothing to do.")
    if CRASH.search(message or ""):
        return FIX, "reader-crashed", ("This is a fault in Second Wind, not in your account. "
                                       "Update it with `" + script("update.py") + "`, and "
                                       "report it if it is still there afterwards.")
    if "not on path" in low:
        return FIX, "client-missing", "Install that client, or put it on PATH, then run setup again."
    if "working directory" in low:
        return FIX, "workdir-missing", "Rerun your setup command; `setup.py --show` prints it."
    if "could not load" in low or "could not get fresh" in low or "unreachable" in low \
            or "enotfound" in low or "offline" in low or "timed out" in low:
        return NOTE, "unreachable", "The vendor's server did not answer. It is retried every refresh."
    return NOTE, "failed", None


def role_findings(role, cfg, now):
    label = swlib.role_label(role, cfg)
    out = []
    drift = swlib.identity_drift(role, cfg, now=now)
    if drift:
        out.append({"level": FIX, "code": "sign-in-changed", "who": label,
                    "what": "%s." % (drift[0].upper() + drift[1:]),
                    "fix": "Sign back in to the configured account, or accept the new one with "
                           "`" + script("setup.py") + " --repin %s`. Until then jobs to "
                           "it are refused." % role})
    if not swlib.reading_enabled(role, cfg):
        return out
    message = swlib.status(role)
    kind = swlib.status_kind(message=message)
    state = swlib.freshness(role, now=now, cfg=cfg)
    usage = swlib.load_usage(role)
    last_good = swlib.number(usage.get("cached_at")) or 0
    count, first = failure_streak(role, last_good)
    lasting = " (%d failed readings since %s)" % (count, day(first)) if count > 1 else ""
    if kind == "ok" or (kind == "none" and state in ("fresh", "stale")):
        if swlib.spent(role, usage, now=now, cfg=cfg):
            note = swlib.spent_reset_note(role, usage)
            out.append({"level": NOTE, "code": "spent", "who": label,
                        "what": "Out of allowance%s." % (", %s" % note if note else ""),
                        "fix": "Jobs sent to it are refused until it resets. Choose another account."})
        return out
    if kind == "login":
        out.append({"level": FIX, "code": "login", "who": label,
                    "what": "Signed out%s." % lasting, "fix": login_fix(role, cfg)})
    elif kind == "nologin":
        command = re.search(r"Run:\s*(.+)$", message or "")
        out.append({"level": FIX, "code": "login", "who": label,
                    "what": "No terminal sign-in for the profile its usage is read through.",
                    "fix": "Run `%s`." % command.group(1).strip() if command else login_fix(role, cfg)})
    elif kind == "trust":
        out.append({"level": FIX, "code": "trust", "who": label,
                    "what": "The client asked whether to trust the readers' folder, and a reader "
                            "never answers that%s." % lasting,
                    "fix": "Rerun your setup command, which trusts that folder in the client's "
                           "own settings; `setup.py --show` prints it."})
    elif kind == "parser":
        out.append({"level": FIX, "code": "parser", "who": label,
                    "what": "The client's usage panel changed and this version cannot read it%s." % lasting,
                    "fix": "Update Second Wind with `" + script("update.py") + "`."})
    elif kind == "failed":
        level, code, fix = classify_failure(message)
        if code == "login":
            out.append({"level": FIX, "code": "login", "who": label,
                        "what": "Signed out%s." % lasting, "fix": login_fix(role, cfg)})
        else:
            # A passing fault becomes the user's business once it has lasted.
            if level == NOTE and first and now - first >= PERSISTENT_SECONDS and code == "failed":
                level = FIX
            out.append({"level": level, "code": code, "who": label,
                        "what": "Usage could not be read%s: %s" % (lasting, said(message) or "no reason given"),
                        "fix": fix or "If it continues, run `" + script("setup.py") + " --check` "
                                      "for the full picture."})
    elif state in ("dead", "none"):
        if role == "primary":
            out.append({"level": NOTE, "code": "no-reading", "who": label,
                        "what": "No current reading.",
                        "fix": "It is written while a Claude Code session is open on this account."})
        else:
            out.append({"level": NOTE, "code": "no-reading", "who": label,
                        "what": "No current reading, and no reader has reported why.",
                        "fix": "Run `" + script("usage-refresh.sh", "sh") + " --force`, then look again."})
    return out


def antigravity_findings(cfg, now):
    out = []
    accounts = cfg.get("accounts") or {}
    for name in swlib.antigravity_accounts(cfg):
        label = str(accounts[name].get("label") or name)
        usage = swlib.load_antigravity_usage(name)
        error = usage.get("error")
        if not error:
            continue
        signed_out = "not signed in" in str(error).lower()
        out.append({"level": FIX if signed_out else NOTE,
                    "code": "login" if signed_out else "failed", "who": label,
                    "what": "Signed out." if signed_out else "Usage could not be read: %s"
                            % swlib.scrub(str(error))[:200],
                    # The reader's own sentence carries the command, with a
                    # HOME for a second account: its case is left alone.
                    "fix": (_sentence(swlib.scrub(str(error)).split("; ", 1)[-1])
                            if signed_out else "It is retried every hour.")})
    return out


def _sentence(text):
    text = text.strip()
    return (text[:1].upper() + text[1:] + ("" if text.endswith(".") else ".")) if text else ""


def tool_findings(cfg, now, deep):
    """Faults in Second Wind's own wiring rather than in an account."""
    out = []
    runtime = swlib.runtime_dir()
    if os.path.isdir(runtime):
        missing = [name for name in swlib.RUNTIME_FILES
                   if not os.path.exists(os.path.join(runtime, name))]
        if missing:
            out.append({"level": FIX, "code": "mirror-incomplete", "who": "Second Wind",
                        "what": "The scheduled refresh is missing %s, so it cannot run."
                                % ", ".join(missing),
                        "fix": "Run `" + script("usage-refresh.sh", "sh") + " --force`, which copies them."})
    try:
        with open(update_check_path()) as handle:
            check = json.load(handle)
        behind = int(check.get("behind") or 0)
    except Exception:
        behind = 0
    if behind > 0:
        out.append({"level": NOTE, "code": "update-available", "who": "Second Wind",
                    "what": "%d update%s available." % (behind, "" if behind == 1 else "s"),
                    "fix": "Run `" + script("update.py") + "`."})
    surplus = len(surplus_files(cfg))
    if surplus >= 20:
        out.append({"level": NOTE, "code": "tidy", "who": "Second Wind",
                    "what": "%d surplus backup and temporary files of its own are lying about." % surplus,
                    "fix": "Run `" + script("health.py") + " --tidy`."})
    if not deep:
        return out
    try:
        import setup as sw_setup
        if sys.platform == "darwin" and not sw_setup.in_temp_home() \
                and (cfg.get("refresh") or {}).get("launchd") \
                and any(swlib.reading_enabled(role, cfg) for role in swlib.enabled_roles(cfg)) \
                and sw_setup.launchd_loaded() is False:
            out.append({"level": FIX, "code": "timer-off", "who": "Second Wind",
                        "what": "The 15-minute usage refresh is not loaded, so readings only "
                                "update when a job runs.",
                        "fix": "Rerun your setup command; `setup.py --show` prints it."})
        installed = swlib.client_versions().get("codex")
        newer = swlib.newer_codex_clients(installed, cfg) if installed else []
        if newer:
            out.append({"level": NOTE, "code": "client-behind", "who": "Codex client",
                        "what": "%s is installed and %s has been seen on this machine. Newer "
                                "models are refused to an older client." % (installed, newer[0][0]),
                        "fix": "Run `%s`." % swlib.codex_update_command(newer[0][0])})
    except Exception:
        pass
    return out


def findings(cfg=None, now=None, deep=True):
    """Every fault, most pressing first. Never raises: the session brief calls it."""
    cfg = swlib.load_config() if cfg is None else cfg
    now = time.time() if now is None else now
    out = []
    if not cfg:
        return out
    for role in swlib.enabled_roles(cfg):
        try:
            out.extend(role_findings(role, cfg, now))
        except Exception:
            continue
    for part in (antigravity_findings, ):
        try:
            out.extend(part(cfg, now))
        except Exception:
            pass
    try:
        out.extend(tool_findings(cfg, now, deep))
    except Exception:
        pass
    out.sort(key=lambda item: item["level"] != FIX)
    return out


def healthy_labels(cfg, found):
    troubled = {item["who"] for item in found}
    labels = [swlib.role_label(role, cfg) for role in swlib.enabled_roles(cfg)]
    labels += [str((cfg.get("accounts") or {}).get(name, {}).get("label") or name)
               for name in swlib.antigravity_accounts(cfg)]
    return [label for label in labels if label not in troubled]


def report(cfg=None, now=None, deep=True):
    cfg = swlib.load_config() if cfg is None else cfg
    if not cfg:
        return ["Second Wind is not set up yet. Ask your assistant to set up Second Wind."], []
    found = findings(cfg, now, deep)
    fixes = [item for item in found if item["level"] == FIX]
    notes = [item for item in found if item["level"] == NOTE]
    if not found:
        lines = ["Second Wind is healthy. Nothing needs you."]
    else:
        lines = ["Second Wind health: %s." % ", ".join(part for part in (
            "%d thing%s need%s you" % (len(fixes), "" if len(fixes) == 1 else "s",
                                       "s" if len(fixes) == 1 else "") if fixes else "",
            "%d note%s" % (len(notes), "" if len(notes) == 1 else "s") if notes else "") if part)]
    for title, items in (("NEEDS YOU", fixes), ("WORTH KNOWING", notes)):
        if not items:
            continue
        lines += ["", title]
        for item in items:
            lines.append("  %s: %s" % (item["who"], item["what"]))
            lines.append("    Fix: %s" % item["fix"])
    fine = healthy_labels(cfg, found)
    if fine:
        lines += ["", "FINE: %s" % ", ".join(fine)]
    return lines, found


def brief(cfg=None, now=None):
    """At most two lines for the session brief: only what needs the user.

    Shown at every session start until it is fixed, like the row above it
    that says the same thing in three words. A reminder held back for some
    hours was tried first: the next session to start was as likely to be an
    unattended one as the user's own, and it used the reminder up."""
    try:
        fixes = [item for item in findings(cfg, now, deep=False) if item["level"] == FIX]
        return ["Needs you: %s %s Fix: %s" % (item["who"], item["what"][0].lower() + item["what"][1:],
                                              item["fix"]) for item in fixes[:2]]
    except Exception:
        return []


def surplus_files(cfg=None):
    """Our own leftovers: timestamped backups beyond the newest few, and temp
    files a killed process left in the state folder. Only names this tool
    writes, and never an original."""
    cfg = swlib.load_config() if cfg is None else cfg
    targets = []
    for role in swlib.CLAUDE_ROLES + ("reader",):
        folder = (cfg.get(role) or {}).get("config_dir")
        if not folder:
            continue
        folder = swlib.expand(folder)
        targets.append(os.path.join(folder, "settings.json"))
        default = os.path.realpath(folder) == os.path.realpath(os.path.join(swlib.HOME, ".claude"))
        targets.append(os.path.join(swlib.HOME, ".claude.json") if default
                       else os.path.join(folder, ".claude.json"))
    for role in swlib.CODEX_ROLES:
        if (cfg.get(role) or {}).get("enabled"):
            home = swlib.codex_home(role, cfg) or os.path.join(swlib.HOME, ".codex")
            targets.append(os.path.join(home, "config.toml"))
    stamp = re.compile(r"^\d{8}-\d{6}$")
    surplus = []
    for target in dict.fromkeys(targets):
        prefix = target + ".second-wind-backup-"
        copies = sorted(name for name in glob.glob(glob.escape(prefix) + "*")
                        if stamp.match(name[len(prefix):]))
        surplus += copies[:-swlib.BACKUPS_KEPT]
    cutoff = time.time() - 86400
    for pattern in ("*.tmp.[0-9]*", ".refresh-*.out", ".refresh-*.err", ".status-*.[0-9]*",
                    ".second-wind-*.tmp"):
        for path in glob.glob(os.path.join(swlib.sw_home(), pattern)):
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    surplus.append(path)
            except OSError:
                pass
    return surplus


def tidy(cfg=None):
    removed = 0
    for path in surplus_files(cfg):
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            pass
    return removed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="print the findings as JSON")
    ap.add_argument("--tidy", action="store_true",
                    help="remove surplus backups and stray temp files of our own first")
    ap.add_argument("--quick", action="store_true",
                    help="state files only: skip the timer and client-version checks")
    a = ap.parse_args(argv)
    cfg = swlib.load_config()
    if a.tidy and cfg:
        print("Removed %d surplus files. Originals and the newest %d backups of each "
              "file were kept." % (tidy(cfg), swlib.BACKUPS_KEPT))
    lines, found = report(cfg, deep=not a.quick)
    if a.json:
        print(json.dumps({"healthy": not any(item["level"] == FIX for item in found),
                          "findings": found}, indent=2))
    else:
        print("\n".join(lines))
    return 1 if any(item["level"] == FIX for item in found) else 0


if __name__ == "__main__":
    sys.exit(main())
