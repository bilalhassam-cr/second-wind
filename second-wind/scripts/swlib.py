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
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HOME = os.path.expanduser("~")
ROLES = ("primary", "secondary", "codex", "codex2", "codex3", "grok", "cursor")
CLAUDE_ROLES = ("primary", "secondary")
# Up to three Codex roles, each its own CODEX_HOME and so its own ChatGPT sign-in. The
# desktop app rewrites ~/.codex/auth.json whenever its user switches workspace,
# so a role that shares that directory changes identity without anyone telling
# second-wind; a role with its own directory does not.
CODEX_ROLES = ("codex", "codex2", "codex3")
# Every role that can take delegated work, in the order the picker lists them.
WORKER_ROLES = ("secondary", "codex", "codex2", "codex3", "grok", "cursor")
# A reading older than this is not shown as a figure at all. The guard ignores
# it and the brief says so, because a wrong percentage is worse than no figure.
DEAD_SECONDS = 3600
DEFAULT_INTERVAL_MINUTES = 15
CONFIG_VERSION = 4
STATUS_KINDS = ("ok", "login", "nologin", "trust", "parser", "failed", "none")


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
    for role in WORKER_ROLES:
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


def codex_home(role, cfg=None):
    """The CODEX_HOME a Codex role runs under, or None for the default.

    None means ~/.codex, and the caller leaves the variable alone. Only a
    directory of its own is ever exported, the same rule the Claude side keeps
    for CLAUDE_CONFIG_DIR: the default is what the client finds unaided.
    """
    cfg = load_config() if cfg is None else cfg
    configured = (cfg.get(role) or {}).get("config_dir")
    if not isinstance(configured, str) or not configured.strip():
        return None
    path = expand(configured.strip())
    if os.path.realpath(path) == os.path.realpath(os.path.join(HOME, ".codex")):
        return None
    return path


def full_access(role, cfg=None):
    """Whether this worker may run unsandboxed in work mode. Off unless the
    config says so in as many words: the skill ships to strangers."""
    cfg = load_config() if cfg is None else cfg
    return (cfg.get(role) or {}).get("full_access") is True


def _known(value):
    text = str(value or "").strip()
    return text if text and text.lower() != "unknown" else ""


def identity_drift(role, cfg=None, now=None):
    """A sentence when the sign-in a reader saw is not the one the config
    expects, or None.

    Only a reading the brief would show is compared. A dead one, hours old and
    possibly written before a re-login, would report a drift on a healthy
    sign-in, and the panel and --check would then disagree with each other.

    The account is compared for every role. The plan is compared for the Codex
    roles as well, because the case that bit was a workspace switch inside one
    login: same email, Business seat one minute and Free the next, and a check
    on the email alone waved it through. The other clients' plan labels come
    from setup's own vocabulary rather than from a panel, so comparing them
    would report a drift on every machine. Only fields both sides know are
    compared, so an unknown is never a drift.
    """
    cfg = load_config() if cfg is None else cfg
    if freshness(role, now=now, cfg=cfg) not in ("fresh", "stale"):
        return None
    wanted = cfg.get(role) or {}
    seen = load_usage(role)
    changes = []
    fields = ("account", "plan") if role in CODEX_ROLES else ("account",)
    for field in fields:
        expected, observed = _known(wanted.get(field)), _known(seen.get(field))
        if expected and observed and expected.lower() != observed.lower():
            changes.append((field, observed, expected))
    if not changes:
        return None
    return "signed in as %s, not the configured %s" % (
        ", ".join(observed for _, observed, _ in changes),
        ", ".join(expected for _, _, expected in changes))


# ---------------------------------------------------------------- routing

# The model picker is the only list of destinations Claude Code puts in front of
# somebody mid-session, so second-wind borrows it. These ids are not models: a
# PreModelSwitch hook refuses the switch and writes the routing mode file
# instead. One parser for all of it, shared by that hook, the prompt guard and
# the picker, because three copies of "is this one of ours" would disagree.
ROUTE_PREFIX = "second-wind/"
# Every spelling of a worker inside a pseudo-model id. `personal` and
# `secondary` both mean the second Claude account: the config calls it the
# secondary, a person calls it their personal one.
ROUTE_WORKERS = {
    "personal": "secondary",
    "secondary": "secondary",
    "codex": "codex",
    "codex2": "codex2",
    "codex-personal": "codex2",
    "codex3": "codex3",
    "grok": "grok",
    "cursor": "cursor",
}
# Bare ids accepted as well, because the desktop app's picker does not show our
# rows and it takes a typed model id without the CLI's lookup. Somebody there
# types what they would say.
ROUTE_ALIASES = {
    "claude-personal": "secondary",
    "personal": "secondary",
    "second-wind-personal": "secondary",
    "codex": "codex",
    "codex2": "codex2",
    "codex-personal": "codex2",
    "codex3": "codex3",
    "grok": "grok",
    "cursor": "cursor",
}
ROUTE_LABELS = {
    "secondary": "the second Claude account",
    "codex": "Codex",
    "codex2": "the second Codex account",
    "codex3": "the third Codex account",
    "grok": "Grok Build",
    "cursor": "Cursor Agent",
}
# A model or effort segment. Loose on purpose: the runner passes both straight
# through to a vendor client, and this is not the place to hold a list of every
# model name four vendors will ship next month.
_ROUTE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
# How long a route armed from a picker or a chat command stays in force. A
# route is a decision about the next few tasks, not a setting, and a stale one
# found the next morning would send work somewhere nobody remembers choosing.
ROUTE_MAX_AGE = 12 * 3600
# The model and effort presets the /second-wind picker offers per worker, before
# config overrides them. `default` means send no model, so the client uses the
# account's own. Kept here rather than in the skill text so the picker, the
# config and setup cannot disagree about what a worker can be asked for.
# Cursor takes no effort flag; Grok's is --reasoning-effort; Codex accepts
# minimal, low, medium, high and xhigh.
PICKER_MODELS = {
    "secondary": ("opus/high", "opus/medium", "sonnet/medium", "sonnet/low",
                  "haiku/low"),
    "codex": ("gpt-5.6/high", "gpt-5.6/medium", "gpt-5.6/low"),
    "codex2": ("gpt-5.6/high", "gpt-5.6/medium", "gpt-5.6/low"),
    "codex3": ("gpt-5.6/high", "gpt-5.6/medium", "gpt-5.6/low"),
    "grok": ("default/high", "default/medium"),
    "cursor": ("default",),
}
# One line per preset, so a picker row says what it costs rather than making
# somebody guess from a model name. A preset nobody wrote a line for gets a
# plain description built from its own words.
MODEL_NOTES = {
    "opus/high": "Opus 5, high effort: the hardest work, spends the most",
    "opus/medium": "Opus 5, medium: long jobs at a lower rate",
    "sonnet/medium": "Sonnet 5, medium: everyday tasks, light on the allowance",
    "sonnet/low": "Sonnet 5, low: quick reads and checks",
    "haiku/low": "Haiku 4.5, low: the cheapest pass",
    "gpt-5.6/high": "GPT-5.6, high effort: the hardest work, slowest",
    "gpt-5.6/medium": "GPT-5.6, medium: everyday tasks",
    "gpt-5.6/low": "GPT-5.6, low: quick reads and checks",
    "default/high": "The account's own model, high reasoning effort",
    "default/medium": "The account's own model, medium reasoning effort",
    "default": "The account's own model and effort; neither is passed",
}


