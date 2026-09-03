#!/usr/bin/env python3
"""Write the second-wind config and wire it into the Claude profiles.

Two steps on purpose. `--detect` reports what is on the machine; a human then
chooses which account is primary. A script cannot infer that choice: primary is
wherever the person actually works and keeps their history, and getting it
backwards silently sends their main work to the wrong subscription.

  setup.py --detect
  setup.py --write --primary ~/.claude --secondary ~/.claude-secondary
      --level reviewer|worker|relief [--reader ~/.claude-usage]
      [--codex on|off] [--grok on|off] [--cursor on|off]
      [--five-hour N] [--seven-day N] [--refresh-minutes N]
      [--model-picker on|off] [--picker-routes id,id] [--no-launchd]
      [--timeout N] [--force]
  setup.py --accounts [--live]
  setup.py --check
  setup.py --show
  setup.py --uninstall
"""
import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swlib
from swlib import expand, tilde

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_DIR = os.path.join(HERE, "hooks")
HOME = swlib.HOME

LAUNCHD_LABEL = "com.second-wind.refresh"
LAUNCHD_PLIST = os.path.join(HOME, "Library", "LaunchAgents", LAUNCHD_LABEL + ".plist")

# event -> (hook file, timeout seconds, status message, matcher)
# The matcher is what stops a hook firing on every unrelated event. StopFailure
# fires on any stop reason, and Notification on every notification Claude Code
# raises, so both are narrowed to the ones that mean the allowance ran out.
HOOK_FILES = {
    "SessionStart": ("session-start.py", 10, "Reading the usage cache", ""),
    "UserPromptSubmit": ("prompt-guard.py", 10, "Checking usage headroom", ""),
    "StopFailure": ("stop-failure.py", 10, "Checking for a rate limit",
                    "rate_limit"),
    "Notification": ("notification.py", 10, "Checking a usage notification",
                     "quota_auto_resume_fired|quota_auto_resume_stale|"
                     "quota_auto_resume_disabled"),
    "PostModelSwitch": ("model-switch.py", 10, "Refreshing the usage reading", ""),
    # No matcher: our routing ids have no canonical model name, and Claude Code
    # runs every PreModelSwitch hook when it cannot canonicalise a target
    # anyway. The hook checks to_model itself.
    "PreModelSwitch": ("model-route.py", 10, "Checking the routing target", ""),
}

# PreModelSwitch is at every level, including reviewer: routing a task to
# another account is the one thing every level can do, so the picker rows that
# start it belong everywhere.
LEVELS = {
    "reviewer": {"mode": "review", "failover": False,
                 "events": ("SessionStart", "PreModelSwitch")},
    "worker": {"mode": "work", "failover": False,
               "events": ("SessionStart", "StopFailure", "Notification",
                          "PostModelSwitch", "PreModelSwitch")},
    "relief": {"mode": "work", "failover": True,
               "events": ("SessionStart", "StopFailure", "Notification",
                          "PostModelSwitch", "PreModelSwitch",
                          "UserPromptSubmit")},
}

# Names that have ever belonged to second-wind. Any settings entry pointing at
# one of these is ours to remove, wherever an older version installed it.
LEGACY_BASENAMES = {"usage-guard.sh", "statusline.sh", "session-brief.sh",
                    "usage-refresh-hook.sh"}
HOOK_BASENAMES = {entry[0] for entry in HOOK_FILES.values()}


def sw_home():
    return swlib.sw_home()


def config_file():
    return swlib.config_path()


def workdir_path():
    return os.path.join(sw_home(), "workdir")


def in_temp_home():
    """A test run points SW_HOME at a temporary directory. Do not touch the
    real launchd domain in that case."""
    real = os.path.realpath(sw_home())
    return real.startswith("/tmp/") or real.startswith("/private/tmp/")


def settings_path(config_dir):
    return os.path.join(expand(config_dir), "settings.json")


def claude_metadata_path(config_dir):
    """The default profile keeps its project list in ~/.claude.json; any other
    profile keeps its own copy inside the config directory."""
    d = expand(config_dir)
    if os.path.realpath(d) == os.path.realpath(os.path.join(HOME, ".claude")):
        return os.path.join(HOME, ".claude.json")
    return os.path.join(d, ".claude.json")


def discover():
    out = subprocess.run([sys.executable, os.path.join(HERE, "discover.py")],
                         capture_output=True, text=True).stdout
    try:
        return json.loads(out or "{}")
    except ValueError:
        return {}


# ---------------------------------------------------------------- settings


def hook_command(event):
    return os.path.join(HOOK_DIR, HOOK_FILES[event][0])


def statusline_command():
    return os.path.join(HERE, "statusline.sh")


def is_ours(command):
    """True for a command second-wind installed, in this version or an older
    one. Matched on the exact paths this install writes, on our own hook
    basenames inside a directory called hooks, and on the basenames older
    versions used. Never on a substring: a rule that removed any .py or .sh
    living under a path containing "second-wind" also removed somebody's own
    second-wind-notes.sh, and uninstall is not the place to guess."""
    if not isinstance(command, str) or not command.strip():
        return False
    ours = {statusline_command()}
    ours.update(hook_command(event) for event in HOOK_FILES)
    # scan every token, because an entry may name an interpreter first
    for token in command.split():
        if token in ours:
            return True
        base = os.path.basename(token)
        parent = os.path.basename(os.path.dirname(token))
        if base in LEGACY_BASENAMES:
            return True
        if parent == "hooks" and base in HOOK_BASENAMES:
            return True
    return False


def strip_ours(hooks):
    """Remove our entries from a settings hooks block, in place."""
    for event in list(hooks.keys()):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept = []
        for group in groups:
            if not isinstance(group, dict):
                kept.append(group)
                continue
            inner = [h for h in (group.get("hooks") or [])
                     if not (isinstance(h, dict) and is_ours(h.get("command", "")))]
            if inner:
                copy = dict(group)
                copy["hooks"] = inner
                kept.append(copy)
            elif not group.get("hooks"):
                kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event)


