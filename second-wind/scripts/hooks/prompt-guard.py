#!/usr/bin/env python3
"""UserPromptSubmit hook: when this account is running low, say so and hand the
next task to another one.

It fires at a task boundary and announces itself, rather than rerouting
silently. That distinction is the whole design: one person moving their own
work to their own second subscription between tasks is ordinary use, whereas
silent mid-request rotation is the shape that looks like defeating per-account
limits. It is also simply more useful to know which account did the work.

This hook never starts a reader. A prompt is the worst moment to spend a second
or two on a subprocess, and the session-start hook, the refresh timer and the
runner already keep the cache warm.

Switch off:  touch ~/.second-wind/no-failover   (or set failover.enabled false)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import swlib  # noqa: E402

MACHINERY = ("<system-reminder>", "<task-notification>", "<command-name>")

MANUAL_WORKERS = {
    "secondary": "the secondary Claude account",
    "codex": "Codex",
    "grok": "Grok Build",
    "cursor": "Cursor Agent",
    "both": "the secondary Claude account and Codex",
    "all": "every enabled worker",
}

WORKER_NAMES = {
    "secondary": "the secondary Claude account",
    "codex": "Codex",
    "grok": "Grok Build",
    "cursor": "Cursor Agent",
}

ROUTING = """

Before starting the task in this message, tell the user in one line that you are routing
the heavy work to %s, then do it: send the substantial pieces out through
second-wind rather than doing them here. Keep orchestrating, reconciling and deciding
on this account, since that is cheap and it holds the conversation.

