#!/usr/bin/env python3
"""Shared library for every second-wind script and hook.

One module owns the config, the usage cache, the freshness rule, the headroom
sums and the brief wording. Before this existed, four scripts each had their own
copy of "is this reading too old", they disagreed, and the guard fired off a
figure the brief had already called dead.

Import it from a sibling script:

    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import swlib

or from ``scripts/hooks/``:

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import swlib
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HOME = os.path.expanduser("~")
ROLES = ("primary", "secondary", "codex", "grok", "cursor")
CLAUDE_ROLES = ("primary", "secondary")
# A reading older than this is not shown as a figure at all. The guard ignores
# it and the brief says so, because a wrong percentage is worse than no figure.
DEAD_SECONDS = 3600
DEFAULT_INTERVAL_MINUTES = 15
CONFIG_VERSION = 4
STATUS_KINDS = ("ok", "login", "trust", "parser", "failed", "none")


# ---------------------------------------------------------------- paths


def sw_home():
    """Where the runtime state lives. SW_HOME is read every call so a test can
    point the whole library at a temporary directory."""
    return os.environ.get("SW_HOME") or os.path.join(HOME, ".second-wind")


# Convenience for scripts that want the constant. Functions above are the
# authority; this is resolved once at import.
SW_HOME = sw_home()


def config_path():
    return os.path.join(sw_home(), "config.json")


def usage_path(role):
    return os.path.join(sw_home(), "usage-%s.json" % role)


def status_path(role):
    return os.path.join(sw_home(), "refresh-status-%s.txt" % role)


def expand(path):
    """Turn ~/x into an absolute path. Empty input stays empty."""
    if not path:
        return ""
    return os.path.expanduser(path)


def tilde(path):
    """Shorten an absolute path under the home directory back to ~/x, so nothing
    we print or store carries a real user name."""
    if not path:
        return ""
    if path == HOME:
        return "~"
    if path.startswith(HOME + os.sep):
        return "~" + path[len(HOME):]
    return path


# ---------------------------------------------------------------- config


def load_config():
    """The config, or an empty dict when second-wind is not set up. Never
    raises: a hook that cannot read the config must stay silent, not crash the
    session."""
    try:
        with open(config_path()) as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def is_configured():
    return os.path.exists(config_path())


def interval_minutes(cfg=None):
    cfg = load_config() if cfg is None else cfg
    raw = (cfg.get("refresh") or {}).get("interval_minutes", DEFAULT_INTERVAL_MINUTES)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_MINUTES
    return value if value >= 1 else DEFAULT_INTERVAL_MINUTES


def thresholds(cfg=None):
    cfg = load_config() if cfg is None else cfg
    block = cfg.get("thresholds") or {}
    def pick(key, default):
        try:
            value = int(block.get(key, default))
        except (TypeError, ValueError):
            return default
        return value if 1 <= value <= 100 else default
    return pick("five_hour_pct", 90), pick("seven_day_pct", 80)


def enabled_roles(cfg=None):
    """The roles this machine is configured to use. Primary is always in."""
    cfg = load_config() if cfg is None else cfg
    if not cfg:
        return []
    roles = ["primary"]
    for role in ("secondary", "codex", "grok", "cursor"):
        if (cfg.get(role) or {}).get("enabled") is True:
            roles.append(role)
    return roles


def reading_enabled(role, cfg=None):
    """Whether a usage reading is expected for this role. A worker can be
    enabled for delegation and still have no readable usage panel: Cursor
    signed in with an API key is the case that matters."""
    cfg = load_config() if cfg is None else cfg
    if role not in enabled_roles(cfg):
        return False
    return (cfg.get("refresh") or {}).get(role, True) is not False


def role_label(role, cfg=None):
    cfg = load_config() if cfg is None else cfg
    label = (cfg.get(role) or {}).get("label")
    return label if isinstance(label, str) and label.strip() else role


# ---------------------------------------------------------------- writing


def write_json_atomic(path, data, mode=0o600):
    """Temp file in the same directory then rename, so a crash or a concurrent
    reader never sees half a settings file."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=folder, prefix=".second-wind-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as out:
            json.dump(data, out, indent=2)
            out.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def write_text_atomic(path, text, mode=0o600):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=folder, prefix=".second-wind-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as out:
            out.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def backup_once(path):
    """Back up a file we are about to edit. Two copies on purpose: one
    ``.second-wind-original`` taken the first time we ever touch this file and
    never overwritten, plus a timestamped copy per write. The original is what
    somebody wants a year later; the timestamped ones are for today.

    Returns (original_path_or_None, timestamped_path_or_None).
    """
    if not os.path.exists(path):
        return None, None
    original = path + ".second-wind-original"
    made_original = None
    if not os.path.exists(original):
        shutil.copy2(path, original)
        made_original = original
    stamped = "%s.second-wind-backup-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, stamped)
    return made_original, stamped


