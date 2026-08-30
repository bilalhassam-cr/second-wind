#!/usr/bin/env python3
"""Write the second-wind config and wire it into the primary profile.

Two steps on purpose. `--discover` reports what is on the machine; a human then
chooses which account is primary. A script cannot infer that choice: primary is
wherever the person actually works and keeps their history, and getting it
backwards silently sends their main work to the wrong subscription.

  setup.py --discover
  setup.py --write --primary ~/.claude --secondary ~/.claude-secondary [--codex on|off]
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

def merge_settings(config_dir, install=True):
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

def cmd_discover():
    subprocess.run([sys.executable, os.path.join(HERE, "discover.py")])

def cmd_write(a):
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

    codex_on = a.codex == "on" and prof.get("codex", {}).get("installed", False) \
               and prof.get("codex", {}).get("logged_in", False)
    if not a.secondary and not codex_on:
        sys.exit("second-wind: nothing to delegate to. Give --secondary, or sign in to Codex.")
    cfg = {
        "version": 1,
        "created": time.strftime("%Y-%m-%d"),
        "primary": {
            "kind": "claude", "label": "primary",
            "config_dir": tilde(expand(a.primary)),
            "account": prim.get("account", "unknown"),
            "is_default_dir": prim.get("is_default_dir", False),
        },
        "secondary": {
            "kind": "claude", "label": "secondary",
            "enabled": bool(a.secondary),
            "config_dir": tilde(expand(a.secondary)) if a.secondary else "",
            "account": sec.get("account", "unknown") if a.secondary else "",
            "is_default_dir": sec.get("is_default_dir", False),
        },
        "codex": {
            "kind": "codex", "label": "codex", "enabled": codex_on,
            "account": prof.get("codex", {}).get("account", "unknown"),
            "can_launch_browser": codex_browser,
            "browser_probe": (probe.get("codex") or {}).get("detail", ""),
        },
        "thresholds": {"five_hour_pct": a.five_hour, "seven_day_pct": a.seven_day},
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

    print(f"\nWrote {tilde(CONFIG)}")
    if b:
        print(f"Backed up the primary settings file to {tilde(b)}")
    print(f"  primary   {cfg['primary']['account']}   ({cfg['primary']['config_dir']})")
    if a.secondary:
        print(f"  secondary {cfg['secondary']['account']}   ({cfg['secondary']['config_dir']})")
    else:
        print("  secondary none (Codex only)")
    print(f"  codex     {'on, ' + cfg['codex']['account'] if codex_on else 'off'}")
    if codex_on and codex_browser is not True:
        print("  note: codex cannot launch a browser in its sandbox, so browser work")
        print("        will never be delegated to it. This is expected, not a fault.")
    print(f"  failover  {'on' if cfg['failover']['enabled'] else 'off'} "
          f"at {a.five_hour}% of the 5-hour window and {a.seven_day}% of the weekly window")
    print("\nRestart Claude Code. Both the status bar and the automatic handover stay")
    print("inert until you do. Then run:  setup.py --check")

def cmd_show():
    if not os.path.exists(CONFIG):
        sys.exit("second-wind: not set up yet. Run setup.py --discover first.")
    print(json.dumps(load_cfg(), indent=2))

def cmd_check():
    """Answer the one question the tool cannot answer for itself: is the
    automatic handover actually armed? Every link in that chain fails silently by
    design, so without this there is no way to tell working from broken."""
    if not os.path.exists(CONFIG):
        print("NOT SET UP. Run: setup.py --discover"); return
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

    fo = cfg.get("failover", {}).get("enabled", True)
    print(f"failover        {'on' if fo else 'OFF in config'}")
    if not fo:
        ok = False
    if os.path.exists(os.path.join(SW_HOME, "no-failover")):
        print("                OFF: ~/.second-wind/no-failover exists"); ok = False

    t5 = cfg["thresholds"]["five_hour_pct"]; t7 = cfg["thresholds"]["seven_day_pct"]
    print(f"thresholds      5h {t5}%   7d {t7}%")

    primary_settings = settings_path(cfg["primary"]["config_dir"])
    sl_cmd = os.path.join(HERE, "statusline.sh")
    guard_cmd = os.path.join(HERE, "usage-guard.sh")
    try:
        with open(primary_settings) as f:
            settings = json.load(f)
        status_installed = settings.get("statusLine", {}).get("command") == sl_cmd
        prompt_hooks = settings.get("hooks", {}).get("UserPromptSubmit", [])
        guard_installed = any(
            hook.get("command") == guard_cmd
            for group in prompt_hooks if isinstance(group, dict)
            for hook in (group.get("hooks") or []) if isinstance(hook, dict)
        )
        print(f"status line      {'installed' if status_installed else 'MISSING'}")
        print(f"usage guard      {'installed' if guard_installed else 'MISSING'}")
        if not status_installed or not guard_installed:
            print(f"                Check {tilde(primary_settings)} or run --write again.")
            ok = False
    except Exception as e:
        print(f"settings         UNREADABLE: {tilde(primary_settings)}: {e}")
        ok = False

    usage = os.path.join(SW_HOME, "usage-primary.json")
    if not os.path.exists(usage):
        print("reading         NONE YET")
        print("                Only a terminal status line writes this cache. The desktop app")
        print("                does not run status lines, so desktop-only use cannot arm")
        print("                automatic handover. In a terminal, restart Claude Code, send")
        print("                one message, then run this again. Manual delegation through")
        print("                scripts/run.sh still works in terminal and desktop workflows.")
        ok = False
    else:
        try:
            u = json.load(open(usage))
            age = int(time.time() - u.get("cached_at", 0))
            fresh = age <= 3600
            print(f"reading         5h {u.get('five_hour_pct') or '?'}%   "
                  f"7d {u.get('seven_day_pct') or '?'}%   "
                  f"({age // 60}m old{'' if fresh else ', TOO OLD, ignored'})")
            if not fresh:
                ok = False
        except Exception as e:
            print(f"reading         UNREADABLE: {e}"); ok = False

    print()
    print("ARMED: handover will fire when a threshold is crossed" if ok
          else "NOT ARMED: fix the failures shown above")

def cmd_uninstall():
    if os.path.exists(CONFIG):
        cfg = load_cfg()
        for k in ("primary", "secondary"):
            d = cfg.get(k, {}).get("config_dir")
            if d:
                merge_settings(d, install=False)
        print("Removed the status bar and usage guard from both profiles.")
        print(f"Config and logs are still at {tilde(SW_HOME)}; delete that folder to finish.")
    else:
        print("Nothing to uninstall.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--primary")
    ap.add_argument("--secondary")
    ap.add_argument("--codex", choices=["on", "off"], default="on")
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
    a = ap.parse_args()
    if a.discover: return cmd_discover()
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