def read_settings(config_dir):
    path = settings_path(config_dir)
    if not os.path.exists(path):
        return {}, path
    try:
        with open(path) as handle:
            data = json.load(handle)
        return (data if isinstance(data, dict) else {}), path
    except Exception as exc:
        print("  ! %s is not valid JSON (%s). Leaving it alone."
              % (tilde(path), exc), file=sys.stderr)
        return None, path


def replaced_store():
    return os.path.join(sw_home(), "replaced-statusline.json")


def merge_settings(config_dir, events=(), statusline=False, model_picker=None):
    """Install or remove our own keys, leaving every other setting alone.

    We never rewrite a settings file wholesale: it is the user's, and it usually
    holds hooks and plugins we know nothing about. Passing no events and no
    status line is the uninstall path.
    """
    data, path = read_settings(config_dir)
    if data is None:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _, stamped = swlib.backup_once(path)

    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    strip_ours(hooks)
    for event in events:
        _, timeout, message, matcher = HOOK_FILES[event]
        entry = {"hooks": [{"type": "command", "command": hook_command(event),
                            "timeout": timeout, "statusMessage": message}]}
        if matcher:
            entry["matcher"] = matcher
        hooks.setdefault(event, []).append(entry)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)

    sl = {"type": "command", "command": statusline_command(), "padding": 0}
    if statusline:
        existing = data.get("statusLine")
        if existing and existing != sl:
            store = {}
            if os.path.exists(replaced_store()):
                try:
                    with open(replaced_store()) as handle:
                        store = json.load(handle)
                except Exception:
                    store = {}
            store[tilde(expand(config_dir))] = existing
            swlib.write_json_atomic(replaced_store(), store)
            print("  note: replaced an existing status line in %s. "
                  "Uninstall restores it." % tilde(path))
        data["statusLine"] = sl
    elif isinstance(data.get("statusLine"), dict) and \
            data["statusLine"].get("command") == statusline_command():
        restored = None
        if os.path.exists(replaced_store()):
            try:
                with open(replaced_store()) as handle:
                    store = json.load(handle)
                restored = store.get(tilde(expand(config_dir))) or store.get(config_dir)
            except Exception:
                restored = None
        if restored:
            data["statusLine"] = restored
            print("  restored the previous status line in %s" % tilde(path))
        else:
            data.pop("statusLine", None)

    # We never create the modelPicker key. model-picker.py owns it and writes it
    # with our marker on the first refresh, so setup writing a placeholder would
    # only put an empty object in the user's settings for no gain.
    if model_picker is False:
        picker = data.get("modelPicker")
        if isinstance(picker, dict) and picker.get("_second_wind") is True:
            data.pop("modelPicker", None)

    swlib.write_json_atomic(path, data, mode=0o600)
    return stamped or True


# ---------------------------------------------------------------- trust


def trust_claude(config_dir, folder):
    """Mark our own working directory trusted for a Claude profile.

    This is a setting the user asked for at setup, on a directory second-wind
    creates and owns. No reader ever answers a trust dialog: the dialog is what
    this prevents, and a reader that meets one stops and reports.
    """
    path = claude_metadata_path(config_dir)
    data = {}
    if os.path.exists(path):
        try:
            with open(path) as handle:
                data = json.load(handle)
        except Exception as exc:
            return "could not read %s (%s)" % (tilde(path), exc)
        if not isinstance(data, dict):
            return "%s is not a JSON object" % tilde(path)
    projects = data.get("projects")
    if not isinstance(projects, dict):
        projects = {}
    entry = projects.get(folder)
    if not isinstance(entry, dict):
        entry = {}
    if entry.get("hasTrustDialogAccepted") is True:
        return "already trusted"
    entry["hasTrustDialogAccepted"] = True
    projects[folder] = entry
    data["projects"] = projects
    if os.path.exists(path):
        swlib.backup_once(path)
    swlib.write_json_atomic(path, data, mode=0o600)
    return "trusted"


def trust_codex(folder):
    """Append a trust entry to the Codex config, if it is not there already.

    Codex 0.152 shows a trust modal on launch in an unknown directory, which
    stops the reader before it can ask for /status.
    """
    path = os.path.join(HOME, ".codex", "config.toml")
    text = ""
    if os.path.exists(path):
        try:
            with open(path) as handle:
                text = handle.read()
        except Exception as exc:
            return "could not read %s (%s)" % (tilde(path), exc)
    header = '[projects."%s"]' % folder
    if header in text:
        return "already trusted"
    if os.path.exists(path):
        swlib.backup_once(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    block = "\n%s\ntrust_level = \"trusted\"\n" % header
    new = (text.rstrip("\n") + "\n" + block) if text.strip() else block.lstrip("\n")
    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(path),
                                   prefix=".second-wind-", suffix=".tmp")
    with os.fdopen(handle, "w") as out:
        out.write(new)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return "trusted"


def claude_trusted(config_dir, folder):
    """Whether this Claude profile's project list still trusts our workdir.

    Worth checking rather than assuming: a Claude Code session that was already
    running when setup wrote the file keeps its own copy of the project list and
    can write it back on exit, which quietly drops our entry.
    """
    path = claude_metadata_path(config_dir)
    try:
        with open(path) as handle:
            data = json.load(handle)
    except Exception:
        return False
    entry = (data.get("projects") or {}).get(folder)
    return isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True


def codex_trusted(folder):
    path = os.path.join(HOME, ".codex", "config.toml")
    try:
        with open(path) as handle:
            return ('[projects."%s"]' % folder) in handle.read()
    except Exception:
        return False


# ---------------------------------------------------------------- launchd