def route_id(worker):
    """The canonical pseudo-model id for a worker."""
    return ROUTE_PREFIX + ("personal" if worker == "secondary" else worker)


def route_label(worker, cfg=None):
    """What a picker row and a block message call this worker. A label the user
    set wins; setup writes the role name itself, which reads as nothing in a
    sentence, so that falls through to our own wording."""
    label = role_label(worker, cfg)
    return ROUTE_LABELS.get(worker, worker) if label == worker else label


def parse_route(target, cfg=None):
    """A second-wind pseudo-model id as a dict, or None for anything else.

    Accepts ``second-wind/<worker>[/<model>[/<effort>]]`` and the bare aliases
    above. Returns ``{worker, model, effort, label}`` with model and effort None
    when the id does not name them. Anything unrecognised is somebody's real
    model and is not ours to touch, so it returns None rather than guessing.
    """
    if not isinstance(target, str):
        return None
    text = target.strip().strip("/")
    if not text:
        return None
    lowered = text.lower()
    if lowered in ROUTE_ALIASES:
        worker, parts = ROUTE_ALIASES[lowered], []
    elif lowered.startswith(ROUTE_PREFIX):
        parts = text[len(ROUTE_PREFIX):].split("/")
        if parts[0].lower() not in ROUTE_WORKERS:
            return None
        worker, parts = ROUTE_WORKERS[parts[0].lower()], parts[1:]
    else:
        return None
    if len(parts) > 2 or any(not _ROUTE_TOKEN.match(part) for part in parts):
        return None
    return {"worker": worker,
            "model": parts[0] if parts else None,
            "effort": parts[1] if len(parts) == 2 else None,
            "label": route_label(worker, cfg)}


def model_menu(worker, cfg=None):
    """The model and effort rows to offer for this worker, newest choice first.

    Each row is ``{model, effort, text}``, with model and effort None on the
    ``default`` row. ``picker.models`` in the config replaces the defaults for a
    worker it names; a row it cannot parse is dropped rather than shown, because
    a picker row that fails at the runner is worse than a shorter menu.
    """
    cfg = load_config() if cfg is None else cfg
    configured = ((cfg.get("picker") or {}).get("models") or {}).get(worker)
    entries = configured if isinstance(configured, list) and configured \
        else PICKER_MODELS.get(worker, ("default",))
    rows = []
    for entry in entries:
        row = parse_model_entry(entry)
        if row and row not in rows:
            rows.append(row)
    return rows


def parse_model_entry(entry):
    """``model/effort``, ``model``, ``default`` or ``default/effort`` as a
    preset row, or None. ``default`` in the model position means pass no model.
    """
    if not isinstance(entry, str):
        return None
    text = entry.strip().strip("/")
    if not text:
        return None
    parts = text.split("/")
    if len(parts) > 2 or any(not _ROUTE_TOKEN.match(part) for part in parts):
        return None
    model = None if parts[0].lower() == "default" else parts[0]
    effort = parts[1] if len(parts) == 2 else None
    if model:
        shown = " ".join(part for part in (model, effort) if part)
    else:
        shown = "%s effort" % effort if effort else "default"
    key = "%s/%s" % (model or "default", effort) if effort else (model or "default")
    note = MODEL_NOTES.get(key)
    if not note:
        note = ("%s, %s effort" % (model, effort) if model and effort
                else model or ("%s reasoning effort" % effort if effort
                               else "the account's own model and effort"))
    return {"model": model, "effort": effort, "text": shown, "note": note}


