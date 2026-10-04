#!/usr/bin/env python3
"""The usage panel as data, for the usage pop-up mod to draw.

Same readings and the same rules as `swlib.brief_card`, so the pop-up and the
text panel can never disagree; only the drawing differs. Nothing is spawned.

  card_json.py [--session ID]
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swlib  # noqa: E402


HOUR = 3600
_CLOCK = re.compile(r"^(\d{2}):(\d{2}) ([A-Z][a-z]{2}) (\d{1,2})$")
_DATE = re.compile(r"^([A-Z][a-z]{2}) (\d{1,2})$")
_TIME_ONLY = re.compile(r"\b(\d{1,2})(?::([0-5]\d))?\s*(am|pm)?\b", re.I)


def reset_epoch(at, text, now=None):
    """When a window resets, as an epoch in local time, or None.

    A client that gave an epoch is taken at its word. Otherwise the text is
    reworded by swlib's own reader into one shape and read here: a date with
    no year is the next one on or after yesterday, and a bare time of day
    (a 5-hour window) is its next occurrence.
    """
    now = time.time() if now is None else now
    if isinstance(at, (int, float)) or (isinstance(at, str) and at.isdigit()):
        return int(at)
    text = swlib._known(text) if text else None
    if not text:
        return None
    text = re.sub(r"\s*\([^()]*\)\s*$", "", text).strip()
    norm = swlib._normalize_reset(text)
    today = dt.datetime.fromtimestamp(now)
    match = _CLOCK.match(norm) or _DATE.match(norm)
    if match:
        parts = match.groups()
        hour, minute, month, day = (int(parts[0]), int(parts[1]), parts[2], int(parts[3])) \
            if len(parts) == 4 else (0, 0, parts[0], int(parts[1]))
        number = dt.datetime.strptime(month, "%b").month
        when = dt.datetime(today.year, number, day, hour, minute)
        if when < today - dt.timedelta(days=1):
            when = when.replace(year=today.year + 1)
        return int(when.timestamp())
    clock = _TIME_ONLY.search(text)
    if not clock:
        return None
    hour, minute = int(clock.group(1)), int(clock.group(2) or 0)
    if clock.group(3):
        hour = hour % 12 + (12 if clock.group(3).lower() == "pm" else 0)
    when = today.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if when < today:
        when += dt.timedelta(days=1)
    return int(when.timestamp())


def windows(role, kind, usage):
    """Each limit as a figure, when it resets and how long it runs, so the
    pop-up can draw time left and where the current pace leads."""
    extra = usage.get("extra") or {}
    out = []

    def add(name, pct, at, text, hours):
        pct = swlib.number(pct)
        if pct is None:
            return
        out.append({"name": name, "pct": pct, "resets_at": reset_epoch(at, text),
                    "hours": hours})

    if role == "cursor":
        add("Month", swlib.pool(usage, "included_pct"), None, extra.get("resets"), 720)
    elif kind == "monthly":
        add("Month", swlib.pool(usage, "monthly_pct"), None, extra.get("monthly_resets"), 720)
    else:
        add("5-hour", usage.get("five_hour_pct"), usage.get("five_hour_resets_at"),
            usage.get("five_hour_resets"), 5)
        add("Week", usage.get("seven_day_pct"), usage.get("seven_day_resets_at"),
            usage.get("seven_day_resets"), 168)
        for model, week in sorted((extra.get("model_weeks") or {}).items()):
            if isinstance(week, dict):
                add("%s week" % model, week.get("pct"), None, week.get("resets"), 168)
    return out


def account(role, cfg):
    row = {"role": role, "label": swlib.role_label(role, cfg)}
    if not swlib.reading_enabled(role, cfg):
        row.update(kind="reason", reason="no usage reading for this sign-in")
        return row
    usage = swlib.load_usage(role)
    state = swlib.freshness(role, cfg=cfg)
    if state not in ("fresh", "stale"):
        row.update(kind="reason", reason=swlib.status_phrase(swlib.status_kind(role))
                   or "no current reading, refreshing")
        return row
    row["age"] = swlib.cache_age(role)
    row["stale"] = state == "stale"
    row["plan"] = swlib._known(usage.get("plan"))
    if role == "cursor":
        row["kind"] = "pools"
        row["pools"] = [{"name": name, "pct": swlib.pool(usage, key)}
                        for name, key in (("included", "included_pct"),
                                          ("auto", "auto_pct"), ("api", "api_pct"))
                        if swlib.pool(usage, key) is not None]
        row["notes"] = [n for n in [swlib.reset_note(role, usage)] if n]
        row["windows"] = windows(role, "pools", usage)
        return row
    row["notes"] = [n for n in (swlib.reset_note(role, usage),
                                swlib.status_phrase(swlib.status_kind(role)),
                                swlib.identity_drift(role, cfg),
                                swlib.model_week_note(usage)) if n]
    if swlib._is_monthly_only(role, usage):
        row.update(kind="monthly", monthly=swlib.pool(usage, "monthly_pct"),
                   windows=windows(role, "monthly", usage))
        return row
    row.update(kind="bars", five=swlib.number(usage.get("five_hour_pct")),
               week=swlib.number(usage.get("seven_day_pct")),
               windows=windows(role, "bars", usage))
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default=None)
    a = ap.parse_args()
    if not swlib.is_configured():
        print(json.dumps({"configured": False}))
        return 0
    cfg = swlib.load_config()
    five, week = swlib.thresholds(cfg)
    route = swlib.active_route(a.session, cfg)
    out = {
        "configured": True,
        "thresholds": {"five": five, "week": week},
        "now": int(time.time()),
        "accounts": [account(role, cfg) for role in swlib.enabled_roles(cfg)],
        "destinations": swlib.destinations(cfg),
        "route": {k: route.get(k) for k in ("worker", "label", "model", "effort", "mode")}
        if route else None,
    }
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