def launchd_plist(interval_seconds):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
           "HOME": HOME}
    if os.environ.get("SW_HOME"):
        env["SW_HOME"] = os.environ["SW_HOME"]
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": ["/bin/sh", os.path.join(HERE, "usage-refresh.sh"),
                             "--if-claude-running"],
        "StartInterval": int(interval_seconds),
        "RunAtLoad": False,
        "EnvironmentVariables": env,
        "StandardErrorPath": os.path.join(sw_home(), "log", "launchd.err"),
    }


def launchd_install(interval_seconds):
    """Write the agent and load it. Returns a line to print."""
    if sys.platform != "darwin":
        minutes = max(1, int(interval_seconds) // 60)
        return ("no launchd on this platform. Add this cron line instead:\n"
                "  */%d * * * * %s --if-claude-running"
                % (minutes, os.path.join(HERE, "usage-refresh.sh")))
    os.makedirs(os.path.dirname(LAUNCHD_PLIST), exist_ok=True)
    os.makedirs(os.path.join(sw_home(), "log"), exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(LAUNCHD_PLIST),
                                   prefix=".second-wind-", suffix=".plist")
    with os.fdopen(handle, "wb") as out:
        plistlib.dump(launchd_plist(interval_seconds), out)
    os.chmod(tmp, 0o644)
    os.replace(tmp, LAUNCHD_PLIST)
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (uid, LAUNCHD_LABEL)],
                   capture_output=True, text=True)
    done = subprocess.run(["launchctl", "bootstrap", "gui/%d" % uid, LAUNCHD_PLIST],
                          capture_output=True, text=True)
    if done.returncode == 0:
        return "installed and loaded (every %d minutes)" % (int(interval_seconds) // 60)
    fallback = subprocess.run(["launchctl", "load", "-w", LAUNCHD_PLIST],
                              capture_output=True, text=True)
    if fallback.returncode == 0:
        return "installed and loaded through launchctl load"
    detail = (done.stderr or done.stdout or "").strip().splitlines()
    return ("written to %s but not loaded: %s. Load it with: launchctl bootstrap "
            "gui/$(id -u) %s" % (tilde(LAUNCHD_PLIST),
                                 detail[0] if detail else "unknown error",
                                 tilde(LAUNCHD_PLIST)))


def launchd_loaded():
    if sys.platform != "darwin":
        return None
    uid = os.getuid()
    done = subprocess.run(["launchctl", "print", "gui/%d/%s" % (uid, LAUNCHD_LABEL)],
                          capture_output=True, text=True)
    if done.returncode == 0:
        return True
    done = subprocess.run(["launchctl", "list", LAUNCHD_LABEL],
                          capture_output=True, text=True)
    return done.returncode == 0


def launchd_remove():
    if sys.platform != "darwin":
        return "nothing to remove"
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (uid, LAUNCHD_LABEL)],
                   capture_output=True, text=True)
    subprocess.run(["launchctl", "unload", LAUNCHD_PLIST], capture_output=True, text=True)
    if os.path.exists(LAUNCHD_PLIST):
        os.unlink(LAUNCHD_PLIST)
        return "unloaded and deleted %s" % tilde(LAUNCHD_PLIST)
    return "no launchd agent was installed"


# ---------------------------------------------------------------- detect


def detection():
    found = discover()
    if swlib.is_configured():
        found["config"] = swlib.load_config() or {
            "error": "could not read %s" % tilde(config_file())}
    else:
        found["config"] = None
    return found


def cmd_detect():
    print(json.dumps(detection(), indent=2))


def cmd_show():
    if not swlib.is_configured():
        sys.exit("second-wind: not set up yet. Run setup.py --detect first.")
    print(json.dumps(swlib.load_config(), indent=2))


# ---------------------------------------------------------------- write