def destinations(cfg=None, now=None):
    """Every worker this machine can send the next tasks to, for the picker.

    One row per enabled worker: its label, the same usage wording the brief
    uses, its headroom for sorting and a model menu. The primary is not in the
    list, because keeping the work here is not a destination.
    """
    cfg = load_config() if cfg is None else cfg
    rows = []
    for worker in WORKER_ROLES:
        if worker not in enabled_roles(cfg):
            continue
        usage = load_usage(worker)
        live = (freshness(worker, now=now, cfg=cfg) in ("fresh", "stale")
                and status_kind(worker) not in ("login", "nologin", "trust",
                                                "parser"))
        rows.append({"worker": worker,
                     "label": route_label(worker, cfg),
                     "usage": usage_summary(worker, cfg=cfg, now=now),
                     "headroom": headroom(worker, usage) if live else None,
                     "models": model_menu(worker, cfg)})
    rows.sort(key=lambda row: (row["headroom"] is None, -(row["headroom"] or 0)))
    return rows


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


# ---------------------------------------------------------------- route store

# One file per session, plus one file for the routes that are meant to apply
# everywhere. Before this, every route landed in ~/.second-wind/mode, so a route
# armed in one chat overwrote the route another chat had already set: a desktop
# arming replaced a picker route somebody was in the middle of using. The global
# file is still written for an explicit "every session" route, and still read,
# because a bare word echoed into it is the oldest way to use this tool.
EVERY_SESSION = "*"


def mode_path():
    """The global route file: a bare word, or JSON scoped to every session."""
    return os.path.join(sw_home(), "mode")


def routes_dir():
    return os.path.join(sw_home(), "routes")


def route_slug(session_id):
    """A session id as one filename, or None when no single session is named.

    Ids are uuids in practice. Anything else is folded to the same small set of
    characters, so an id carrying a slash or a dotted pair cannot write outside
    the routes directory.
    """
    text = str(session_id or "").strip()
    if not text or text == EVERY_SESSION:
        return None
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", text)[:120]
    if not slug.strip("._-"):
        slug = "session-%s" % hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return slug


def route_path(session_id):
    """Where this session's route lives, or None when the route is global."""
    slug = route_slug(session_id)
    return os.path.join(routes_dir(), slug + ".json") if slug else None


def route_paths():
    """Every per-session route file, by name."""
    return sorted(glob.glob(os.path.join(routes_dir(), "*.json")))


def write_mode(route, source=None, session_id=None):
    """The routing override, in the one shape every reader expects.

    Two hooks and the chat command write it now, so the keys live here rather
    than in each of them. ``source`` records which path armed the route, because
    the desktop one cannot be turned off by picking a model in a menu that runs
    no hook. ``session_id`` decides the file: a named session gets its own file
    in ``routes/``, and ``"*"`` or no session at all goes to the global file.
    """
    data = {"worker": route["worker"],
            "model": route["model"],
            "effort": route["effort"],
            "set_at": int(time.time()),
            "label": route["label"]}
    if route.get("mode"):
        data["mode"] = route["mode"]
    if source:
        data["source"] = source
    if session_id:
        data["session_id"] = str(session_id).strip()
    return write_json_atomic(route_path(session_id) or mode_path(), data)


def route_text(path):
    """A route file as written, stripped, or an empty string."""
    try:
        with open(path) as handle:
            return handle.read().strip()
    except Exception:
        return ""


def mode_text():
    """The global route file as written, stripped, or an empty string."""
    return route_text(mode_path())


def drop_mode():
    """Remove the global route. Absent is the same as removed."""
    try:
        os.unlink(mode_path())
    except OSError:
        pass


def drop_route(session_id=None):
    """Remove this session's route file. Returns the path when there was one."""
    path = route_path(session_id)
    if not path or not os.path.exists(path):
        return None
    try:
        os.unlink(path)
    except OSError:
        return None
    return path


def global_route_applies(session_id=None):
    """Whether the global file is what routes this session.

    Unreadable contents count as ours: rubbish in a file only this tool writes
    is not somebody else's decision, and clearing is the gesture that gets rid
    of it.
    """
    text = mode_text()
    if not text:
        return False
    if not text.startswith("{"):
        return True
    try:
        data = json.loads(text)
    except Exception:
        return True
    if not isinstance(data, dict):
        return True
    scope = data.get("session_id")
    scope = scope.strip() if isinstance(scope, str) and scope.strip() else None
    return (not scope or scope == EVERY_SESSION
            or scope == str(session_id or "").strip())


def clear_route(session_id=None):
    """Stop routing this session: its own file, and the global file when that is
    what was routing here. Returns the paths removed."""
    removed = []
    path = drop_route(session_id)
    if path:
        removed.append(path)
    if os.path.exists(mode_path()) and global_route_applies(session_id):
        drop_mode()
        removed.append(mode_path())
    return removed


def route_age(data, now=None, path=None):
    """Seconds since this route was armed, or None when nothing says. The
    file's own mtime stands in for a hand-written route with no timestamp."""
    now = int(time.time()) if now is None else int(now)
    try:
        stamped = int(data.get("set_at"))
    except (TypeError, ValueError, AttributeError):
        stamped = 0
    if stamped <= 0:
        try:
            stamped = int(os.path.getmtime(path or mode_path()))
        except OSError:
            return None
    return now - stamped


