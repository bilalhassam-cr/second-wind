#!/usr/bin/env python3
"""Arm, show or clear the routing override from chat.

The model picker can only be used in a terminal, and the desktop app's menu
takes a typed model and runs no hook for it. So `/second-wind` asks its
questions in chat and calls this, which writes exactly the file the picker hook
writes, with `source: "chat"`.

A route is scoped to one session on purpose. The whole reason this exists is
that a route armed in one chat used to tell every other session to send its work
somewhere else.

    route.py --set codex --model gpt-5.6 --effort high --here
    route.py --set personal --mode review --all
    route.py --show
    route.py --clear
    route.py --destinations

A session-scoped route is its own file, `~/.second-wind/routes/<session>.json`,
so two chats can each be routed somewhere without overwriting each other.
`--all` writes `~/.second-wind/mode`, which is the one route that applies
everywhere.

`--here` finds this session's id in the desktop app's own store, by the
directory it is running in. When it cannot, it says so and falls back to a route
that applies everywhere, because a route nobody can scope is still better than
silently arming nothing.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swlib  # noqa: E402

EVERY_SESSION = "*"


def fail(message, code=2):
    sys.stderr.write("second-wind: %s\n" % message)
    return code


def here_session(a):
    """This session's id, from `--session` or the desktop app's own store, or
    None when nothing can name it."""
    if a.session:
        return a.session.strip()
    record = swlib.desktop_session_here()
    session = (record or {}).get("cliSessionId")
    return session.strip() if isinstance(session, str) and session.strip() \
        else None


def scope(a):
    """(session id or "*", note to print). `--here` is the default: a route
    meant for every session is a bigger claim and has to be asked for."""
    if a.all:
        return EVERY_SESSION, "applies to every session until cleared"
    session = here_session(a)
    if session:
        return session, ""
    return EVERY_SESSION, ("this session could not be identified, so the route "
                           "applies to every session until cleared")


def route_from(a):
    """The worker, model and effort as one route, or None with a reason."""
    route = swlib.parse_route(a.set) or swlib.role_route(a.set)
    if not route:
        return None, ("%s is not a worker. Use personal, codex, grok or cursor."
                      % a.set)
    cfg = swlib.load_config()
    if route["worker"] not in swlib.enabled_roles(cfg):
        return None, ("%s is not connected, so it cannot take the work. Run "
                      "setup, or pick another destination." % route["label"])
    route["label"] = swlib.route_label(route["worker"], cfg)
    for key, given in (("model", a.model), ("effort", a.effort)):
        if given:
            route[key] = given.strip()
    if route["model"] or route["effort"]:
        # `default` in the model position is how a preset says "pass no model",
        # which is what Grok's effort-only rows and Cursor's single row do.
        entry = "%s/%s" % (route["model"] or "default", route["effort"]) \
            if route["effort"] else route["model"]
        preset = swlib.parse_model_entry(entry)
        if not preset:
            return None, ("%s is not a model and effort this can pass on."
                          % entry)
        route["model"], route["effort"] = preset["model"], preset["effort"]
    route["mode"] = a.mode
    return route, ""


def describe(route, session):
    parts = ["Routing to %s" % route["label"]]
    for label, key in (("model", "model"), ("effort", "effort")):
        if route.get(key):
            parts.append("%s %s" % (label, route[key]))
    if route.get("mode"):
        parts.append("%s mode" % route["mode"])
    where = "every session" if session == EVERY_SESSION else "this session only"
    return "%s, %s." % (", ".join(parts), where)


def cmd_set(a):
    route, problem = route_from(a)
    if not route:
        return fail(problem, 1)
    session, note = scope(a)
    swlib.write_mode(route, source="chat", session_id=session)
    print(describe(route, session))
    if note:
        print(note)
    print("/second-wind off stops it.")
    return 0


def cmd_clear(a):
    """Clear this session's route. `--all` clears the one that applies
    everywhere, `--session` takes an id. When the session cannot be named, the
    armed routes are listed rather than one of them guessed at: removing
    somebody else's route is exactly the fault this store exists to stop."""
    if a.all:
        if not swlib.mode_text():
            print("No route applies to every session. Nothing to clear.")
            return 0
        swlib.drop_mode()
        print("Routing off for every session, back to usage-based handover.")
        return 0
    session = here_session(a)
    if session:
        removed = swlib.clear_route(session)
        if removed:
            if swlib.mode_path() in removed:
                print("Routing off, back to usage-based handover. The route "
                      "that applied to every session is gone too.")
            else:
                print("Routing off for this session, back to usage-based "
                      "handover.")
            return 0
    armed = swlib.armed_routes()
    if not armed:
        print("No route was armed. Nothing to clear.")
        return 0
    if session:
        print("Nothing was armed for this session. Armed elsewhere:")
    else:
        print("This session could not be identified, so nothing was cleared. "
              "Armed now:")
    for row in armed:
        print("  %s" % summary(row))
    print("Clear one with --clear --session ID, or --clear --all for the route "
          "that applies to every session.")
    return 0


