#!/usr/bin/env python3
"""PreModelSwitch hook: the model picker doubles as a routing switch.

Picking a `second-wind/...` row, or typing `/model second-wind/codex/gpt-5.6/high`,
is not a model change at all. This session keeps the model it has and the next
tasks go to that worker instead, so the hook refuses the switch on purpose and
writes the mode file the prompt guard reads at the next prompt.

The refusal is the mechanism, not a failure. Claude Code has no "do something
else" entry in a picker, and a row that quietly left the session on some other
model would be worse than one that says what it did.

Picking any real model deletes the mode file, so stopping is the same gesture as
starting. A real model is never blocked, whatever else is going on.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import swlib  # noqa: E402


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def route_of(payload, cfg):
    """The route this request names, or None.

    Both fields are read: `to_model` is what the matcher saw, `requested_model`
    is what the person typed, and on a target Claude Code cannot canonicalise
    the two do not have to agree.
    """
    for key in ("to_model", "requested_model"):
        found = swlib.parse_route(payload.get(key), cfg)
        if found:
            return found
    return None


def main():
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    cfg = swlib.load_config()
    mode = os.path.join(swlib.sw_home(), "mode")
    route = route_of(payload, cfg)

    if not route:
        if not os.path.exists(mode):
            return 0
        try:
            os.unlink(mode)
        except OSError:
            return 0
        # PreModelSwitch shows a systemMessage whatever the decision is, so this
        # says routing stopped without standing in the way of the switch.
        print(json.dumps({"systemMessage": "second-wind: routing off, back to "
                                           "usage-based handover."}))
        return 0

    if route["worker"] not in swlib.enabled_roles(cfg):
        return block("second-wind: that worker is not connected; run setup.")

    swlib.write_json_atomic(mode, {
        "worker": route["worker"],
        "model": route["model"],
        "effort": route["effort"],
        "set_at": int(time.time()),
        "label": route["label"],
    })
    detail = ""
    if route["model"]:
        detail += ", model %s" % route["model"]
    if route["effort"]:
        detail += ", effort %s" % route["effort"]
    return block("second-wind: the next tasks route to %s%s. Your session model "
                 "is unchanged. Pick any normal model to stop routing."
                 % (route["label"], detail))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        # A hook that raises is a broken session, and a routing convenience is
        # never worth one. Silence, and the switch goes through.
        raise SystemExit(0)
