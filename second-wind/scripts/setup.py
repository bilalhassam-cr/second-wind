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
import argparse, json, os, shutil, subprocess, sys, time

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
            return None
    b = backup(p)

    sl = {"type": "command", "command": f"{HERE}/statusline.sh", "padding": 0}
    hook = {"hooks": [{"type": "command", "command": f"{HERE}/usage-guard.sh",
                       "timeout": 10, "statusMessage": "Checking usage headroom"}]}

    if install:
        data["statusLine"] = sl
        hooks = data.setdefault("hooks", {})
        ups = [h for h in hooks.get("UserPromptSubmit", [])
               if "usage-guard" not in json.dumps(h)]
        ups.append(hook)
        hooks["UserPromptSubmit"] = ups
    else:
        if isinstance(data.get("statusLine"), dict) and "second-wind" in json.dumps(data["statusLine"]):
            data.pop("statusLine", None)
        hooks = data.get("hooks", {})
        if "UserPromptSubmit" in hooks:
            hooks["UserPromptSubmit"] = [h for h in hooks["UserPromptSubmit"]
                                         if "usage-guard" not in json.dumps(h)]
            if not hooks["UserPromptSubmit"]:
                hooks.pop("UserPromptSubmit")
    with open(p, "w") as f:
        json.dump(data, f, indent=2)
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

    prim, sec = look(a.primary), look(a.secondary)
    if tilde(expand(a.primary)) == tilde(expand(a.secondary)):
        sys.exit("second-wind: primary and secondary cannot be the same profile.")
    if not prim.get("logged_in"):
        print(f"  ! primary {a.primary} is not signed in. Sign it in first.", file=sys.stderr)
    if not sec.get("logged_in"):
        print(f"  ! secondary {a.secondary} is not signed in. Sign it in first.", file=sys.stderr)

    print("Probing what each worker can do (about 5 seconds)...")
    probe = json.loads(subprocess.run(
        [sys.executable, os.path.join(HERE, "probe.py")],
        capture_output=True, text=True).stdout or "{}")
    codex_browser = (probe.get("codex") or {}).get("can_launch_browser")

    codex_on = a.codex == "on" and prof.get("codex", {}).get("installed", False)
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
            "kind": "claude", "label": "secondary", "enabled": True,
            "config_dir": tilde(expand(a.secondary)),
            "account": sec.get("account", "unknown"),
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
    }
    os.makedirs(SW_HOME, exist_ok=True)
    os.makedirs(os.path.join(SW_HOME, "log"), exist_ok=True)
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(CONFIG, 0o600)

    b = merge_settings(cfg["primary"]["config_dir"], install=True)
    merge_settings(cfg["secondary"]["config_dir"], install=True)

    print(f"\nWrote {tilde(CONFIG)}")
    if b:
        print(f"Backed up the primary settings file to {tilde(b)}")
    print(f"  primary   {cfg['primary']['account']}   ({cfg['primary']['config_dir']})")
    print(f"  secondary {cfg['secondary']['account']}   ({cfg['secondary']['config_dir']})")
    print(f"  codex     {'on, ' + cfg['codex']['account'] if codex_on else 'off'}")
    if codex_on and codex_browser is False:
        print("  note: codex cannot launch a browser in its sandbox, so browser work")
        print("        will never be delegated to it. This is expected, not a fault.")
    print(f"  failover  {'on' if cfg['failover']['enabled'] else 'off'} "
          f"at {a.five_hour}% of the 5-hour window and {a.seven_day}% of the weekly window")
    print("\nRestart Claude Code for the status bar to appear.")

def cmd_show():
    if not os.path.exists(CONFIG):
        sys.exit("second-wind: not set up yet. Run setup.py --discover first.")
    print(json.dumps(load_cfg(), indent=2))

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
    ap.add_argument("--timeout", type=int, default=600,
                    help="seconds before a delegated call is killed (default 600)")
    a = ap.parse_args()
    if a.discover: return cmd_discover()
    if a.show: return cmd_show()
    if a.uninstall: return cmd_uninstall()
    if a.write:
        if not (a.primary and a.secondary):
            sys.exit("second-wind: --write needs --primary and --secondary")
        return cmd_write(a)
    ap.print_help()

if __name__ == "__main__":
    main()
