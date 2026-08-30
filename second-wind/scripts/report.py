#!/usr/bin/env python3
"""Summarise delegated work. Built for a weekly or monthly review.

    report.py [days]        default 7

The reverse check is the point: work handed out and never verified is the risk
this log exists to expose. A delegated job that half-worked still returns text
that reads like success, and its exit code is still zero.
"""
import json, os, sys, glob, time
from collections import Counter

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7
SW_HOME = os.environ.get("SW_HOME", os.path.expanduser("~/.second-wind"))
CFG = os.path.join(SW_HOME, "config.json")
logdir = os.path.join(SW_HOME, "log")
try:
    with open(CFG) as f:
        d = json.load(f).get("log_dir", "")
    if d:
        logdir = os.path.expanduser(d)
except Exception:
    pass

if not os.path.isdir(logdir):
    print(f"WARN second-wind: no log directory at {logdir}")
    sys.exit(0)

cutoff = time.time() - DAYS * 86400
def parse(ts):
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except Exception:
        return 0

rows = []
for f in sorted(glob.glob(os.path.join(logdir, "*.jsonl"))):
    for line in open(f):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if parse(r.get("ts", "")) >= cutoff:
            rows.append(r)

if not rows:
    print(f"PASS second-wind: nothing delegated in {DAYS} days "
          "(the primary account carried all the work)")
    sys.exit(0)

by_worker = Counter(r.get("worker", "?") for r in rows)
by_mode = Counter(r.get("mode", "?") for r in rows)
fails = [r for r in rows if r.get("exit", 0) != 0]
missing = [r for r in rows if not os.path.exists(r.get("exchange", ""))]
secs = sum(r.get("duration_s", 0) for r in rows)

parts = ", ".join(f"{n} {w}" for w, n in by_worker.most_common())
modes = ", ".join(f"{n} {m}" for m, n in by_mode.most_common())
status = "WARN" if (fails or missing) else "PASS"
print(f"{status} second-wind: {len(rows)} calls in {DAYS} days ({parts}; {modes}), "
      f"{secs // 60}m of work moved off the primary account, {len(fails)} failed")

if fails:
    print("\nDo this: failed delegations to look at")
    for r in fails[-3:]:
        print(f"  - {r.get('ts','?')[:16]} {r.get('worker','?')} exit={r.get('exit')} "
              f"in {os.path.basename(r.get('cwd','?'))}")
        print(f"    {r.get('exchange','?')}")

if missing:
    print(f"\nWARN: {len(missing)} ledger entries point at an exchange file that is gone")

big = [r for r in rows if r.get("reply_bytes", 0) > 3000 and r.get("exit", 0) == 0]
if big:
    print(f"\nWorth a look: {len(big)} substantial replies came back (over 3KB). "
          "Confirm each finding landed in a memory file or a project file, not just here. "
          "Neither worker's own memory is ever read by the primary session.")
    for r in big[-3:]:
        print(f"  - {r.get('ts','?')[:16]} {r.get('worker','?')} "
              f"{r.get('reply_bytes')}b -> {os.path.basename(r.get('exchange',''))}")

thin = [r for r in rows if r.get("prompt_bytes", 0) > 4000
        and r.get("reply_bytes", 0) < 500 and r.get("exit", 0) == 0]
if thin:
    print(f"\nWorth a look: {len(thin)} call(s) sent a large prompt and got a thin reply. "
          "Delegating costs a prompt that has to carry context the worker lacks; when that "
          "prompt is bigger than the answer, the job was cheaper done on the primary account.")