def cmd_write(a):
    if not swlib.has_jq():
        sys.exit("second-wind: jq is not on PATH. The status line and the refresh "
                 "script both need it. Install jq, then run --write again.")
    previous = swlib.load_config()
    found = discover()
    by_dir = {p["config_dir"]: p for p in found.get("claude_profiles", [])}

    def look(d):
        if not d:
            return {}
        return by_dir.get(tilde(expand(d))) or by_dir.get(d) or {}

    # A second --write used to reset every tuning value to its argparse default,
    # so re-running the command to change one profile quietly moved the timeout
    # and both thresholds back. Anything not passed on the command line now
    # carries over from the config that is already there.
    settings_changed = []

    def carry(attr, previous_value, constant, label):
        chosen = getattr(a, attr)
        if chosen is None:
            chosen = previous_value if previous_value is not None else constant
        setattr(a, attr, chosen)
        if previous_value is not None and chosen != previous_value:
            settings_changed.append("%s %s to %s" % (label, previous_value, chosen))

    prev_thresholds = previous.get("thresholds") if isinstance(
        previous.get("thresholds"), dict) else {}
    prev_refresh = previous.get("refresh") if isinstance(
        previous.get("refresh"), dict) else {}
    prev_picker = prev_refresh.get("model_picker")
    carry("five_hour", prev_thresholds.get("five_hour_pct"), 90, "five-hour threshold")
    carry("seven_day", prev_thresholds.get("seven_day_pct"), 80, "seven-day threshold")
    carry("refresh_minutes", prev_refresh.get("interval_minutes"), 15,
          "refresh interval")
    carry("timeout", previous.get("timeout_seconds"), 600, "timeout")
    carry("model_picker",
          ("on" if prev_picker else "off") if isinstance(prev_picker, bool) else None,
          "off", "model picker")
    # Normalised before the comparison, so `a, b` and `a,b` are not reported as
    # a change on every rerun.
    if a.picker_routes is not None:
        a.picker_routes = ",".join(entry.strip()
                                   for entry in a.picker_routes.split(",")
                                   if entry.strip())
    prev_routes = (previous.get("picker") or {}).get("routes")
    carry("picker_routes",
          ",".join(prev_routes) if isinstance(prev_routes, list) else None,
          "", "picker routes")

    for name, value in (("--five-hour", a.five_hour), ("--seven-day", a.seven_day)):
        if not isinstance(value, int) or not 1 <= value <= 100:
            sys.exit("second-wind: %s must be between 1 and 100" % name)
    if not isinstance(a.timeout, int) or a.timeout < 30:
        sys.exit("second-wind: --timeout must be at least 30 seconds")
    if not isinstance(a.refresh_minutes, int) or not 1 <= a.refresh_minutes <= 1440:
        sys.exit("second-wind: --refresh-minutes must be between 1 and 1440")
    if a.model_picker not in ("on", "off"):
        sys.exit("second-wind: --model-picker must be on or off")
    routes = [entry.strip() for entry in (a.picker_routes or "").split(",")
              if entry.strip()]
    for entry in routes:
        if not swlib.parse_route(entry):
            sys.exit("second-wind: --picker-routes takes ids like "
                     "second-wind/codex/gpt-5.6/high. %s is not one." % entry)

    dirs = {"primary": a.primary, "secondary": a.secondary, "reader": a.reader}
    for role, folder in dirs.items():
        if role != "primary" and folder and not os.path.isdir(expand(folder)):
            sys.exit("second-wind: %s does not exist. Create and sign in to the "
                     "profile first: see references/setup.md. Refusing to write "
                     "settings into a directory that is not a Claude profile."
                     % folder)
    if not os.path.isdir(expand(a.primary)):
        sys.exit("second-wind: %s does not exist." % a.primary)
    seen = {}
    for role, folder in dirs.items():
        if not folder:
            continue
        real = os.path.realpath(expand(folder))
        if real in seen:
            sys.exit("second-wind: %s and %s resolve to the same directory."
                     % (seen[real], role))
        seen[real] = role

    prim = look(a.primary)
    sec = look(a.secondary)
    rdr = look(a.reader)
    if not prim.get("logged_in"):
        print("  ! primary %s does not report a terminal login. The desktop app "
              "can be signed in while the CLI check says otherwise, so this is a "
              "warning, not a refusal." % a.primary, file=sys.stderr)
    for role, folder, info in (("secondary", a.secondary, sec),
                               ("reader", a.reader, rdr)):
        if folder and not info.get("logged_in"):
            if not a.force:
                sys.exit("second-wind: %s %s is not signed in, so it would fail "
                         "every time it was used. Sign it in first, or pass "
                         "--force." % (role, folder))
            print("  ! %s %s is not signed in (forced)." % (role, folder),
                  file=sys.stderr)

    def selected(name, state):
        info = found.get(name) or {}
        installed = info.get("installed") is True
        logged = info.get("logged_in") is True
        if state == "on" and not installed:
            sys.exit("second-wind: --%s on was requested, but its command is not "
                     "on PATH." % name)
        if state == "on" and not logged:
            sys.exit("second-wind: --%s on was requested, but it is not signed in."
                     % name)
        if state == "off":
            return False
        return installed and logged

    codex_on = selected("codex", a.codex)
    grok_on = selected("grok", a.grok)
    cursor_on = selected("cursor", a.cursor)
    if not any((a.secondary, codex_on, grok_on, cursor_on)):
        sys.exit("second-wind: nothing to delegate to. Connect at least one "
                 "worker first.")
    cursor_reads = bool(cursor_on and (found.get("cursor") or {}).get("can_read_usage"))

    level = LEVELS[a.level]
    versions = swlib.client_versions()
    launchd_on = (not a.no_launchd and sys.platform == "darwin"
                  and not in_temp_home())
    folder = workdir_path()
    os.makedirs(sw_home(), exist_ok=True)
    os.makedirs(os.path.join(sw_home(), "log"), exist_ok=True)
    os.makedirs(folder, exist_ok=True)
    # the log holds whole prompts and replies, so keep the tree private
    os.chmod(sw_home(), 0o700)
    os.chmod(os.path.join(sw_home(), "log"), 0o700)
    os.chmod(folder, 0o700)

    cfg = {
        "version": swlib.CONFIG_VERSION,
        "created": time.strftime("%Y-%m-%d"),
        "level": a.level,
        "primary": {
            "kind": "claude", "label": "primary",
            "config_dir": tilde(expand(a.primary)),
            "account": prim.get("account", "unknown"),
            "plan": prim.get("subscription") or "unknown",
            "is_default_dir": prim.get("is_default_dir", False),
        },
        "secondary": {
            "kind": "claude", "label": "secondary",
            "enabled": bool(a.secondary),
            "config_dir": tilde(expand(a.secondary)) if a.secondary else "",
            "account": sec.get("account", "unknown") if a.secondary else "",
            "plan": (sec.get("subscription") or "unknown") if a.secondary else "",
            "is_default_dir": sec.get("is_default_dir", False),
        },
        "reader": {
            "kind": "claude", "label": "reader",
            "enabled": bool(a.reader),
            "config_dir": tilde(expand(a.reader)) if a.reader else "",
            "account": rdr.get("account", "unknown") if a.reader else "",
            "read_for": "primary",
        },
        "codex": {
            "kind": "codex", "label": "codex", "enabled": codex_on,
            "account": (found.get("codex") or {}).get("account", "unknown"),
            "plan": "unknown",
        },
        "grok": {
            "kind": "grok", "label": "grok", "enabled": grok_on,
            "account": "unknown", "plan": "SuperGrok or X Premium+",
        },
        "cursor": {
            "kind": "cursor", "label": "cursor", "enabled": cursor_on,
            "account": (found.get("cursor") or {}).get("account", "unknown"),
            "plan": "Cursor Pro",
            "auth": (found.get("cursor") or {}).get("auth", "none"),
        },
        "thresholds": {"five_hour_pct": a.five_hour, "seven_day_pct": a.seven_day},
        "refresh": {
            "interval_minutes": a.refresh_minutes,
            "workdir": tilde(folder),
            "launchd": launchd_on,
            "model_picker": a.model_picker == "on",
            "cursor": cursor_reads,
        },
        "picker": {"routes": routes},
        "failover": {"enabled": level["failover"], "announce": True},
        "defaults": {"mode": level["mode"]},
        "log": {
            "dir": tilde(os.path.join(sw_home(), "log")),
            "max_exchange_kb": 200,
            "prune_days": 30,
        },
        "timeout_seconds": a.timeout,
        "tested_versions": versions,
        # where the skill lives, so SKILL.md can find its own scripts. Relative
        # paths do not work: a Bash tool call runs in the user's project, not here.
        "skill_dir": tilde(os.path.dirname(HERE)),
    }
    swlib.write_json_atomic(config_file(), cfg)

    print("\nWrote %s" % tilde(config_file()))
    if settings_changed:
        print("  changed   %s" % "; ".join(settings_changed))
    print("  level     %s (mode %s, automatic handover %s)"
          % (a.level, level["mode"], "on" if level["failover"] else "off"))
    print("  primary   %s   (%s)" % (cfg["primary"]["account"],
                                     cfg["primary"]["config_dir"]))
    if a.secondary:
        print("  secondary %s   (%s)" % (cfg["secondary"]["account"],
                                         cfg["secondary"]["config_dir"]))
    else:
        print("  secondary off")
    if a.reader:
        print("  reader    %s   (%s)" % (cfg["reader"]["account"],
                                         cfg["reader"]["config_dir"]))
    print("  codex     %s" % ("on, " + cfg["codex"]["account"] if codex_on else "off"))
    print("  grok      %s" % ("on" if grok_on else "off"))
    if cursor_on:
        print("  cursor    on, %s (%s sign-in, usage reading %s)"
              % (cfg["cursor"]["account"], cfg["cursor"]["auth"],
                 "on" if cursor_reads else "off, an API key cannot read the panel"))
    else:
        print("  cursor    off")

    print("\nWorking directory for the readers: %s" % tilde(folder))
    print("  claude %s: %s" % (cfg["primary"]["config_dir"],
                               trust_claude(a.primary, folder)))
    if a.secondary:
        print("  claude %s: %s" % (cfg["secondary"]["config_dir"],
                                   trust_claude(a.secondary, folder)))
    if a.reader:
        print("  claude %s: %s" % (cfg["reader"]["config_dir"],
                                   trust_claude(a.reader, folder)))
    if codex_on:
        print("  codex: %s" % trust_codex(folder))

    print("\nProfile settings:")
    # model_picker is passed on every write, not only when it is on: --model-picker
    # off has to take a marked modelPicker key back out again, and omitting the
    # argument left the row in place for as long as the profile existed.
    result = merge_settings(cfg["primary"]["config_dir"], events=level["events"],
                           statusline=True,
                           model_picker=(a.model_picker == "on"))
    if result is False:
        sys.exit("second-wind: could not install the primary hooks and status "
                 "line. Fix its settings.json and run --write again.")
    print("  primary   %s hooks and the status line installed"
          % ", ".join(level["events"]))
    if a.secondary:
        guard = ("UserPromptSubmit",) if "UserPromptSubmit" in level["events"] else ()
        if merge_settings(cfg["secondary"]["config_dir"], events=guard,
                          statusline=True) is False:
            sys.exit("second-wind: could not install the secondary status line. "
                     "Fix its settings.json and run --write again.")
        print("  secondary status line%s installed"
              % (" and the guard" if guard else ""))
    for stale_dir in {(previous.get(role) or {}).get("config_dir")
                      for role in ("primary", "secondary", "reader")} | \
            {cfg["reader"]["config_dir"]}:
        if not stale_dir:
            continue
        current = {cfg["primary"]["config_dir"], cfg["secondary"]["config_dir"]}
        if stale_dir in current or not os.path.isdir(expand(stale_dir)):
            continue
        merge_settings(stale_dir, model_picker=False)
        print("  %s cleared of second-wind entries" % stale_dir)

    print("\nBackground refresh:")
    if a.no_launchd or in_temp_home():
        print("  skipped. Run %s yourself, or rerun --write without --no-launchd."
              % tilde(os.path.join(HERE, "usage-refresh.sh")))
    else:
        print("  %s" % launchd_install(a.refresh_minutes * 60))

    print("\nTested against: %s"
          % ", ".join("%s %s" % (name, version or "not installed")
                      for name, version in versions.items()))
    missing = [event for event in level["events"]
               if not os.path.exists(hook_command(event))]
    if missing:
        print("\n  ! these hook files are not present yet: %s"
              % ", ".join(HOOK_FILES[event][0] for event in missing))
    print("\nRestart Claude Code before relying on the pre-trusted working "
          "directory: a running session holds its own copy of the project list "
          "and can write it back over ours on exit.")
    print("Then run:  setup.py --check  and  setup.py --accounts")


