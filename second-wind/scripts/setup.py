#!/usr/bin/env python3
"""Write the second-wind config and wire it into the primary profile.

Two steps on purpose. `--detect` reports what is on the machine; a human then
chooses which account is primary. A script cannot infer that choice: primary is
wherever the person actually works and keeps their history, and getting it
backwards silently sends their main work to the wrong subscription.

  setup.py --detect
  setup.py --write --primary ~/.claude --secondary ~/.claude-secondary
      [--codex on|off] [--grok on|off] [--cursor on|off]
  setup.py --accounts
  setup.py --show
  setup.py --uninstall
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time

HOME = os.path.expanduser("~")
SW_HOME = os.environ.get("SW_HOME", os.path.join(HOME, ".second-wind"))
CONFIG = os.path.join(SW_HOME, "config.json")
HERE = os.path.dirname(os.path.abspath(__file__))

def tilde(p): return p.replace(HOME, "~", 1)
def expand(p): return os.path.expanduser(p)

def load_cfg():
    with open(CONFIG) as f:
        return json.load(f)

def settings_path(config_dir):
    return os.path.join(expand(config_dir), "settings.json")

def write_json(path, data, mode=0o600):
    """Write via a temp file in the same directory then rename, so a crash or a
    concurrent reader never sees a half-written settings file."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".second-wind-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise

def backup(path):
    if os.path.exists(path):
        b = f"{path}.second-wind-backup-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, b)
        return b
    return None