Two things to respect. Hand over at this task boundary, never part-way through a job
already running here. And if the user tells you to keep the work on this account, do that
without arguing: they can see the same numbers you can."""


PENDING_MAX_AGE = 6 * 3600


def emit(message):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": message,
    }}))
    return 0


def clock(epoch):
    """An epoch as a local HH:MM, or an empty string."""
    try:
        return time.strftime("%H:%M", time.localtime(int(epoch)))
    except (TypeError, ValueError, OSError):
        return ""


def reset_text(usage):
    """When the 5-hour window comes back, in whatever form the reading has it.
    The epoch is preferred; a client that only printed a display string still
    gets to say something useful."""
    stamp = clock(usage.get("five_hour_resets_at"))
    if stamp:
        return " The 5-hour window resets at %s." % stamp
    shown = usage.get("five_hour_resets")
    if isinstance(shown, str) and shown.strip():
        return " The 5-hour window resets at %s." % shown.strip()
    return ""


def pending_age(path):
    """Seconds since the rate limit that wrote this note. The file holds an
    epoch; a hand-made or truncated one falls back to its mtime."""
    try:
        with open(path) as handle:
            stamped = int(handle.read().strip())
    except Exception:
        stamped = 0
    if stamped <= 0:
        try:
            stamped = int(os.path.getmtime(path))
        except OSError:
            return 0
    return int(time.time()) - stamped


def drop(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def worker_list(cfg):
    """The workers this machine can hand to, in the order the config lists."""
    names = []
    for role in ("secondary", "codex", "grok", "cursor"):
        if role in swlib.enabled_roles(cfg):
            name = WORKER_NAMES[role]
            if role == "secondary":
                account = (cfg.get("secondary") or {}).get("account")
                if account:
                    name += " (%s)" % account
            names.append(name)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", or ".join([", ".join(names[:-1]), names[-1]])


def secondary_note(cfg, five_threshold, now):
    """Never send work to an account that is just as spent. A reading up to a
    day old is good enough to raise the doubt."""
    if "secondary" not in swlib.enabled_roles(cfg):
        return ""
    age = swlib.cache_age("secondary", now=now)
    if age is not None and 0 <= age < 86400:
        spent = swlib.number(swlib.load_usage("secondary").get("five_hour_pct"))
        if spent is not None and spent >= five_threshold:
            return (" Note: the secondary account is also at %d%% of its 5-hour "
                    "window, so it may not have headroom either. Say so rather "
                    "than assuming it is fresh." % round(spent))
    return " Note: check the sorted accounts view before choosing a destination."


def main():
    cfg = swlib.load_config()
    if not cfg:
        return 0
    # Setup installs this hook into both profiles so uninstall stays symmetric.
    # Only the primary may act on it: the cache below is the primary's, and a
    # secondary session reading it would describe someone else's figures as its
    # own and could route work to itself.
    if not swlib.is_primary_session():
        return 0

    home = swlib.sw_home()
    if os.path.exists(os.path.join(home, "no-failover")):
        return 0
    if (cfg.get("failover") or {}).get("enabled") is False:
        return 0

    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    text = prompt if isinstance(prompt, str) else raw
    # Do not fire on the session's own machinery.
    if any(marker in text for marker in MACHINERY):
        return 0

    # A rate limit already stopped a turn on this account. Say so on the next
    # prompt whatever the cache looks like, then forget it: the reading behind
    # it may well be stale, and the fact of the limit is the stronger evidence.
    pending = os.path.join(home, "handover-pending")
    if os.path.exists(pending):
        if pending_age(pending) > PENDING_MAX_AGE:
            # Six hours on, the window it described has come and gone. Drop the
            # note rather than announce a limit that no longer exists.
            drop(pending)
        else:
            workers = worker_list(cfg)
            if not workers:
                # Nowhere to hand to. Leave the note where it is, so it still
                # fires if a worker is switched on before it expires.
                return 0
            drop(pending)
            return emit("[second-wind] This account hit its usage limit and the last "
                        "turn stopped there." + ROUTING % workers)

    # A mode file is an explicit task-boundary override, so it is handled before
    # any usage reading. It still honours the master switches above, and
    # unrecognised contents stay silent rather than routing work somewhere the
    # user did not name.
    manual = ""
    try:
        with open(os.path.join(home, "mode")) as handle:
            manual = "".join(handle.read().split())
    except Exception:
        manual = ""
    if manual in MANUAL_WORKERS:
        return emit(
            "[second-wind] Manual routing override '%s' is active.\n"
            "\n"
            "Before starting the task in this message, tell the user in one line that "
            "you are routing\n"
            "the heavy work to %s, then send the substantial pieces there through\n"
            "second-wind. Keep orchestrating, reconciling and deciding on this account.\n"
            "\n"
            "Hand over at this task boundary, never part-way through a job already "
            "running here. If\n"
            "the user tells you to keep the work on this account, do that without "
            "arguing. Remove\n"
            "%s to return to usage-based handover."
            % (manual, MANUAL_WORKERS[manual], swlib.tilde(os.path.join(home, "mode"))))

    # A failed or blocked refresh means the figures below describe nothing, and
    # a wrong percentage is worse than no percentage.
    if swlib.status_kind("primary") not in ("ok", "none"):
        return 0
    # A dead or missing reading is ignored: the window may already have reset.
    if swlib.freshness("primary", cfg=cfg) not in ("fresh", "stale"):
        return 0

    usage = swlib.load_usage("primary")
    five_threshold, week_threshold = swlib.thresholds(cfg)
    five = swlib.number(usage.get("five_hour_pct"))
    week = swlib.number(usage.get("seven_day_pct"))
    hits = []
    if five is not None and five >= five_threshold:
        hits.append("the 5-hour window is %d%% spent (threshold %d%%)"
                    % (round(five), five_threshold))
    if week is not None and week >= week_threshold:
        hits.append("the weekly window is %d%% spent (threshold %d%%)"
                    % (round(week), week_threshold))
    if not hits:
        return 0

    workers = worker_list(cfg)
    if not workers:
        return 0

    resets = reset_text(usage)
    # One notification per reset window. The body deliberately carries no
    # percentage, so a figure creeping from 91 to 93 does not read as news.
    body = "This account is over its handover threshold."
    if resets:
        body += resets
    window = 3600
    try:
        due = int(usage.get("five_hour_resets_at") or 0) - int(time.time())
        if due > 0:
            window = max(300, due)
    except (TypeError, ValueError):
        window = 3600
    swlib.notify("second-wind", body, "handover-primary", window_seconds=window)

    return emit("[second-wind] This account is running low: %s.%s%s"
                % (" and ".join(hits), resets,
                   secondary_note(cfg, five_threshold, None))
                + ROUTING % workers)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        # A hook that raises is a broken session. Silence beats a traceback.
        raise SystemExit(0)