# ---------------------------------------------------------------- usage cache


def load_usage(role):
    """The last reading for a role, or an empty dict."""
    try:
        with open(usage_path(role)) as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def status(role):
    """The first line of the role's status file, when it is at least as new as
    the reading. An older status file describes a refresh the cache has already
    superseded, so it is dropped."""
    path = status_path(role)
    try:
        with open(path) as handle:
            message = handle.readline().strip()
        status_time = os.path.getmtime(path)
    except Exception:
        return ""
    cache = usage_path(role)
    cache_time = 0.0
    if os.path.exists(cache):
        # a corrupt cached_at must not take the caller down: the brief runs
        # inside a hook, and a hook that raises is a broken session
        try:
            stamped = float(load_usage(role).get("cached_at") or 0)
        except (TypeError, ValueError):
            stamped = 0.0
        cache_time = max(os.path.getmtime(cache), stamped)
    return message if status_time >= cache_time else ""


def status_kind(role=None, message=None):
    """Reduce a status line to one of the vocabulary kinds."""
    if message is None:
        message = status(role) if role else ""
    upper = (message or "").strip().upper()
    if not upper:
        return "none"
    if upper.startswith("OK"):
        return "ok"
    if "LOGIN EXPIRED" in upper:
        return "login"
    if "TRUST PROMPT" in upper:
        return "trust"
    if "PARSER MISMATCH" in upper:
        return "parser"
    return "failed"


def status_words(kind):
    """At most three words, for a table cell."""
    return {
        "ok": "ready",
        "login": "login expired",
        "trust": "trust prompt blocked",
        "parser": "parser mismatch",
        "failed": "refresh failed",
        "none": "no reading yet",
    }.get(kind, "unknown")


def status_phrase(kind):
    """A sentence fragment for the session brief."""
    return {
        "login": "login expired, sign in again",
        "trust": "a trust prompt blocked the reader",
        "parser": "the usage panel labels moved, the parser needs an update",
        "failed": "the last refresh failed",
    }.get(kind, "")


def cache_age(role, now=None):
    """Seconds since this role's reading was written, or None."""
    usage = load_usage(role)
    try:
        cached = int(usage.get("cached_at") or 0)
    except (TypeError, ValueError):
        return None
    if cached <= 0:
        return None
    now = int(time.time()) if now is None else int(now)
    return now - cached


def freshness(role, now=None, cfg=None):
    """The one staleness rule in the codebase.

    fresh   younger than refresh.interval_minutes
    stale   older than that, still worth showing with its age
    dead    older than an hour: no figure is shown and the guard ignores it
    none    no reading at all, or a timestamp so far in the future it is junk
    """
    age = cache_age(role, now=now)
    if age is None:
        return "none"
    if age < 0:
        # small clock skew between the writer and us is not a problem
        return "fresh" if age >= -60 else "none"
    if age >= DEAD_SECONDS:
        return "dead"
    return "fresh" if age < interval_minutes(cfg) * 60 else "stale"