def route_in_file(path, session_id=None, cfg=None, now=None):
    """The route this file holds for this session, or None.

    Three shapes reach this. A bare word is global, which is the old behaviour
    and what somebody echoing into the file expects. A JSON route carries the
    session that armed it and applies only there, unless it says ``"*"`` or is
    an older file that names no session. A JSON route past ROUTE_MAX_AGE is
    deleted where it was found, because it is nobody's current decision.

    Returns ``{word, worker, model, effort, mode, label, session_id, source,
    path}``. ``word`` is what the file named, for a message that quotes it back.
    """
    text = route_text(path)
    if not text:
        return None
    if not text.startswith("{"):
        word = "".join(text.split())
        route = parse_route(word, cfg) or role_route(word, cfg)
        if not route:
            return None
        route.update({"word": route["worker"], "mode": None,
                      "session_id": None, "source": None, "path": path})
        return route
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    route = role_route(data.get("worker"), cfg)
    if not route:
        return None
    age = route_age(data, now=now, path=path)
    if age is not None and age > ROUTE_MAX_AGE:
        try:
            os.unlink(path)
        except OSError:
            pass
        return None
    scope = data.get("session_id")
    scope = scope.strip() if isinstance(scope, str) and scope.strip() else None
    if scope and scope != EVERY_SESSION \
            and scope != str(session_id or "").strip():
        # Somebody else's route. Left where it is: it is still their decision,
        # and this session simply is not the one it was armed for.
        return None
    label = data.get("label")
    for key in ("model", "effort", "mode"):
        value = data.get(key)
        route[key] = value.strip() if isinstance(value, str) and value.strip() \
            else None
    route.update({"word": route["worker"],
                  "label": label.strip() if isinstance(label, str)
                  and label.strip() else route["label"],
                  "session_id": scope,
                  "source": data.get("source"),
                  "path": path})
    return route


def active_route(session_id=None, cfg=None, now=None):
    """The route in force for this session, or None.

    This session's own file is read first, then the global one, so a route armed
    here beats one somebody left applying everywhere. An expired file met on the
    way is deleted.
    """
    cfg = load_config() if cfg is None else cfg
    mine = route_path(session_id)
    if mine:
        route = route_in_file(mine, session_id, cfg, now)
        if route:
            return route
    return route_in_file(mode_path(), session_id, cfg, now)


def armed_routes(cfg=None, now=None):
    """Every route armed on this machine, newest first, for ``--show``.

    A listing, not a reader: it names expired and unreadable files rather than
    deleting or hiding them, because "why is my work going there" is answered by
    seeing all of them at once.

    Rows are ``{path, scope, worker, label, model, effort, mode, source, age,
    expired, readable}``.
    """
    cfg = load_config() if cfg is None else cfg
    rows = []
    for path in [mode_path()] + route_paths():
        text = route_text(path)
        if not text:
            continue
        named = EVERY_SESSION if path == mode_path() \
            else os.path.basename(path)[:-len(".json")]
        row = {"path": path, "scope": named, "worker": None, "label": None,
               "model": None, "effort": None, "mode": None, "source": None,
               "age": None, "expired": False, "readable": True}
        if not text.startswith("{"):
            word = "".join(text.split())
            route = parse_route(word, cfg) or role_route(word, cfg) or {}
            row.update({"scope": EVERY_SESSION,
                        "worker": route.get("worker") or word,
                        "label": route.get("label"),
                        "model": route.get("model"),
                        "effort": route.get("effort"),
                        "source": "a bare word",
                        "readable": bool(route),
                        "age": route_age({}, now=now, path=path)})
            rows.append(row)
            continue
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if not isinstance(data, dict):
            row["readable"] = False
            rows.append(row)
            continue
        route = role_route(data.get("worker"), cfg) or {}
        scope = data.get("session_id")
        scope = scope.strip() if isinstance(scope, str) and scope.strip() \
            else named
        age = route_age(data, now=now, path=path)
        row.update({"scope": scope,
                    "worker": route.get("worker") or data.get("worker"),
                    "label": data.get("label") or route.get("label"),
                    "model": data.get("model"), "effort": data.get("effort"),
                    "mode": data.get("mode"), "source": data.get("source"),
                    "age": age, "readable": bool(route),
                    "expired": age is not None and age > ROUTE_MAX_AGE})
        rows.append(row)
    rows.sort(key=lambda row: (row["age"] is None, row["age"] or 0))
    return rows


def role_route(word, cfg=None):
    """A worker named the way the config and the mode file name it, in the shape
    parse_route returns. parse_route takes the picker ids and the aliases
    somebody types; this is the other spelling, the plain role name."""
    worker = ROUTE_WORKERS.get(str(word or "").strip().lower())
    if not worker:
        return None
    return {"worker": worker, "model": None, "effort": None, "mode": None,
            "label": route_label(worker, cfg)}


# ---------------------------------------------------------------- desktop store

# The desktop app keeps one JSON file per session in here, and the `cliSessionId`
# in it is the session id a hook is handed. It is the only place a name typed
# into the app's model menu shows up: that route fires no PreModelSwitch hook, so
# the prompt guard reads this instead of being told. Read only, always. Nothing
# in second-wind writes into the app's own store.
DESKTOP_STORE = os.path.join(HOME, "Library", "Application Support", "Claude",
                             "claude-code-sessions")
# How many sessions the path cache keeps. A machine that is never restarted
# would otherwise grow the file for ever, and only the current session matters.
DESKTOP_MAP_MAX = 40
# How many store files a scan reads, newest first. The store grows for ever and
# this runs at a prompt, so the work has to be bounded by something. A live
# session is among the newest by definition: the app rewrites its file as the
# session goes, and it had just written the typed model when this hook ran.
DESKTOP_SCAN_MAX = 200


def desktop_store():
    """SW_DESKTOP_STORE is read every call, so a test points the scanner at a
    fixture store and never at the real one."""
    return os.environ.get("SW_DESKTOP_STORE") or DESKTOP_STORE


def session_map_path():
    """Where the session id to store file match is remembered."""
    return os.path.join(sw_home(), "session-map.json")