def where(row):
    return "every session" if row["scope"] == EVERY_SESSION else row["scope"]


def summary(row):
    """One armed route on one line, for a listing."""
    age = swlib.short_age(row["age"]) if row["age"] is not None else "unknown"
    label = row["label"] or row["worker"] or "unreadable"
    return "%s | %s | armed %s ago%s" % (where(row), label, age,
                                         " | expired" if row["expired"] else "")


def cmd_show(a):
    """Every armed route, with the session it belongs to, its label and its
    age, and which one applies here. First thing to check when a route seems to
    apply to the wrong chat."""
    rows = swlib.armed_routes()
    if not rows:
        print("No route armed. Work stays on this account.")
        return 0
    here = here_session(a)
    applies = swlib.active_route(here)
    print("Armed routes: %d" % len(rows))
    for row in rows:
        print()
        print("Session   %s%s" % (where(row),
                                  " (a bare word, so it applies to every "
                                  "session)" if row["source"] == "a bare word"
                                  else ""))
        print("Worker    %s" % (row["worker"] or "unreadable"))
        print("Label     %s" % (row["label"] or ""))
        print("Model     %s" % (row["model"] or "not set"))
        print("Effort    %s" % (row["effort"] or "not set"))
        print("Mode      %s" % (row["mode"] or "the configured default"))
        print("Armed by  %s" % (row["source"] or "unknown"))
        print("Age       %s" % (swlib.short_age(row["age"])
                                if row["age"] is not None else "unknown"))
        print("File      %s" % swlib.tilde(row["path"]))
        if applies and applies.get("path") == row["path"]:
            print("Applies here: yes")
        elif not row["readable"]:
            print("Applies here: no, the file is not readable. Clear it.")
        elif row["expired"]:
            print("Applies here: no, it was armed more than 12 hours ago and "
                  "is ignored")
        elif row["scope"] not in (EVERY_SESSION, here):
            print("Applies here: no, it was armed in another session")
        elif applies:
            print("Applies here: no, this session's own route takes it")
        else:
            print("Applies here: no")
    return 0


def cmd_destinations():
    """One line per place the next tasks could go, for the picker to read."""
    rows = swlib.destinations()
    if not rows:
        print("No destinations are connected. Run setup first.")
        return 0
    for row in rows:
        head = "%d%% headroom" % row["headroom"] \
            if row["headroom"] is not None else "headroom unknown"
        print("%s | %s | %s | %s"
              % (row["worker"], row["label"], head, row["usage"]))
        for entry in row["models"]:
            print("    %s | %s" % (entry["text"], entry["note"]))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        prog="route.py", description="arm, show or clear the second-wind route")
    ap.add_argument("--set", metavar="WORKER",
                    help="personal, codex, grok or cursor")
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", default=None)
    ap.add_argument("--mode", choices=["review", "work"], default=None)
    ap.add_argument("--session", metavar="ID", default=None,
                    help="scope the route to this session id")
    ap.add_argument("--here", action="store_true",
                    help="scope it to the session in this directory (default)")
    ap.add_argument("--all", action="store_true",
                    help="apply it to every session until cleared")
    ap.add_argument("--clear", action="store_true", help="remove the route")
    ap.add_argument("--show", action="store_true", help="print the route")
    ap.add_argument("--destinations", action="store_true",
                    help="print the workers, their usage and their model rows")
    return ap


def main():
    ap = build_parser()
    a = ap.parse_args()
    if len([x for x in (a.session, a.here, a.all) if x]) > 1:
        return fail("pick one of --session, --here or --all")
    if a.clear:
        return cmd_clear(a)
    if a.show:
        return cmd_show(a)
    if a.destinations:
        return cmd_destinations()
    if not a.set:
        ap.print_help()
        return 2
    if not swlib.is_configured():
        return fail("not set up yet. Run setup.py --detect first.", 1)
    return cmd_set(a)


if __name__ == "__main__":
    sys.exit(main())