# ---------------------------------------------------------------- accounts


def reset_text(value):
    if value in (None, ""):
        return "unknown"
    try:
        return time.strftime("%d %b %H:%M", time.localtime(int(float(value))))
    except (TypeError, ValueError, OverflowError):
        return str(value).strip()


def window_text(used, reset):
    pct = swlib.number(used)
    return "%.0f%% / %s" % (pct, reset_text(reset)) if pct is not None else "unknown"


def limits_text(role, usage):
    if not usage:
        return "unknown"
    extra = usage.get("extra") or {}
    if role == "cursor":
        parts = []
        for label, key in (("Included", "included_pct"), ("Auto", "auto_pct"),
                           ("API", "api_pct")):
            value = swlib.pool(usage, key)
            parts.append("%s %.0f%%" % (label, value) if value is not None
                         else "%s unknown" % label)
        resets = extra.get("resets") or usage.get("resets")
        if resets:
            parts.append("resets %s" % resets)
        return "; ".join(parts)
    if role == "grok":
        return "week %s" % window_text(usage.get("seven_day_pct"),
                                       usage.get("seven_day_resets_at")
                                       or usage.get("seven_day_resets"))
    return ("5h %s; week %s"
            % (window_text(usage.get("five_hour_pct"),
                           usage.get("five_hour_resets_at")
                           or usage.get("five_hour_resets")),
               window_text(usage.get("seven_day_pct"),
                           usage.get("seven_day_resets_at")
                           or usage.get("seven_day_resets"))))