def _desktop_files(store):
    """Store files, most recently written first. The session asking is the one
    that just did something, so the match is usually among the first read."""
    def when(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0
    paths = glob.glob(os.path.join(store, "*", "*", "local_*.json"))
    return sorted(paths, key=when, reverse=True)[:DESKTOP_SCAN_MAX]


def _desktop_entry(path, session_id):
    """(True, model) when this file is that session's, else (False, None).

    The raw substring test comes first on purpose. The store holds hundreds of
    files and tens of megabytes; reading the bytes costs a fraction of a second
    and parsing all of them costs several times that, at the one moment a person
    is watching the cursor. An unreadable or half-written file is skipped.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return False, None
    if ('"%s"' % session_id).encode("utf-8", "replace") not in raw:
        return False, None
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return False, None
    if not isinstance(data, dict) or data.get("cliSessionId") != session_id:
        return False, None
    model = data.get("model")
    if isinstance(model, str) and model.strip():
        return True, model.strip()
    return True, None


def _remember_session(session_id, path):
    """Cache the match so the next prompt reads one file instead of the store.
    A failure here is not worth a word: the scan simply happens again."""
    known = {}
    try:
        with open(session_map_path()) as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            known = {key: value for key, value in loaded.items()
                     if isinstance(value, str)}
    except Exception:
        known = {}
    known.pop(session_id, None)
    entries = list(known.items())[-(DESKTOP_MAP_MAX - 1):] + [(session_id, path)]
    try:
        write_json_atomic(session_map_path(), dict(entries))
    except Exception:
        pass


def desktop_session_model(session_id):
    """The model the desktop app has on this session, or None.

    Typing a name into the app's model menu sets it as the session model without
    firing PreModelSwitch, so this file is the only sign that a routing name is
    sitting where a real model should be. None means no answer, whatever the
    reason: another platform, no store, no entry for this session.
    """
    session_id = str(session_id or "").strip()
    if not session_id:
        return None
    if not os.environ.get("SW_DESKTOP_STORE") and sys.platform != "darwin":
        return None
    store = desktop_store()
    if not os.path.isdir(store):
        return None
    cached = None
    try:
        with open(session_map_path()) as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            cached = loaded.get(session_id)
    except Exception:
        cached = None
    if isinstance(cached, str) and cached:
        mine, model = _desktop_entry(cached, session_id)
        if mine:
            return model
        # The cached file is gone, or the app has reused it for another session.
        # Either way it is no longer evidence, so fall through to a rescan.
    for path in _desktop_files(store):
        mine, model = _desktop_entry(path, session_id)
        if mine:
            _remember_session(session_id, path)
            return model
    return None


def _desktop_record(path):
    """One store file parsed, or None. No substring shortcut here: the caller is
    looking for a record it cannot name in advance."""
    try:
        with open(path, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8", "replace"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _activity(record, path):
    """When this session was last active, as a sortable number. The store's own
    field decides; a record without one falls back to the file's mtime."""
    stamp = record.get("lastActivityAt")
    if isinstance(stamp, (int, float)):
        return float(stamp)
    if isinstance(stamp, str):
        try:
            return float(stamp.strip())
        except ValueError:
            pass
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def desktop_session_here(cwd=None, now=None):
    """The desktop app's record for the session running in this directory.

    A chat command has no way to ask Claude Code for its own session id, and a
    route has to be scoped to a session or it applies to all of them. The store
    is the only place the id is written down, so the record whose ``cwd`` is
    this directory and whose activity is the most recent is taken as this
    session. None means no answer, whatever the reason, and the caller says so
    rather than guessing.
    """
    if not os.environ.get("SW_DESKTOP_STORE") and sys.platform != "darwin":
        return None
    store = desktop_store()
    if not os.path.isdir(store):
        return None
    try:
        here = os.path.realpath(cwd or os.getcwd())
    except OSError:
        return None
    best, latest = None, None
    for path in _desktop_files(store):
        record = _desktop_record(path)
        if not record:
            continue
        session = record.get("cliSessionId")
        folder = record.get("cwd")
        if not isinstance(session, str) or not session.strip():
            continue
        if not isinstance(folder, str) or not folder.strip():
            continue
        try:
            if os.path.realpath(expand(folder)) != here:
                continue
        except OSError:
            continue
        when = _activity(record, path)
        if latest is None or when > latest:
            best, latest = record, when
    return best


# ---------------------------------------------------------------- runtime mirror

# A LaunchAgent has no permission for ~/Documents, ~/Desktop or ~/Downloads, so
# it cannot read a skill installed in one of them and every scheduled refresh
# dies with "Operation not permitted" before its first line. Hooks are fine:
# they run inside Claude Code, which does have that permission. So the scheduled
# refresh runs from a mirror inside SW_HOME, and these are the files it needs.
# scripts/hooks/ is deliberately not mirrored.
RUNTIME_FILES = (
    "usage-refresh.sh",
    "swlib.py",
    "ptyreader.py",
    "claude-usage.py",
    "codex-status.py",
    "grok-usage.py",
    "cursor-usage.py",
    "model-picker.py",
)


def runtime_dir():
    return os.path.join(sw_home(), "runtime")


def _runtime_source(skill_dir):
    """The folder to mirror from. Accepts the skill root or the scripts folder
    itself, because setup passes one and usage-refresh.sh passes the other."""
    folder = expand(skill_dir or "")
    if not folder:
        return ""
    inner = os.path.join(folder, "scripts")
    return inner if os.path.isdir(inner) else folder


def sync_runtime(skill_dir):
    """Mirror the scheduled-refresh files into ~/.second-wind/runtime.

    A file is copied only when the mirror is missing it, or the source is newer,
    or the sizes differ. The executable bit is preserved. Nothing in the mirror
    that this list does not name is ever deleted, and the source is only ever
    read. Returns ``{dir, copied, skipped, missing}``.
    """
    source = _runtime_source(skill_dir)
    target = runtime_dir()
    os.makedirs(target, exist_ok=True)
    os.chmod(target, 0o700)
    result = {"dir": target, "copied": [], "skipped": [], "missing": []}
    if not source or not os.path.isdir(source):
        result["missing"] = list(RUNTIME_FILES)
        return result
    for name in RUNTIME_FILES:
        src = os.path.join(source, name)
        dst = os.path.join(target, name)
        try:
            src_stat = os.stat(src)
        except OSError:
            result["missing"].append(name)
            continue
        try:
            dst_stat = os.stat(dst)
        except OSError:
            dst_stat = None
        if (dst_stat is not None and dst_stat.st_size == src_stat.st_size
                and int(src_stat.st_mtime) <= int(dst_stat.st_mtime)):
            result["skipped"].append(name)
            continue
        handle, tmp = tempfile.mkstemp(dir=target, prefix=".second-wind-",
                                       suffix=".tmp")
        os.close(handle)
        try:
            shutil.copyfile(src, tmp)
            # mode and times together: the mode carries the executable bit, and
            # the mtime is what makes the next sync skip this file.
            shutil.copystat(src, tmp)
            os.replace(tmp, dst)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        result["copied"].append(name)
    return result


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
    # Ordered before the expiry test: a profile that never had a sign-in is a
    # different fault from one whose token ran out, and only the first is
    # explained by the credential living in the desktop app.
    if "NO CLI SIGN-IN" in upper:
        return "nologin"
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
        "nologin": "no CLI sign-in",
        "trust": "trust prompt blocked",
        "parser": "parser mismatch",
        "failed": "refresh failed",
        "none": "no reading yet",
    }.get(kind, "unknown")