def merge_settings(config_dir, install=True, include_guard=True):
    """Add (or remove) the status bar and the usage-guard hook, leaving every
    other setting alone. We never rewrite a settings file wholesale: it is the
    user's, and it usually holds hooks and plugins we know nothing about."""
    p = settings_path(config_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data = {}
    if os.path.exists(p):
        try:
            with open(p) as f:
                data = json.load(f)
        except Exception:
            print(f"  ! {tilde(p)} is not valid JSON. Leaving it alone.", file=sys.stderr)
            return False
    b = backup(p)

    sl_cmd = os.path.join(HERE, "statusline.sh")
    guard_cmd = os.path.join(HERE, "usage-guard.sh")
    sl = {"type": "command", "command": sl_cmd, "padding": 0}
    hook = {"hooks": [{"type": "command", "command": guard_cmd,
                       "timeout": 10, "statusMessage": "Checking usage headroom"}]}
    saved = os.path.join(SW_HOME, "replaced-statusline.json")

    def ours(h):
        # match on our own command path, not on a substring of the whole entry,
        # so we never delete a hook that merely mentions the same words
        return any(x.get("command") == guard_cmd for x in (h.get("hooks") or [])
                   if isinstance(x, dict))

    if install:
        existing = data.get("statusLine")
        if existing and existing != sl:
            # keep whatever was there so uninstall can put it back
            os.makedirs(SW_HOME, exist_ok=True)
            store = {}
            if os.path.exists(saved):
                try:
                    store = json.load(open(saved))
                except Exception:
                    store = {}
            store[config_dir] = existing
            write_json(saved, store)
            print(f"  note: replaced an existing status line in {tilde(p)}. "
                  f"Uninstall restores it.")
        data["statusLine"] = sl
        if include_guard:
            hooks = data.setdefault("hooks", {})
            ups = [h for h in hooks.get("UserPromptSubmit", []) if not ours(h)]
            ups.append(hook)
            hooks["UserPromptSubmit"] = ups
    else:
        if data.get("statusLine", {}).get("command") == sl_cmd:
            restored = None
            if os.path.exists(saved):
                try:
                    restored = json.load(open(saved)).get(config_dir)
                except Exception:
                    restored = None
            if restored:
                data["statusLine"] = restored
                print(f"  restored the previous status line in {tilde(p)}")
            else:
                data.pop("statusLine", None)
        hooks = data.get("hooks", {})
        if "UserPromptSubmit" in hooks:
            hooks["UserPromptSubmit"] = [h for h in hooks["UserPromptSubmit"] if not ours(h)]
            if not hooks["UserPromptSubmit"]:
                hooks.pop("UserPromptSubmit")
    write_json(p, data, mode=0o600)
    return b

def metadata_path(config_dir):
    d = expand(config_dir)
    if os.path.realpath(d) == os.path.realpath(os.path.join(HOME, ".claude")):
        return os.path.join(HOME, ".claude.json")
    return os.path.join(d, ".claude.json")

def trusted_project(config_dir):
    """Use a trust decision the user has already made instead of inventing one."""
    path = metadata_path(config_dir)
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:
        sys.exit(f"second-wind: cannot read {tilde(path)} to find a trusted working "
                 f"directory: {e}")
    projects = data.get("projects") or {}
    candidates = []
    for project, entry in projects.items():
        if (isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True
                and os.path.isdir(expand(project))):
            candidates.append((expand(project), entry))
    if not candidates:
        sys.exit("second-wind: the primary profile has no existing trusted working "
                 "directory. Trust one in Claude Code, then run --write again.")
    current = os.path.realpath(os.getcwd())
    candidates.sort(key=lambda item: os.path.realpath(item[0]) != current)
    return data, candidates[0][0], candidates[0][1]

def cmd_discover():
    subprocess.run([sys.executable, os.path.join(HERE, "discover.py")])

def detection():
    found = json.loads(subprocess.run(
        [sys.executable, os.path.join(HERE, "discover.py")],
        capture_output=True, text=True).stdout or "{}")
    if os.path.exists(CONFIG):
        try:
            found["config"] = load_cfg()
        except Exception as exc:
            found["config"] = {"error": f"could not read config: {exc}"}
    else:
        found["config"] = None
    return found

def cmd_detect():
    print(json.dumps(detection(), indent=2))

def cmd_write(a):
    previous = load_cfg() if os.path.exists(CONFIG) else {}
    prof = json.loads(subprocess.run(
        [sys.executable, os.path.join(HERE, "discover.py")],
        capture_output=True, text=True).stdout or "{}")
    by_dir = {p["config_dir"]: p for p in prof.get("claude_profiles", [])}

    def look(d):
        return by_dir.get(tilde(expand(d))) or by_dir.get(d) or {}

    prim = look(a.primary)
    sec = look(a.secondary) if a.secondary else {}
    if a.secondary and os.path.realpath(expand(a.primary)) == os.path.realpath(expand(a.secondary)):
        sys.exit("second-wind: primary and secondary resolve to the same directory.")
    if a.secondary and not os.path.isdir(expand(a.secondary)):
        sys.exit(f"second-wind: {a.secondary} does not exist. Create and sign in to the "
                 "profile first: see references/setup.md. Refusing to write settings into a "
                 "directory that is not a Claude profile.")
    for name, v in (("--five-hour", a.five_hour), ("--seven-day", a.seven_day)):
        if not 1 <= v <= 100:
            sys.exit(f"second-wind: {name} must be between 1 and 100")
    if a.timeout < 30:
        sys.exit("second-wind: --timeout must be at least 30 seconds")
    if not 1 <= a.refresh_minutes <= 1440:
        sys.exit("second-wind: --refresh-minutes must be between 1 and 1440")
    if not prim.get("logged_in"):
        print(f"  ! primary {a.primary} is not signed in. Sign it in first.", file=sys.stderr)
    if a.secondary and not sec.get("logged_in"):
        if not a.force:
            sys.exit(f"second-wind: secondary {a.secondary} is not signed in, so every "
                     "delegation to it would fail. Sign it in first, or pass --force.")
        print(f"  ! secondary {a.secondary} is not signed in (forced).", file=sys.stderr)
    if not prim.get("logged_in") and not a.force:
        sys.exit(f"second-wind: primary {a.primary} is not signed in. Sign it in first, "
                 "or pass --force.")

    print("Recording the constraints imposed by each worker command...")
    probe = json.loads(subprocess.run(
        [sys.executable, os.path.join(HERE, "probe.py")],
        capture_output=True, text=True).stdout or "{}")
    codex_browser = (probe.get("codex") or {}).get("can_launch_browser")

    def selected(name, state, require_login=True):
        info = prof.get(name) or {}
        installed = info.get("installed") is True
        logged = info.get("logged_in") is True
        if state == "on" and not installed:
            sys.exit(f"second-wind: --{name} on was requested, but its command is not on PATH.")
        if state == "on" and require_login and not logged:
            sys.exit(f"second-wind: --{name} on was requested, but it is not signed in.")
        if state == "off":
            return False
        return installed and (logged or not require_login)

    codex_on = selected("codex", a.codex)
    grok_on = selected("grok", a.grok, require_login=False)
    cursor_on = selected("cursor", a.cursor)
    if not any((a.secondary, codex_on, grok_on, cursor_on)):
        sys.exit("second-wind: nothing to delegate to. Connect at least one worker first.")
    _, working_dir, _ = trusted_project(a.primary)
    cfg = {
        "version": 3,
        "created": time.strftime("%Y-%m-%d"),
        "primary": {
            "kind": "claude", "label": "primary",
            "config_dir": tilde(expand(a.primary)),
            "account": prim.get("account", "unknown"),
            "plan": prim.get("subscription", "unknown") or "unknown",
            "is_default_dir": prim.get("is_default_dir", False),
        },
        "secondary": {
            "kind": "claude", "label": "secondary",
            "enabled": bool(a.secondary),
            "config_dir": tilde(expand(a.secondary)) if a.secondary else "",
            "account": sec.get("account", "unknown") if a.secondary else "",
            "plan": (sec.get("subscription", "unknown") or "unknown") if a.secondary else "",
            "is_default_dir": sec.get("is_default_dir", False),
        },
        "codex": {
            "kind": "codex", "label": "codex", "enabled": codex_on,
            "account": prof.get("codex", {}).get("account", "unknown"),
            "plan": "unknown",
            "can_launch_browser": codex_browser,
            "browser_probe": (probe.get("codex") or {}).get("detail", ""),
        },
        "grok": {
            "kind": "grok", "label": "grok", "enabled": grok_on,
            "account": "unknown", "plan": "SuperGrok or X Premium+",
        },
        "cursor": {
            "kind": "cursor", "label": "cursor", "enabled": cursor_on,
            "account": prof.get("cursor", {}).get("account", "unknown"),
            "plan": "Cursor Pro",
        },
        "thresholds": {"five_hour_pct": a.five_hour, "seven_day_pct": a.seven_day},
        "refresh": {
            "interval_minutes": a.refresh_minutes,
            "working_dir": tilde(working_dir),
            "codex_enabled": codex_on and a.codex_harvest == "on",
            "grok_enabled": grok_on,
            "cursor_enabled": cursor_on,
        },
        "timeout_seconds": a.timeout,
        "failover": {"enabled": not a.no_failover, "announce": True},
        "defaults": {"mode": a.default_mode},
        "log_dir": tilde(os.path.join(SW_HOME, "log")),
        # where the skill lives, so SKILL.md can find its own scripts. Relative
        # paths do not work: a Bash tool call runs in the user's project, not here.
        "skill_dir": tilde(os.path.dirname(HERE)),
    }
    os.makedirs(SW_HOME, exist_ok=True)
    os.makedirs(os.path.join(SW_HOME, "log"), exist_ok=True)
    # the log holds whole prompts and replies, so keep the tree private
    os.chmod(SW_HOME, 0o700)
    os.chmod(os.path.join(SW_HOME, "log"), 0o700)
    write_json(CONFIG, cfg)

    b = merge_settings(cfg["primary"]["config_dir"], install=True)
    if b is False:
        sys.exit("second-wind: could not install the primary status line and usage guard. "
                 "Fix its settings.json and run --write again.")
    if a.secondary:
        sb = merge_settings(cfg["secondary"]["config_dir"], install=True)
        if sb is False:
            sys.exit("second-wind: could not install the secondary status line and usage "
                     "guard. Fix its settings.json and run --write again.")
    old_reader = (previous.get("reader") or {}).get("config_dir")
    if old_reader:
        merge_settings(old_reader, install=False, include_guard=False)

    print(f"\nWrote {tilde(CONFIG)}")
    if b:
        print(f"Backed up the primary settings file to {tilde(b)}")
    print(f"  primary   {cfg['primary']['account']}   ({cfg['primary']['config_dir']})")
    if a.secondary:
        print(f"  secondary {cfg['secondary']['account']}   ({cfg['secondary']['config_dir']})")
    else:
        print("  secondary off")
    print(f"  codex     {'on, ' + cfg['codex']['account'] if codex_on else 'off'}")
    print(f"  grok      {'on' if grok_on else 'off'}")
    print(f"  cursor    {'on, ' + cfg['cursor']['account'] if cursor_on else 'off'}")
    print("  Claude usage reader     on")
    print(f"  Codex usage reader      "
          f"{'on' if cfg['refresh']['codex_enabled'] else 'off'}")
    if codex_on and codex_browser is not True:
        print("  note: codex cannot launch a browser in its sandbox, so browser work")
        print("        will never be delegated to it. This is expected, not a fault.")
    print(f"  failover  {'on' if cfg['failover']['enabled'] else 'off'} "
          f"at {a.five_hour}% of the 5-hour window and {a.seven_day}% of the weekly window")
    print("\nRestart Claude Code. Then run:  setup.py --accounts  and  setup.py --check")

def cmd_show():
    if not os.path.exists(CONFIG):
        sys.exit("second-wind: not set up yet. Run setup.py --detect first.")
    print(json.dumps(load_cfg(), indent=2))

def cache_age(path):
    try:
        with open(path) as f:
            cached = int(json.load(f).get("cached_at", 0))
        age = int(time.time()) - cached
        return age if age >= -60 else None
    except Exception:
        return None

def short_age(seconds):
    if seconds is None:
        return "unknown"
    if seconds < 90:
        return f"{max(0, seconds)}s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"

def load_usage(role):
    path = os.path.join(SW_HOME, f"usage-{role}.json")
    try:
        with open(path) as f:
            return json.load(f), path
    except Exception:
        return {}, path

def effective_status(role, usage_path, cached_at):
    path = os.path.join(SW_HOME, f"refresh-status-{role}.txt")
    try:
        with open(path) as f:
            message = f.readline().strip()
        status_time = os.path.getmtime(path)
    except Exception:
        return ""
    usage_time = max(cached_at or 0, os.path.getmtime(usage_path) if os.path.exists(usage_path) else 0)
    return message if status_time >= usage_time else ""

def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def reset_text(value):
    if value in (None, ""):
        return "unknown"
    try:
        return time.strftime("%d %b %H:%M", time.localtime(int(float(value))))
    except (TypeError, ValueError, OverflowError):
        return str(value).strip()

def window_text(used, reset):
    pct = number(used)
    return f"{pct:.0f}% / {reset_text(reset)}" if pct is not None else "unknown"

def status_summary(message, has_cache):
    upper = message.upper()
    if "LOGIN EXPIRED" in upper:
        return "login expired: sign in"
    if "VERSION TOO OLD" in upper:
        return "version too old: upgrade Claude Code"
    if "MISSING PROFILE" in upper:
        return "profile missing: rerun setup"
    if message and not upper.startswith("OK"):
        return "cached after refresh failure" if has_cache else "refresh failed"
    return "ready" if has_cache else "no reading"

def account_row(role, profile, enabled=True):
    usage, path = load_usage(role)
    cached_at = int(usage.get("cached_at", 0) or 0)
    age = cache_age(path)
    message = effective_status(role, path, cached_at)
    blocking = any(marker in message.upper() for marker in
                   ("LOGIN EXPIRED", "VERSION TOO OLD", "MISSING PROFILE"))
    five = None if blocking else number(usage.get("five_hour_pct"))
    week = None if blocking else number(usage.get("seven_day_pct"))
    cursor_pools = [number(usage.get(key)) for key in
                    ("included_pct", "auto_pct", "api_pct")]
    cursor_pools = [value for value in cursor_pools if value is not None]
    if role in ("primary", "secondary", "codex"):
        headroom = None if five is None or week is None else 100 - max(five, week)
        limits = (f"5h {window_text(five, usage.get('five_hour_resets_at') or usage.get('five_hour_resets'))}; "
                  f"week {window_text(week, usage.get('seven_day_resets_at') or usage.get('seven_day_resets'))}")
    elif role == "grok":
        headroom = None if week is None else 100 - week
        limits = f"week {window_text(week, usage.get('seven_day_resets'))}"
    else:
        headroom = None if not cursor_pools else 100 - max(cursor_pools)
        parts = []
        for label, key in (("Included", "included_pct"), ("Auto", "auto_pct"), ("API", "api_pct")):
            value = number(usage.get(key))
            parts.append(f"{label} {value:.0f}%" if value is not None else f"{label} unknown")
        if usage.get("resets"):
            parts.append(f"resets {usage['resets']}")
        if usage.get("on_demand") is None and usage:
            parts.append("on-demand unavailable")
        limits = "; ".join(parts) if usage else "unknown"
    headroom = None if headroom is None else min(100, max(0, headroom))
    if not enabled:
        headroom = None
        limits = "unknown"
        message = "DISABLED"
    account = usage.get("account") or profile.get("account") or "unknown"
    plan = usage.get("plan") or profile.get("plan") or "unknown"
    return {
        "role": role,
        "account": account,
        "plan": plan,
        "limits": limits,
        "age": short_age(age) if usage else "unknown",
        "headroom": headroom,
        "status": "disabled" if message == "DISABLED" else status_summary(message, bool(usage)),
        "eligible": enabled,
        "usage": usage,
        "config_dir": profile.get("config_dir", ""),
    }

def live_plan(config_dir):
    """Ask a profile what plan it is on. Cheap and local: no tokens are spent.

    The stored plan can be "unknown" because discovery ran while that profile's
    terminal login was expired, which says nothing about the account itself.
    """
    if not config_dir:
        return ""
    env = dict(os.environ)
    if os.path.realpath(expand(config_dir)) == os.path.realpath(os.path.join(HOME, ".claude")):
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = expand(config_dir)
    try:
        out = subprocess.run(["claude", "auth", "status", "--json"],
                             capture_output=True, text=True, timeout=20,
                             env=env).stdout
        return (json.loads(out) or {}).get("subscriptionType", "") or ""
    except Exception:
        return ""

def cmd_accounts():
    if not os.path.exists(CONFIG):
        print("NOT SET UP. Run: setup.py --detect")
        return
    cfg = load_cfg()
    try:
        interval = int((cfg.get("refresh") or {}).get("interval_minutes", 15) or 15)
    except (TypeError, ValueError):
        interval = 15
    if interval < 1:
        interval = 15
    refresh = cfg.get("refresh") or {}
    roles = ["primary"]
    if (cfg.get("secondary") or {}).get("enabled"):
        roles.append("secondary")
    for role in ("codex", "grok", "cursor"):
        if ((cfg.get(role) or {}).get("enabled") is True and
                refresh.get(f"{role}_enabled", True) is True):
            roles.append(role)
    stale = [role for role in roles
             if (cache_age(os.path.join(SW_HOME, f"usage-{role}.json")) is None
                 or cache_age(os.path.join(SW_HOME, f"usage-{role}.json")) >= interval * 60)]
    refresh_note = "Usage readings were already fresh; no refresh was needed."
    if stale:
        try:
            subprocess.run([os.path.join(HERE, "usage-refresh.sh")], timeout=120,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            refresh_note = ("A refresh was attempted for stale enabled readings. "
                            "Failures remain unknown or show a cached age.")
        except subprocess.TimeoutExpired:
            refresh_note = "Refresh timed out. Showing cached figures with their age."
        except OSError as e:
            refresh_note = f"Refresh could not start ({e}). Showing cached figures with their age."
    rows = []
    for role in ("primary", "secondary", "codex", "grok", "cursor"):
        profile = cfg.get(role) or {}
        rows.append(account_row(role, profile,
                                role == "primary" or bool(profile.get("enabled"))))

    role_order = {"primary": 0, "secondary": 1, "codex": 2, "grok": 3, "cursor": 4}
    rows.sort(key=lambda row: (
        row["headroom"] is None,
        -(row["headroom"] or 0),
        role_order[row["role"]],
    ))
    recommendation = next((row for row in rows
                           if row["eligible"] and row["headroom"] is not None), None)
    if recommendation:
        print(f"Work should go to: {recommendation['role']} ({recommendation['account']})")
    else:
        print("Work destination: unknown until an eligible account has a readable limit.")
    print(refresh_note)
    for row in rows:
        if row["role"] in ("primary", "secondary") and row.get("plan", "unknown") in ("", "unknown"):
            got = live_plan(row.get("config_dir", ""))
            if got:
                row["plan"] = got

    print("Headroom uses the strictest reported pool: 5h and weekly for Claude/Codex, weekly for Grok, monthly pools for Cursor. Unknown sorts last.")

    headers = ("ROLE", "ACCOUNT", "PLAN", "LIMITS", "AGE", "HEADROOM", "STATUS")
    values = []
    for row in rows:
        values.append((
            row["role"], row["account"], row["plan"], row["limits"],
            row["age"], f"{row['headroom']:.0f}%" if row["headroom"] is not None else "unknown",
            row["status"],
        ))
    widths = [max(len(str(headers[i])), *(len(str(row[i])) for row in values))
              for i in range(len(headers) - 1)]
    def line(row):
        left = "  ".join(str(row[i]).ljust(widths[i]) for i in range(len(widths)))
        return f"{left}  {row[-1]}"
    print(line(headers))
    print(line(tuple("-" * len(header) for header in headers)))
    for row in values:
        print(line(row))

    codex_usage = next((row["usage"] for row in rows if row["role"] == "codex"), {})
    if codex_usage.get("monthly_credit_pct") is not None or codex_usage.get("credits_note"):
        credit_parts = []
        monthly = number(codex_usage.get("monthly_credit_pct"))
        if monthly is not None:
            credit_parts.append(f"{monthly:.0f}% used")
        if codex_usage.get("monthly_credit_resets"):
            credit_parts.append(f"resets {codex_usage['monthly_credit_resets']}")
        if codex_usage.get("credits_note"):
            credit_parts.append(codex_usage["credits_note"])
        print("Codex monthly credits: " + ", ".join(credit_parts)
              + ". They cap overage only; the 5h and weekly plan windows determine headroom.")

def cmd_check():
    """Answer the one question the tool cannot answer for itself: is the
    automatic handover actually armed? Every link in that chain fails silently by
    design, so without this there is no way to tell working from broken."""
    if not os.path.exists(CONFIG):
        print("NOT SET UP. Run: setup.py --detect"); return
    cfg = load_cfg()
    ok = True
    print(f"config          {tilde(CONFIG)}")
    print(f"primary         {cfg['primary']['account']}  ({cfg['primary']['config_dir']})")
    sec = cfg.get("secondary", {})
    print(f"secondary       {sec.get('account') or 'none'}"
          f"{'  (' + sec['config_dir'] + ')' if sec.get('config_dir') else ''}"
          f"{'' if sec.get('enabled') else '  [disabled]'}")
    cx = cfg.get("codex", {})
    print(f"codex           {cx.get('account') if cx.get('enabled') else 'off'}")
    if cx.get("enabled") and cx.get("can_launch_browser") is not True:
        print("                cannot launch a browser, so browser work is never sent to it")
    for role in ("grok", "cursor"):
        profile = cfg.get(role) or {}
        print(f"{role:15} {profile.get('account', 'unknown') if profile.get('enabled') else 'off'}")

    commands = {"codex": "codex", "grok": "grok", "cursor": "cursor-agent"}
    for role, command in commands.items():
        if (cfg.get(role) or {}).get("enabled"):
            installed = shutil.which(command) is not None
            print(f"{role + ' command':15} {'installed' if installed else 'MISSING'}")
            if not installed:
                ok = False

    fo = cfg.get("failover", {}).get("enabled", True)
    print(f"failover        {'on' if fo else 'OFF in config'}")
    if not fo:
        ok = False
    if os.path.exists(os.path.join(SW_HOME, "no-failover")):
        print("                OFF: ~/.second-wind/no-failover exists"); ok = False

    t5 = cfg["thresholds"]["five_hour_pct"]; t7 = cfg["thresholds"]["seven_day_pct"]
    print(f"thresholds      5h {t5}%   7d {t7}%")
    refresh = cfg.get("refresh") or {}
    print("Claude reader   on")
    for role in ("codex", "grok", "cursor"):
        enabled = ((cfg.get(role) or {}).get("enabled") is True and
                   refresh.get(f"{role}_enabled", True) is True)
        print(f"{role + ' reader':15} {'on' if enabled else 'off'}")

    sl_cmd = os.path.join(HERE, "statusline.sh")
    guard_cmd = os.path.join(HERE, "usage-guard.sh")
    for role in ("primary", "secondary"):
        profile = cfg.get(role) or {}
        if role == "secondary" and not profile.get("enabled"):
            continue
        path = settings_path(profile["config_dir"])
        try:
            with open(path) as f:
                settings = json.load(f)
            status_installed = settings.get("statusLine", {}).get("command") == sl_cmd
            prompt_hooks = settings.get("hooks", {}).get("UserPromptSubmit", [])
            guard_installed = any(
                hook.get("command") == guard_cmd
                for group in prompt_hooks if isinstance(group, dict)
                for hook in (group.get("hooks") or []) if isinstance(hook, dict)
            )
            print(f"{role + ' status':15} {'installed' if status_installed else 'MISSING'}")
            print(f"{role + ' guard':15} {'installed' if guard_installed else 'MISSING'}")
            if not status_installed or not guard_installed:
                print(f"                Check {tilde(path)} or run --write again.")
                ok = False
        except Exception as e:
            print(f"{role + ' settings':15} UNREADABLE: {tilde(path)}: {e}")
            ok = False

    usage = os.path.join(SW_HOME, "usage-primary.json")
    if not os.path.exists(usage):
        print("reading         NONE YET")
        print("                Run setup.py --accounts to refresh the prompt-free usage panel.")
        ok = False
    else:
        try:
            u = json.load(open(usage))
            age = int(time.time() - u.get("cached_at", 0))
            fresh = -60 <= age <= 3600
            five = u.get("five_hour_pct")
            seven = u.get("seven_day_pct")
            five_text = f"{number(five):.0f}" if number(five) is not None else "?"
            seven_text = f"{number(seven):.0f}" if number(seven) is not None else "?"
            print(f"reading         5h {five_text}%   7d {seven_text}%   "
                  f"({age // 60}m old{'' if fresh else ', TOO OLD, ignored'})")
            if not fresh:
                ok = False
        except Exception as e:
            print(f"reading         UNREADABLE: {e}"); ok = False

    for role in ("primary", "secondary", "codex", "grok", "cursor"):
        status_file = os.path.join(SW_HOME, f"refresh-status-{role}.txt")
        if not os.path.exists(status_file):
            continue
        usage_data, usage_path = load_usage(role)
        message = effective_status(role, usage_path, int(usage_data.get("cached_at", 0) or 0))
        if message and not message.upper().startswith("OK"):
            print(f"{role + ' refresh':15} {message}")
            if any(marker in message.upper() for marker in
                   ("LOGIN EXPIRED", "VERSION TOO OLD", "MISSING PROFILE")):
                ok = False

    print()
    print("ARMED: handover will fire when a threshold is crossed" if ok
          else "NOT ARMED: fix the failures shown above")

def cmd_uninstall():
    if os.path.exists(CONFIG):
        cfg = load_cfg()
        for k in ("primary", "secondary", "reader"):
            d = cfg.get(k, {}).get("config_dir")
            if d:
                merge_settings(d, install=False, include_guard=(k != "reader"))
        print("Removed the installed status lines and usage guards from all profiles.")
        print(f"Config and logs are still at {tilde(SW_HOME)}; delete that folder to finish.")
    else:
        print("Nothing to uninstall.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detect", action="store_true",
                    help="print installed workers, Claude profiles and existing config as JSON")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--accounts", action="store_true",
                    help="show every account sorted by current usage headroom")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--primary")
    ap.add_argument("--secondary")
    ap.add_argument("--codex", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--grok", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--cursor", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--five-hour", type=int, default=90)
    ap.add_argument("--seven-day", type=int, default=80)
    ap.add_argument("--default-mode", choices=["review", "work"], default="review")
    ap.add_argument("--no-failover", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="write the config even if a profile is not signed in")
    ap.add_argument("--check", action="store_true",
                    help="say whether automatic handover is actually armed")
    ap.add_argument("--timeout", type=int, default=600,
                    help="seconds before a delegated call is killed (default 600)")
    ap.add_argument("--refresh-minutes", type=int, default=15,
                    help="usage refresh interval in minutes (default 15)")
    ap.add_argument("--codex-harvest", choices=["on", "off"], default="on",
                    help="read Codex /status without a model prompt (default on)")
    a = ap.parse_args()
    if a.detect: return cmd_detect()
    if a.discover: return cmd_discover()
    if a.accounts: return cmd_accounts()
    if a.check: return cmd_check()
    if a.show: return cmd_show()
    if a.uninstall: return cmd_uninstall()
    if a.write:
        if not a.primary:
            sys.exit("second-wind: --write needs --primary")
        return cmd_write(a)
    ap.print_help()

if __name__ == "__main__":
    main()