def account_row(role, cfg):
    profile = cfg.get(role) or {}
    enabled = role in swlib.enabled_roles(cfg)
    usage = swlib.load_usage(role)
    state = swlib.freshness(role, cfg=cfg)
    kind = swlib.status_kind(role)
    # "none" means no status file, which is the ordinary case: statusline.sh
    # writes a reading without writing a status. Only the four real faults
    # override what the reading itself says.
    problem = kind in ("login", "trust", "parser", "failed")
    blocking = kind in ("login", "trust", "parser")
    live = state in ("fresh", "stale") and not blocking
    head = swlib.headroom(role, usage) if live else None
    if not enabled:
        words, head = "disabled", None
        limits, age, version = "unknown", "unknown", "unknown"
    elif not swlib.reading_enabled(role, cfg):
        words, head = "no usage reading", None
        limits, age, version = "not readable for this sign-in", "unknown", \
            usage.get("client_version") or "unknown"
    else:
        words = swlib.status_words(kind) if problem else (
            "ready" if state == "fresh" else
            ("reading is stale" if state == "stale" else "no current reading"))
        limits = limits_text(role, usage) if live else "unknown"
        age = swlib.short_age(swlib.cache_age(role)) if usage else "unknown"
        version = usage.get("client_version") or "unknown"
    return {
        "role": role,
        "account": usage.get("account") or profile.get("account") or "unknown",
        "plan": usage.get("plan") or profile.get("plan") or "unknown",
        "limits": limits,
        "age": age,
        "version": version,
        "headroom": head,
        "status": words,
        "eligible": enabled,
    }


