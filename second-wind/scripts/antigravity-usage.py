#!/usr/bin/env python3
"""Read each Antigravity account's limits from its own /usage command.

agy answers `-p /usage --output-format json` with every quota pool on the
account, each with its 5-hour and weekly windows, the fraction left and the
reset time. It costs no model tokens. The reader runs that, through the
official client and under the account's own HOME, and saves one reading per
account for the usage panel. It never reads a credential: probing agy's local
server or reusing its OAuth client is what Google's terms warn against.

  antigravity-usage.py [--account NAME ...] [--if-older MINUTES] [--cwd DIR]
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swlib  # noqa: E402

# The same keys the delegation runner clears: with any of them set agy can
# bill a Cloud project instead of the account's plan.
CLEARED = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "AGY_ADC_AUTH")


def environment(profile):
    env = os.environ.copy()
    for key in CLEARED:
        env.pop(key, None)
    if profile.get("home"):
        home = swlib.expand(profile["home"])
        if not os.path.isdir(home):
            raise ValueError("home does not exist: %s" % home)
        env["HOME"] = home
    return env


def parse(output):
    """The pools, with used percentages, from agy's JSON envelope."""
    try:
        result = json.loads(output[output.index("{"):])
    except ValueError:
        raise ValueError("agy returned no JSON")
    if not isinstance(result, dict) or result.get("status") != "SUCCESS":
        raise ValueError(str((result or {}).get("error") or "agy did not succeed")
                         if isinstance(result, dict) else "agy returned no object")
    groups = (((result.get("command") or {}).get("data") or {}).get("groups"))
    if not isinstance(groups, list) or not groups:
        raise ValueError("agy reported no usage pools; its /usage output may have changed")
    pools = []
    for group in groups:
        buckets = []
        for bucket in group.get("buckets") or []:
            left = swlib.number(bucket.get("remaining_fraction"))
            if bucket.get("window") not in ("5h", "weekly") or left is None:
                continue
            buckets.append({"window": bucket["window"],
                            "used_pct": round(max(0.0, min(1.0, 1.0 - left)) * 100, 1),
                            "reset_time": bucket.get("reset_time")})
        if buckets:
            pools.append({"name": str(group.get("name") or "unnamed"), "buckets": buckets})
    if not pools:
        raise ValueError("agy reported no 5-hour or weekly windows; its /usage output may have changed")
    return pools


def read(name, profile, cwd):
    binary = swlib.agy_path(profile)
    if not binary:
        raise ValueError("agy is not on PATH and the Gemini app is not installed")
    proc = subprocess.run([binary, "-p", "/usage", "--output-format", "json"],
                          env=environment(profile), cwd=cwd, text=True,
                          capture_output=True, timeout=90, check=False)
    output = proc.stdout + proc.stderr
    if "sign in" in output.lower() and "SUCCESS" not in proc.stdout:
        raise ValueError("not signed in; run agy once with this account's HOME")
    return parse(proc.stdout)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--account", action="append", help="one account; repeatable. Default: every Antigravity account")
    ap.add_argument("--if-older", type=float, default=0,
                    help="keep a successful reading younger than this many minutes")
    ap.add_argument("--cwd", default=None, help="directory agy runs in")
    a = ap.parse_args(argv)
    cfg = swlib.load_config()
    names = a.account or swlib.antigravity_accounts(cfg)
    accounts = cfg.get("accounts") or {}
    cwd = a.cwd or swlib.sw_home()
    failed = False
    for name in names:
        profile = accounts.get(name)
        if not isinstance(profile, dict) or profile.get("kind") != "antigravity":
            print("FAILED: %s is not an Antigravity account" % name)
            failed = True
            continue
        cached = swlib.load_antigravity_usage(name)
        age = time.time() - (swlib.number(cached.get("read_at")) or 0)
        if a.if_older > 0 and cached.get("groups") and not cached.get("error") and age < a.if_older * 60:
            print("OK %s %s (cached %s)" % (name, swlib.antigravity_summary(cached), swlib.short_age(age)))
            continue
        try:
            reading = {"read_at": time.time(), "groups": read(name, profile, cwd)}
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            reading = {"read_at": time.time(), "error": str(exc)[:300]}
            failed = True
        swlib.write_text_atomic(swlib.antigravity_usage_path(name), json.dumps(reading) + "\n")
        print("%s %s %s" % ("FAILED:" if reading.get("error") else "OK", name,
                            swlib.antigravity_summary(reading)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
