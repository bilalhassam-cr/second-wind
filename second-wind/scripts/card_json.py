#!/usr/bin/env python3
"""The usage panel as data, for the usage pop-up mod to draw.

Same readings and the same rules as `swlib.brief_card`, so the pop-up and the
text panel can never disagree; only the drawing differs. Nothing is spawned.

  card_json.py [--session ID]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swlib  # noqa: E402


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
    if role == "cursor":
        row["kind"] = "pools"
        row["pools"] = [{"name": name, "pct": swlib.pool(usage, key)}
                        for name, key in (("included", "included_pct"),
                                          ("auto", "auto_pct"), ("api", "api_pct"))
                        if swlib.pool(usage, key) is not None]
        row["notes"] = [n for n in [swlib.reset_note(role, usage)] if n]
        return row
    row["notes"] = [n for n in (swlib.reset_note(role, usage),
                                swlib.status_phrase(swlib.status_kind(role)),
                                swlib.identity_drift(role, cfg),
                                swlib.model_week_note(usage)) if n]
    if swlib._is_monthly_only(role, usage):
        row.update(kind="monthly", monthly=swlib.pool(usage, "monthly_pct"))
        return row
    row.update(kind="bars", five=swlib.number(usage.get("five_hour_pct")),
               week=swlib.number(usage.get("seven_day_pct")))
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
        "accounts": [account(role, cfg) for role in swlib.enabled_roles(cfg)],
        "destinations": swlib.destinations(cfg),
        "route": {k: route.get(k) for k in ("worker", "label", "model", "effort", "mode")}
        if route else None,
    }
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