def cmd_accounts(a):
    if not swlib.is_configured():
        print("NOT SET UP. Run: setup.py --detect")
        return
    cfg = swlib.load_config()
    if a.live:
        script = os.path.join(HERE, "usage-refresh.sh")
        try:
            subprocess.run(["/bin/sh", script, "--force"], timeout=120,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            note = "Readers were run just now."
        except subprocess.TimeoutExpired:
            note = "The refresh did not finish inside 120 seconds. Figures below are cached."
        except OSError as exc:
            note = "The refresh could not start (%s). Figures below are cached." % exc
    else:
        note = ("Cached figures. Nothing was spawned. Add --live to run the readers "
                "first.")

    rows = [account_row(role, cfg) for role in swlib.ROLES]
    order = {role: i for i, role in enumerate(swlib.ROLES)}
    rows.sort(key=lambda row: (row["headroom"] is None,
                               -(row["headroom"] or 0),
                               order[row["role"]]))
    pick = next((row for row in rows
                 if row["eligible"] and row["headroom"] is not None), None)
    if pick:
        print("Work should go to: %s (%s)" % (pick["role"], pick["account"]))
    else:
        print("Work destination: unknown until an eligible account has a readable "
              "limit.")
    print(note)
    print("Headroom uses the strictest window the client reported: 5h and weekly "
          "for Claude and Codex, whichever of them the panel printed, weekly for "
          "Grok, monthly pools for Cursor. Unknown sorts last.")

    headers = ("ROLE", "ACCOUNT", "PLAN", "LIMITS", "AGE", "VERSION", "HEADROOM",
               "STATUS")
    values = [(row["role"], row["account"], row["plan"], row["limits"], row["age"],
               row["version"],
               "%d%%" % row["headroom"] if row["headroom"] is not None else "unknown",
               row["status"]) for row in rows]
    widths = [max(len(headers[i]), *(len(str(row[i])) for row in values))
              for i in range(len(headers) - 1)]

    def line(row):
        left = "  ".join(str(row[i]).ljust(widths[i]) for i in range(len(widths)))
        return "%s  %s" % (left, row[-1])

    print(line(headers))
    print(line(tuple("-" * len(header) for header in headers)))
    for row in values:
        print(line(row))


# ---------------------------------------------------------------- check


def cmd_check():
    """Answer the one question the tool cannot answer for itself: is this
    actually wired up? Every link in the chain fails silently by design, so
    without this there is no way to tell working from broken."""
    if not swlib.is_configured():
        print("NOT SET UP. Run: setup.py --detect")
        return
    cfg = swlib.load_config()
    level_name = cfg.get("level") or ("relief" if (cfg.get("failover") or {})
                                      .get("enabled") else "worker")
    level = LEVELS.get(level_name, LEVELS["worker"])
    faults, warnings = [], []

    def row(label, value):
        print("%-28s%s" % (label, value))

    row("config", tilde(config_file()))
    row("version", str(cfg.get("version", "unknown")))
    if cfg.get("version") != swlib.CONFIG_VERSION:
        faults.append("config is version %s, this build writes version %d. Rerun "
                      "--write." % (cfg.get("version", "unknown"),
                                    swlib.CONFIG_VERSION))
    row("level", "%s (mode %s, automatic handover %s)"
        % (level_name, (cfg.get("defaults") or {}).get("mode", "unknown"),
           "on" if (cfg.get("failover") or {}).get("enabled") else "off"))
    row("primary", "%s  (%s)" % ((cfg.get("primary") or {}).get("account", "unknown"),
                                 (cfg.get("primary") or {}).get("config_dir", "")))
    for role in ("secondary", "reader"):
        profile = cfg.get(role) or {}
        if profile.get("enabled"):
            row(role, "%s  (%s)" % (profile.get("account", "unknown"),
                                    profile.get("config_dir", "")))
        else:
            row(role, "off")
    for role in ("codex", "grok", "cursor"):
        profile = cfg.get(role) or {}
        if not profile.get("enabled"):
            row(role, "off")
            continue
        note = profile.get("account", "unknown")
        if role == "cursor" and not swlib.reading_enabled(role, cfg):
            note += "  (delegation only: this sign-in cannot read usage)"
        row(role, note)

    row("jq", "installed" if swlib.has_jq() else "MISSING")
    if not swlib.has_jq():
        faults.append("jq is not on PATH, so the status line and the refresh "
                      "script are inert.")
    for role, command in (("codex", "codex"), ("grok", "grok"),
                          ("cursor", "cursor-agent")):
        if (cfg.get(role) or {}).get("enabled"):
            found = shutil.which(command) is not None
            row(role + " command", "installed" if found else "MISSING")
            if not found:
                faults.append("%s is enabled but %s is not on PATH." % (role, command))

    folder = expand((cfg.get("refresh") or {}).get("workdir", ""))
    row("workdir", "%s%s" % (tilde(folder) or "not set",
                             "" if folder and os.path.isdir(folder) else "  MISSING"))
    if not folder or not os.path.isdir(folder):
        faults.append("the readers' working directory does not exist. Rerun --write.")
    elif folder:
        # A pre-trust that did not survive is the difference between a reader
        # that reads and one that sits on a trust modal, so check it, do not
        # assume it.
        for role in ("primary", "secondary", "reader"):
            profile = cfg.get(role) or {}
            if role != "primary" and not profile.get("enabled"):
                continue
            if claude_trusted(profile.get("config_dir", ""), folder):
                row(role + " trust", "trusted")
            else:
                row(role + " trust", "NOT TRUSTED: restart Claude Code then "
                                     "rerun --write, or open Claude Code once "
                                     "in the workdir")
                warnings.append("the %s profile does not trust the workdir, so "
                                "its reader will meet a trust prompt instead of "
                                "a usage panel." % role)
        if (cfg.get("codex") or {}).get("enabled"):
            if codex_trusted(folder):
                row("codex trust", "trusted")
            else:
                row("codex trust", "NOT TRUSTED: restart codex then rerun "
                                   "--write, or open codex once in the workdir")
                warnings.append("Codex does not trust the workdir, so its reader "
                                "will meet a trust modal instead of the status "
                                "panel.")

    print()
    for role in ("primary", "secondary"):
        profile = cfg.get(role) or {}
        if role == "secondary" and not profile.get("enabled"):
            continue
        data, path = read_settings(profile.get("config_dir", ""))
        if data is None:
            row(role + " settings", "UNREADABLE: %s" % tilde(path))
            faults.append("%s settings.json is not valid JSON." % role)
            continue
        installed = data.get("statusLine", {}).get("command") == statusline_command() \
            if isinstance(data.get("statusLine"), dict) else False
        row(role + " statusline", "installed" if installed else "MISSING")
        if not installed:
            faults.append("the %s status line is not installed. Rerun --write." % role)
        events = level["events"] if role == "primary" else \
            (("UserPromptSubmit",) if "UserPromptSubmit" in level["events"] else ())
        hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
        for event in events:
            command = hook_command(event)
            wired = any(inner.get("command") == command
                        for group in hooks.get(event, []) if isinstance(group, dict)
                        for inner in (group.get("hooks") or [])
                        if isinstance(inner, dict))
            present = os.path.exists(command)
            state = "installed" if wired else "MISSING"
            if not present:
                state += ", hook file MISSING"
            row("%s %s" % (role, event), state)
            if not wired:
                faults.append("the %s %s hook is not in settings.json." % (role, event))
            if not present:
                faults.append("the hook file %s does not exist on disk."
                              % HOOK_FILES[event][0])
        if role == "primary":
            picker = data.get("modelPicker")
            ours = isinstance(picker, dict) and picker.get("_second_wind") is True
            want = (cfg.get("refresh") or {}).get("model_picker") is True
            if not want:
                row("model picker", "off")
            else:
                row("model picker", "on, labels written" if ours else
                    "on, no labels written yet (the next refresh writes them)")

    print()
    if sys.platform == "darwin":
        loaded = launchd_loaded()
        exists = os.path.exists(LAUNCHD_PLIST)
        row("launchd agent", "loaded" if loaded else
            ("written but NOT LOADED" if exists else "NOT INSTALLED"))
        if (cfg.get("refresh") or {}).get("launchd") and not loaded:
            message = ("the launchd refresh agent is not loaded, so nothing "
                       "refreshes the readings on a schedule.")
            # Only relief depends on a scheduled reading: it is what the guard
            # reads. Reviewer and worker refresh on demand, so this is a warning.
            if level["failover"]:
                faults.append(message)
            else:
                warnings.append(message)
    else:
        row("scheduled refresh", "no launchd on this platform, use cron")

    tested = cfg.get("tested_versions") or {}
    installed_versions = swlib.client_versions()
    for name, version in installed_versions.items():
        was = tested.get(name, "")
        if not version:
            continue
        mark = "" if was == version else "  (set up against %s)" % (was or "nothing")
        row(name + " version", version + mark)
        if was and was != version:
            warnings.append("%s is %s now, %s when second-wind was set up. If a "
                            "reading stops parsing, that is the first thing to "
                            "check." % (name, version, was))

    print()
    interval = swlib.interval_minutes(cfg)
    row("freshness rule", "fresh under %dm, stale after that, dead after 60m"
        % interval)
    for role in swlib.enabled_roles(cfg):
        if not swlib.reading_enabled(role, cfg):
            row(role + " reading", "not readable for this sign-in")
            continue
        state = swlib.freshness(role, cfg=cfg)
        age = swlib.cache_age(role)
        kind = swlib.status_kind(role)
        detail = "%s (%s old)" % (state, swlib.short_age(age)) if age is not None \
            else "none"
        if kind in ("login", "trust", "parser", "failed"):
            detail += ", %s" % swlib.status_phrase(kind)
        row(role + " reading", detail)
        if state in ("dead", "none") or kind in ("login", "trust", "parser"):
            message = "the %s reading is %s." % (
                role, "missing or over an hour old" if state in ("dead", "none")
                else swlib.status_phrase(kind))
            if level["failover"] and role == "primary":
                faults.append(message + " Automatic handover cannot fire without it.")
            else:
                warnings.append(message)

    print()
    for line in swlib.brief_lines(cfg):
        row("brief", line)

    if level["failover"]:
        flag = os.path.join(sw_home(), "no-failover")
        if os.path.exists(flag):
            faults.append("%s exists, which turns automatic handover off."
                          % tilde(flag))
        if not (cfg.get("failover") or {}).get("enabled"):
            faults.append("failover.enabled is false in the config.")

    print()
    faults = list(dict.fromkeys(faults))
    warnings = [w for w in dict.fromkeys(warnings) if w not in faults]
    for warning in warnings:
        print("warning: %s" % warning)
    verdict_ok = "ARMED: handover will fire when a threshold is crossed" \
        if level["failover"] else "READY"
    if faults:
        print(("NOT ARMED: " if level["failover"] else "NOT READY: ")
              + faults[0])
        for fault in faults[1:]:
            print("            " + fault)
    else:
        print(verdict_ok)


# ---------------------------------------------------------------- uninstall


def cmd_uninstall():
    if not swlib.is_configured():
        print("Nothing to uninstall.")
        return
    cfg = swlib.load_config()
    for role in ("primary", "secondary", "reader"):
        folder = (cfg.get(role) or {}).get("config_dir")
        if not folder or not os.path.isdir(expand(folder)):
            continue
        if merge_settings(folder, model_picker=False) is False:
            print("  ! could not clean %s. Edit it by hand."
                  % tilde(settings_path(folder)))
        else:
            print("  %s cleaned: our hooks and status line removed, and our "
                  "modelPicker key if it was there" % folder)
    mode = os.path.join(sw_home(), "mode")
    if os.path.exists(mode):
        try:
            os.unlink(mode)
            print("  routing override %s removed" % tilde(mode))
        except OSError:
            print("  ! could not remove %s. Delete it by hand." % tilde(mode))
    if in_temp_home():
        print("  launchd left alone: SW_HOME points at a temporary directory")
    else:
        print("  %s" % launchd_remove())
    print("Accounts and logins were not touched.")
    folder = expand((cfg.get("refresh") or {}).get("workdir", "")) or workdir_path()
    print("The workdir trust entries are left in place, because removing them "
          "would edit files a running client may be writing:")
    print("  each Claude profile's .claude.json, under \"projects\": delete the "
          "\"%s\" entry" % tilde(folder))
    print("  ~/.codex/config.toml: delete the [projects.\"%s\"] block" % folder)
    print("Config and logs are still at %s; delete that folder to finish."
          % tilde(sw_home()))


# ---------------------------------------------------------------- main


def build_parser():
    """Separated from main so a test can parse a real command line rather than
    hand-building a namespace and missing the defaults being tested."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--detect", action="store_true",
                    help="print installed workers, Claude profiles, client "
                         "versions and existing config as JSON")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--accounts", action="store_true",
                    help="show every account sorted by current usage headroom")
    ap.add_argument("--live", action="store_true",
                    help="with --accounts, run the readers first")
    ap.add_argument("--check", action="store_true",
                    help="say whether second-wind is actually wired up")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--primary")
    ap.add_argument("--secondary")
    ap.add_argument("--reader",
                    help="a third Claude profile used only to read the primary's "
                         "usage, so a background reader never shares the desktop "
                         "app's credential")
    ap.add_argument("--level", choices=sorted(LEVELS))
    # Each worker is off unless it is asked for by name. The old default was
    # auto, which enabled anything installed and signed in, so a config written
    # for one worker quietly turned on two more and spent subscriptions nobody
    # had mentioned. auto is still accepted, because the wizard offers it and
    # someone may want it, but it has to be typed.
    worker_help = ("on to use it, off to leave it out, auto to use it when it "
                   "is installed and signed in (default off)")
    ap.add_argument("--codex", choices=["auto", "on", "off"], default="off",
                    help=worker_help)
    ap.add_argument("--grok", choices=["auto", "on", "off"], default="off",
                    help=worker_help)
    ap.add_argument("--cursor", choices=["auto", "on", "off"], default="off",
                    help=worker_help)
    # These five default to None so --write can tell "not passed" from "passed
    # the same value as last time", and carry the previous config forward.
    ap.add_argument("--five-hour", type=int, default=None)
    ap.add_argument("--seven-day", type=int, default=None)
    ap.add_argument("--refresh-minutes", type=int, default=None)
    ap.add_argument("--model-picker", choices=["on", "off"], default=None)
    ap.add_argument("--picker-routes", default=None,
                    help="comma-separated routing ids to add to the model "
                         "picker, such as second-wind/codex/gpt-5.6/high")
    ap.add_argument("--no-launchd", action="store_true",
                    help="do not install the scheduled refresh agent")
    ap.add_argument("--timeout", type=int, default=None,
                    help="seconds before a delegated call is killed (default 600)")
    ap.add_argument("--force", action="store_true",
                    help="write the config even if a profile is not signed in")
    return ap


def main():
    ap = build_parser()
    a = ap.parse_args()
    if a.detect:
        return cmd_detect()
    if a.accounts:
        return cmd_accounts(a)
    if a.check:
        return cmd_check()
    if a.show:
        return cmd_show()
    if a.uninstall:
        return cmd_uninstall()
    if a.write:
        if not a.primary:
            sys.exit("second-wind: --write needs --primary")
        if not a.level:
            sys.exit("second-wind: --write needs --level reviewer, worker or relief")
        return cmd_write(a)
    ap.print_help()


if __name__ == "__main__":
    main()