def short_age(seconds):
    if seconds is None:
        return "unknown"
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "%ds" % seconds
    minutes = seconds // 60
    if minutes < 90:
        return "%dm" % minutes
    hours = minutes // 60
    if hours < 48:
        return "%dh" % hours
    return "%dd" % (hours // 24)


def number(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def pool(usage, key):
    """Read a pool percentage from ``extra`` first, then the top level, so a
    reading written before the extra block existed still parses."""
    found = number((usage.get("extra") or {}).get(key))
    return found if found is not None else number(usage.get(key))


def headroom(role, usage):
    """Percentage of the strictest reported pool still unspent, or None.

    Claude and Codex are judged on the windows the client actually printed. A
    missing window is skipped rather than fatal: the Codex status panel on some
    plans prints the weekly limit and no 5-hour line, and requiring both made
    Codex unroutable forever. Grok reports a weekly window only. Cursor reports
    monthly pools.
    """
    if not usage:
        return None
    if role in CLAUDE_ROLES or role == "codex":
        windows = [w for w in (number(usage.get("five_hour_pct")),
                               number(usage.get("seven_day_pct"))) if w is not None]
        if not windows:
            return None
        spent = max(windows)
    elif role == "grok":
        week = number(usage.get("seven_day_pct"))
        if week is None:
            return None
        spent = week
    elif role == "cursor":
        pools = [p for p in (pool(usage, "included_pct"), pool(usage, "auto_pct"),
                             pool(usage, "api_pct")) if p is not None]
        if not pools:
            return None
        spent = max(pools)
    else:
        return None
    return int(min(100, max(0, round(100 - spent))))


# ---------------------------------------------------------------- brief


def _windows_text(role, usage):
    if role == "cursor":
        parts = []
        for label, key in (("included", "included_pct"), ("auto", "auto_pct"),
                           ("api", "api_pct")):
            value = pool(usage, key)
            if value is not None:
                parts.append("%s %d%%" % (label, round(value)))
        return ", ".join(parts)
    parts = []
    five = number(usage.get("five_hour_pct"))
    week = number(usage.get("seven_day_pct"))
    if five is not None:
        parts.append("5h %d%%" % round(five))
    if week is not None:
        parts.append("7d %d%%" % round(week))
    return ", ".join(parts)


def brief_lines(cfg=None, now=None):
    """One honest line per enabled account, for the session brief.

    Fresh or stale readings show the figures and their age. A dead or missing
    reading says so and says a refresh is coming, because a stale percentage
    read as current is the failure this whole tool exists to avoid.
    """
    cfg = load_config() if cfg is None else cfg
    lines = []
    for role in enabled_roles(cfg):
        label = role_label(role, cfg)
        if not reading_enabled(role, cfg):
            lines.append("%s: no usage reading available for this sign-in" % label)
            continue
        usage = load_usage(role)
        state = freshness(role, now=now, cfg=cfg)
        kind = status_kind(role)
        phrase = status_phrase(kind)
        figures = _windows_text(role, usage) if state in ("fresh", "stale") else ""
        if not figures:
            lines.append("%s: %s" % (label, phrase or "no current reading, refreshing"))
            continue
        line = "%s: %s (%s ago)" % (label, figures, short_age(cache_age(role, now=now)))
        if phrase:
            line += ", " + phrase
        lines.append(line)
    return lines


# ---------------------------------------------------------------- session


def is_primary_session():
    """True when this process belongs to the primary profile.

    Compares resolved paths. A symlinked profile directory is the same profile,
    and the guard installed in the secondary uses this to exit immediately.
    """
    cfg = load_config()
    configured = (cfg.get("primary") or {}).get("config_dir")
    if not configured:
        return False
    here = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
    try:
        return os.path.realpath(expand(here)) == os.path.realpath(expand(configured))
    except OSError:
        return False


def notify(title, body, key, window_seconds=3600, detached=True):
    """Send one macOS notification per key per window. Returns True when it was
    started. No-op off macOS, and never raises: a failed notification must not
    take a hook down with it.

    Detached is the default because every caller is a hook, and a hook is in the
    way of somebody's prompt. So the dedupe stamp is written first and osascript
    is started with no wait at all: a slow notification centre then costs the
    session nothing. Pass detached=False only where the caller genuinely needs
    to know osascript finished.
    """
    stamp = os.path.join(sw_home(), "notify-%s.stamp" % re.sub(r"[^A-Za-z0-9_.-]", "-", key))
    try:
        if os.path.exists(stamp):
            with open(stamp) as handle:
                seen = handle.read()
            age = time.time() - os.path.getmtime(stamp)
            if seen == body and age < window_seconds:
                return False
    except Exception:
        pass
    if detached:
        # Stamp before starting, so the dedupe holds even though nothing is
        # waiting to see whether osascript worked.
        try:
            os.makedirs(sw_home(), exist_ok=True)
            write_text_atomic(stamp, body)
        except Exception:
            pass
    if sys.platform != "darwin":
        return False

    def quote(text):
        return str(text).replace("\\", "\\\\").replace('"', '\\"')

    script = 'display notification "%s" with title "%s"' % (quote(body), quote(title))
    try:
        if detached:
            subprocess.Popen(["osascript", "-e", script],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True,
                             cwd="/")
            return True
        subprocess.run(["osascript", "-e", script], timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    try:
        os.makedirs(sw_home(), exist_ok=True)
        write_text_atomic(stamp, body)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------- clients


CLIENT_COMMANDS = (
    ("claude", "claude"),
    ("codex", "codex"),
    ("grok", "grok"),
    ("cursor", "cursor-agent"),
)


def _version_of(command):
    binary = shutil.which(command)
    if not binary:
        return ""
    try:
        done = subprocess.run([binary, "--version"], capture_output=True, text=True,
                              timeout=10)
    except Exception:
        return ""
    text = (done.stdout or done.stderr or "").strip()
    match = re.search(r"\d+\.\d+(?:[.\-][0-9A-Za-z]+)*", text)
    return match.group(0) if match else text.splitlines()[0].strip() if text else ""


def client_versions():
    """Installed version of each supported client, empty string when absent."""
    return {name: _version_of(command) for name, command in CLIENT_COMMANDS}


def has_jq():
    return shutil.which("jq") is not None