def status_phrase(kind):
    """A sentence fragment for the session brief."""
    return {
        "login": "login expired, sign in again",
        "nologin": "no Claude Code sign-in for its reader profile",
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
    if role in CLAUDE_ROLES or role in CODEX_ROLES:
        windows = [w for w in (number(usage.get("five_hour_pct")),
                               number(usage.get("seven_day_pct"))) if w is not None]
        # A Free Codex workspace has one monthly window and no other. It counts,
        # because a role that has dropped to Free still has to sort somewhere.
        if not windows and role in CODEX_ROLES:
            monthly = pool(usage, "monthly_pct")
            windows = [monthly] if monthly is not None else []
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
    if not parts and role in CODEX_ROLES and pool(usage, "monthly_pct") is not None:
        parts.append("monthly %d%%" % round(pool(usage, "monthly_pct")))
    return ", ".join(parts)


def usage_summary(role, cfg=None, now=None):
    """What the brief says about one account, after its name.

    Fresh or stale readings show the figures and their age. A dead or missing
    reading says so and says a refresh is coming, because a stale percentage
    read as current is the failure this whole tool exists to avoid. The picker's
    destination rows use the same wording, so a person reads one phrasing.
    """
    cfg = load_config() if cfg is None else cfg
    if not reading_enabled(role, cfg):
        return "no usage reading available for this sign-in"
    usage = load_usage(role)
    state = freshness(role, now=now, cfg=cfg)
    phrase = status_phrase(status_kind(role))
    figures = _windows_text(role, usage) if state in ("fresh", "stale") else ""
    if not figures:
        return phrase or "no current reading, refreshing"
    line = "%s (%s ago)" % (figures, short_age(cache_age(role, now=now)))
    if phrase:
        line += ", " + phrase
    return line


def brief_lines(cfg=None, now=None):
    """One honest line per enabled account, for the session brief."""
    cfg = load_config() if cfg is None else cfg
    return ["%s: %s" % (role_label(role, cfg), usage_summary(role, cfg=cfg, now=now))
            for role in enabled_roles(cfg)]


# ------------------------------------------------------------------ the card

# The card is drawn here, in Python, and not described to a model in prose. The
# reason is uniformity: prose asking for "small bars" produced a different
# picture every session, with the columns wandering and 98% drawn as full. A
# reading is a number, so its picture should be a function of that number and
# nothing else.
BAR_CELLS = 12
BAR_FULL = "█"                 # a filled cell
BAR_EMPTY = "░"                # spent nothing here yet
BAR_ABSENT = "·"               # this client reports no such window
BAR_EIGHTHS = "▏▎▍▌▋▊▉"  # 1/8 .. 7/8
BAR_CHARS = BAR_FULL + BAR_EMPTY + BAR_ABSENT + BAR_EIGHTHS


def usage_bar(pct, cells=BAR_CELLS):
    """A fixed-width bar for one percentage. Same number, same picture, always.

    Three guarantees, each of them an honesty rule rather than a style choice:
    0 draws empty, 100 draws full, and anything in between draws neither. That
    last one is why the eighth-block characters are here at all: 98% rounded to
    whole cells is indistinguishable from 100%, and the whole point of the panel
    is to tell those two apart. None means the client reported no such window,
    which is not the same as zero and must not look like it.
    """
    if pct is None:
        return BAR_ABSENT * cells
    pct = min(100.0, max(0.0, float(pct)))
    eighths = int(round(pct / 100.0 * cells * 8))
    if pct > 0:
        eighths = max(1, eighths)
    if pct < 100:
        eighths = min(cells * 8 - 1, eighths)
    full, remainder = divmod(eighths, 8)
    bar = BAR_FULL * full
    if remainder:
        bar += BAR_EIGHTHS[remainder - 1]
    return bar + BAR_EMPTY * (cells - len(bar))


def _pct_cell(pct):
    return "n/a".rjust(4) if pct is None else ("%d%%" % round(pct)).rjust(4)


def model_week_note(usage):
    """A weekly window named after one model, for the end of that role's row.

    Some plans carry a per-model week alongside the all-models one, and it can
    be much the fuller of the two. It is not shown as a bar because it is not
    one of the two columns, and putting it in a column would invite reading it
    as the weekly figure. Shown highest first, because that is the one that
    decides whether to change model.
    """
    weeks = (usage.get("extra") or {}).get("model_weeks") or {}
    rows = []
    for name, window in weeks.items():
        pct = number((window or {}).get("pct"))
        if pct is not None:
            rows.append((pct, "%s week %d%%" % (name, round(pct))))
    return ", ".join(text for _, text in sorted(rows, reverse=True))


# ------------------------------------------------------------ field notes

# A test round's diary: what setup did, what the readers reported over time,
# what routed where, and what the tester had to do to get past something. It
# carries process, never content: no prompt, no reply, no directory name, no
# address. Off unless the config says so, and the report is the only reader.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def field_notes_enabled(cfg=None):
    cfg = load_config() if cfg is None else cfg
    return (cfg.get("field_notes") or {}).get("enabled") is True


def field_notes_path():
    return os.path.join(sw_home(), "field-notes.jsonl")


def scrub(value):
    """Addresses become <account> and the home directory becomes ~, in every
    string of a nested value. Run at write time, so the file on disk is already
    safe to send rather than safe only after a report has been over it."""
    if isinstance(value, str):
        if HOME and HOME != "/":
            value = value.replace(HOME, "~")
        return EMAIL_RE.sub("<account>", value)
    if isinstance(value, dict):
        return {str(k): scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


# What a diary row may carry, by event, and how each value is coerced. Anything
# not listed here is dropped before it reaches the file, whatever a caller passed.
# The promise to testers ("no prompt, reply, path, project name or address") is
# kept by this table, not by the care of each call site.
_WORD = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
_HOME_DOTDIR = re.compile(r"^~/\.[A-Za-z0-9_.-]{1,60}$")
_KEPT_VALUES = {"on", "off", "auto", "review", "work", "relief", "worker", "reviewer"}
# The settings carry() can report as changed. Names, never values.
FIELD_SETTINGS = {
    "primary label", "secondary label", "five-hour threshold", "seven-day threshold",
    "refresh interval", "timeout", "model picker", "picker routes", "picker models",
    "codex directory", "codex2 directory", "codex3 directory",
    "codex label", "codex2 label", "codex3 label", "codex full access", "field notes",
}
FIELD_EVENTS = {
    "setup": {"command": "word", "args": "args", "result": "int"},
    "write": {"level": "word", "workers": "roles", "changed": "settings", "forced": "bool"},
    "check": {"faults": "codes", "warnings": "codes"},
    "refresh": {"outcomes": "outcomes"},
    "route": {"source": "word", "worker": "role", "model": "token", "effort": "token",
              "mode": "word"},
    "handover": {"reason": "word", "windows": "words", "workers": "roles"},
    "note": {"text": "text"},
}
# A --check line reduced to what kind of thing it reports. The line itself can
# name a directory, an account or a version, so only the kind is kept.
FAULT_CODES = (
    ("signed in as", "sign-in-changed"),
    ("not trusted", "workdir-not-trusted"), ("does not trust", "workdir-not-trusted"),
    ("does not exist", "directory-missing"),
    ("not on path", "command-missing"),
    ("not signed in", "not-signed-in"), ("login", "not-signed-in"),
    ("no cli sign-in", "not-signed-in"),
    ("status line", "statusline"),
    ("hook", "hooks"),
    ("launchd", "launchd"), ("scheduled", "launchd"),
    ("reading is", "reading-stale"), ("over an hour old", "reading-stale"),
    ("now,", "client-version-moved"),
    ("jq", "jq-missing"),
    ("no-failover", "failover-off"),
)


def fault_code(text):
    lowered = str(text or "").lower()
    for needle, code in FAULT_CODES:
        if needle in lowered:
            return code
    return "other"


def safe_args(argv):
    """A setup command line as the diary keeps it. Flags stay. A value stays
    only when it is one of the fixed words setup takes, a number, or a dotfile
    directory directly under the home folder written as ~/.name. Everything
    else, a label, a path anywhere else, a picker string, becomes <value> or
    <path>. `--flag=value` is split so the value gets the same treatment."""
    out = []
    for arg in [str(a) for a in (argv or [])]:
        if arg.startswith("--"):
            flag, sep, value = arg.partition("=")
            out.append(flag if re.match(r"^--[a-z0-9-]{1,40}$", flag) else "<flag>")
            if sep:
                out.append(_safe_value(value))
            continue
        out.append(_safe_value(arg))
    return out


def _safe_value(value):
    text = str(value)
    if text in _KEPT_VALUES or re.match(r"^[0-9]{1,6}$", text):
        return text
    if text.startswith(("/", "~")):
        short = tilde(text)
        return short if _HOME_DOTDIR.match(short) else "<path>"
    return "<value>"


def _coerce(kind, value):
    if value is None:
        return None
    if kind == "word":
        return value if isinstance(value, str) and _WORD.match(value) else "<value>"
    if kind == "words":
        return [v for v in (_coerce("word", x) for x in value) if v] \
            if isinstance(value, (list, tuple)) else []
    if kind == "int":
        return int(value) if isinstance(value, (int, bool)) else None
    if kind == "bool":
        return bool(value)
    if kind == "role":
        return value if value in ROLES else "<role>"
    if kind == "roles":
        return [v for v in value if v in ROLES] if isinstance(value, (list, tuple)) else []
    if kind == "token":
        return value if isinstance(value, str) and _ROUTE_TOKEN.match(value) else "<value>"
    if kind == "settings":
        return [v for v in value if isinstance(v, str) and v in FIELD_SETTINGS] \
            if isinstance(value, (list, tuple)) else []
    if kind == "codes":
        return [fault_code(v) for v in value] if isinstance(value, (list, tuple)) else []
    if kind == "outcomes":
        return {role: (kind_ if kind_ in STATUS_KINDS else "other")
                for role, kind_ in value.items() if role in ROLES} \
            if isinstance(value, dict) else {}
    if kind == "args":
        return safe_args(value)
    if kind == "text":
        return scrub(str(value))
    return None


def field_note(event, cfg=None, **data):
    """Append one event when field notes are on. Never raises: this runs inside
    hooks and readers, and a diary must not take a session down. An event or a
    key not in FIELD_EVENTS is dropped, not written."""
    try:
        cfg = load_config() if cfg is None else cfg
        if not field_notes_enabled(cfg):
            return False
        allowed = FIELD_EVENTS.get(str(event))
        if allowed is None:
            return False
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "event": str(event)}
        for key, kind in allowed.items():
            if key in data:
                row[key] = _coerce(kind, data[key])
        os.makedirs(sw_home(), exist_ok=True)
        # Created with the mode it will keep, rather than chmod'd after a write
        # under whatever umask the calling hook happened to have.
        fd = os.open(field_notes_path(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as handle:
            handle.write(json.dumps(row) + "\n")
        return True
    except Exception:
        return False


def load_field_notes():
    rows = []
    try:
        with open(field_notes_path()) as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def field_note_refresh(cfg=None):
    """One event per refresh run: the status kind each reader reported. The
    status files are overwritten every run, so without this the diary would
    only ever hold the latest reading."""
    cfg = load_config() if cfg is None else cfg
    outcomes = {}
    for role in enabled_roles(cfg):
        if reading_enabled(role, cfg):
            outcomes[role] = status_kind(role)
    return field_note("refresh", cfg=cfg, outcomes=outcomes)


def brief_card(cfg=None, now=None):
    """The usage panel, as lines to be reproduced verbatim.

    One row per enabled account, a fixed label column, and the same two windows
    in the same two columns on every row, so the rows can be compared by eye.
    Cursor spans, because monthly pools are not the 5-hour and weekly windows
    and pretending otherwise would file a number under the wrong heading. Rows
    with no usable reading carry the reason instead of a bar, never a stale
    figure redrawn as if it were current.
    """
    cfg = load_config() if cfg is None else cfg
    roles = enabled_roles(cfg)
    if not roles:
        return []
    labels = {role: role_label(role, cfg) for role in roles}
    width = max(len(label) for label in labels.values())

    rows, ages = [], []
    for role in roles:
        label = labels[role].ljust(width)
        if not reading_enabled(role, cfg):
            rows.append("%s  no usage reading available for this sign-in" % label)
            continue
        usage = load_usage(role)
        state = freshness(role, now=now, cfg=cfg)
        if state not in ("fresh", "stale"):
            reason = status_phrase(status_kind(role)) or "no current reading, refreshing"
            rows.append("%s  %s" % (label, reason))
            continue
        age = cache_age(role, now=now)
        if age is not None:
            ages.append(age)
        if role == "cursor":
            pools = ", ".join(
                "%s %d%%" % (name, round(value))
                for name, key in (("included", "included_pct"), ("auto", "auto_pct"),
                                  ("api", "api_pct"))
                for value in [pool(usage, key)] if value is not None)
            rows.append("%s  monthly pools: %s" % (label, pools or "none reported"))
            continue
        five, week = number(usage.get("five_hour_pct")), number(usage.get("seven_day_pct"))
        notes = [status_phrase(status_kind(role)), identity_drift(role, cfg, now=now),
                 model_week_note(usage)]
        if five is None and week is None and role in CODEX_ROLES \
                and pool(usage, "monthly_pct") is not None:
            # A Free workspace: one monthly window, which is not the 5-hour or
            # the weekly column, so it spans like the Cursor pools do.
            row = "%s  monthly limit %d%% used" % (label, round(pool(usage, "monthly_pct")))
            rows.append("  ".join([row] + [note for note in notes if note]))
            continue
        cells = [usage_bar(five), usage_bar(week)]
        pcts = [_pct_cell(five), _pct_cell(week)]
        row = "%s  %s %s  %s %s" % (label, cells[0], pcts[0], cells[1], pcts[1])
        # A reader fault or a changed sign-in goes on the row it belongs to. The
        # figures are still shown, because they are real; what the note says is
        # whose figures they are.
        rows.append("  ".join([row] + [note for note in notes if note]))

    header = "%s  %s  %s" % (" " * width,
                             "5-hour".ljust(BAR_CELLS + 5),
                             "weekly".ljust(BAR_CELLS + 5))
    lines = [header.rstrip()] + rows
    if ages:
        oldest, newest = short_age(max(ages)), short_age(min(ages))
        lines.append("Readings %s old." % oldest if oldest == newest
                     else "Readings %s to %s old." % (newest, oldest))
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


# ---------------------------------------------------------------- command line


def _main(argv):
    """Two jobs, both for usage-refresh.sh: keep the runtime mirror current when
    it is running from the skill folder, and record one field-notes event per
    refresh run."""
    if len(argv) == 2 and argv[1] == "--field-note-refresh":
        return 0 if field_note_refresh() or not field_notes_enabled() else 1
    if len(argv) == 3 and argv[1] == "--sync-runtime":
        try:
            sync_runtime(argv[2])
        except Exception as problem:
            sys.stderr.write("swlib: could not mirror the runtime files: %s\n"
                             % problem)
            return 1
        return 0
    sys.stderr.write("usage: swlib.py --sync-runtime <skill directory> | "
                     "--field-note-refresh\n")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
