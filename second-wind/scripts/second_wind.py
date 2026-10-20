#!/usr/bin/env python3
"""Delegate through official clients, with a separate Second Wind configuration."""

from __future__ import annotations

import argparse
import hashlib
import datetime as dt
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any


VENDOR_KEYS = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "XAI_API_KEY",
    "CURSOR_API_KEY",
)


class WindError(RuntimeError):
    pass


def home_dir() -> Path:
    return Path(os.environ.get("SW_HOME") or "~/.second-wind").expanduser()


def config_path() -> Path:
    return home_dir() / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise WindError(f"not configured: {path} does not exist") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise WindError(f"cannot read valid configuration from {path}: {exc}") from exc
    return normalise_config(data)


def normalise_config(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("version") not in (1, 4):
        raise WindError("unsupported configuration; expected Second Wind version 4")
    config = dict(data)
    accounts = {}
    if data["version"] == 4:
        for role in ("primary", "secondary", "codex", "codex2", "codex3", "grok", "cursor"):
            entry = data.get(role) or {}
            if not entry or (role != "primary" and entry.get("enabled") is not True):
                continue
            profile = dict(entry)
            profile.setdefault("label", role)
            profile["kind"] = "claude" if role in ("primary", "secondary") else "codex" if role.startswith("codex") else role
            if profile["kind"] in ("claude", "codex"):
                profile.setdefault("config_dir", "~/.claude" if profile["kind"] == "claude" else "~/.codex")
            if "@" in str(entry.get("account", "")):
                profile["expected_email"] = entry["account"]
            accounts[role] = profile
    extra = data.get("accounts", {})
    if not isinstance(extra, dict):
        raise WindError("accounts must be an object")
    for name, profile in extra.items():
        if name in accounts:
            raise WindError(f"account {name} duplicates a configured role")
        accounts[name] = profile
    log = data.get("log") or {}
    for key, default, minimum in (("max_exchange_kb", 200, 1), ("prune_days", 30, 0)):
        value = log.get(key, default)
        if type(value) is not int or value < minimum:
            raise WindError(f"log.{key} must be an integer of at least {minimum}")
    config["accounts"] = accounts
    config["aliases"] = data.get("aliases", {})
    if not isinstance(config["aliases"], dict):
        raise WindError("aliases must be an object")
    config["log_dir"] = (data.get("log") or {}).get("dir") or data.get("log_dir") or str(home_dir() / "log")
    return config


def account(config: dict[str, Any], name: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise WindError("account names must be 1-64 letters, numbers, hyphens or underscores")
    name = config.get("aliases", {}).get(name, name)
    value = config["accounts"].get(name)
    if not isinstance(value, dict):
        choices = ", ".join(sorted(config["accounts"]))
        raise WindError(f"unknown account {name!r}; choose one of: {choices}")
    kind = value.get("kind", "claude")
    if kind not in ("claude", "codex", "grok", "cursor"):
        raise WindError(f"unsupported client kind: {kind}")
    required = ("label", "config_dir", "expected_email") if kind == "claude" else ("label",)
    for key in required:
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise WindError(f"account {name!r} is missing {key}")
    if kind == "codex" and not value.get("config_dir"):
        raise WindError(f"account {name!r} needs its own config_dir")
    return dict(value, _role=name)


def clean_env(profile: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    for key in VENDOR_KEYS:
        env.pop(key, None)
    if profile.get("kind") == "cursor" and profile.get("auth") == "api_key" and os.environ.get("CURSOR_API_KEY"):
        env["CURSOR_API_KEY"] = os.environ["CURSOR_API_KEY"]
    # Never inherit another role's profile or an API endpoint override.
    for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "ANTHROPIC_BASE_URL",
                "OPENAI_BASE_URL", "CURSOR_API_ENDPOINT", "CLAUDECODE",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_CHILD_SESSION",
                "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID"):
        env.pop(key, None)
    kind = profile.get("kind", "claude")
    if kind in ("claude", "codex"):
        configured = Path(profile["config_dir"]).expanduser().resolve()
        if not configured.is_dir():
            raise WindError(f"{kind} profile directory does not exist: {configured}. Create it and run {kind} login")
        default = Path("~/.claude" if kind == "claude" else "~/.codex").expanduser().resolve()
        if configured != default:
            env["CLAUDE_CONFIG_DIR" if kind == "claude" else "CODEX_HOME"] = str(configured)
    return env


def claude_binary() -> str:
    value = shutil.which("claude")
    if not value:
        raise WindError("claude is not on PATH")
    return value


def auth_status(profile: dict[str, Any]) -> dict[str, Any]:
    kind = profile.get("kind", "claude")
    if kind != "claude":
        return other_auth_status(profile, kind)
    proc = subprocess.run(
        [claude_binary(), "auth", "status", "--json"],
        env=clean_env(profile),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
        raise WindError(f"Claude authentication check failed: {detail}")
    try:
        result = json.loads(proc.stdout[proc.stdout.index("{"):])
    except (ValueError, json.JSONDecodeError) as exc:
        raise WindError("Claude authentication check returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise WindError("Claude authentication check did not return an object")
    if result.get("loggedIn") is not True:
        raise WindError("Claude profile is not signed in")
    if result.get("authMethod") != "claude.ai":
        raise WindError(
            f"Claude profile uses {result.get('authMethod') or 'unknown auth'}, not claude.ai subscription authentication"
        )
    expected = profile["expected_email"].casefold()
    actual = str(result.get("email") or "").casefold()
    if actual != expected:
        raise WindError(
            f"Claude profile identity mismatch: expected {profile['expected_email']}, got {result.get('email') or 'unknown'}"
        )
    return result


def binary(kind: str) -> str:
    command = {"claude": "claude", "codex": "codex", "grok": "grok", "cursor": "cursor-agent"}[kind]
    found = shutil.which(command)
    if not found:
        raise WindError(f"{command} is not on PATH")
    return found


def other_auth_status(profile: dict[str, Any], kind: str) -> dict[str, Any]:
    command = {"codex": ["login", "status"], "grok": ["models"], "cursor": ["status"]}[kind]
    proc = subprocess.run([binary(kind), *command], env=clean_env(profile),
                          text=True, capture_output=True, timeout=30, check=False)
    output = proc.stdout + proc.stderr
    if proc.returncode or re.search(r"not (?:logged|signed) in|unauthenticated|login required", output, re.I):
        raise WindError(f"{kind} authentication check failed: {output.strip()[:1000]}")
    if kind == "cursor" and profile.get("auth") == "api_key":
        if not os.environ.get("CURSOR_API_KEY"):
            raise WindError("Cursor is configured for API-key authentication but CURSOR_API_KEY is absent")
    if kind == "codex" and not re.search(r"logged in.*ChatGPT", output, re.I):
        raise WindError("Codex must report a ChatGPT sign-in, not API-key authentication")
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", output)
    actual = emails[0] if emails else ""
    expected = profile.get("expected_email", "")
    if actual and expected and actual.casefold() != expected.casefold():
        raise WindError(f"{kind} identity mismatch: expected {expected}, got {actual}")
    # These clients do not always disclose their account. Do not invent proof.
    return {"email": actual or "not reported by client", "authMethod": kind,
            "subscriptionType": "authenticated", "identity_verified": bool(actual and expected)}


def codex_review_overrides(profile: dict[str, Any], workspace: Path) -> list[str]:
    """Disable every MCP server the client resolves for this workspace.

    A shell sandbox does not constrain a separately started MCP server. Query
    the official configuration surface; never parse a credential store.
    """
    proc = subprocess.run([binary("codex"), "mcp", "list", "--json"],
                          env=clean_env(profile), cwd=workspace, text=True,
                          capture_output=True, timeout=30, check=False)
    try:
        servers = json.loads(proc.stdout)
        if proc.returncode or not isinstance(servers, list):
            raise ValueError
        flags = ["-c", "apps._default.enabled=false"]
        for server in servers:
            name = server["name"]
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise ValueError
            flags += ["-c", f"mcp_servers.{name}.enabled=false"]
        return flags
    except (ValueError, KeyError, TypeError) as exc:
        raise WindError("cannot list Codex MCP servers; refusing an unverified review boundary") from exc


def run_process(command: list[str], *, env: dict[str, str], cwd: Path, prompt: str, timeout: int) -> tuple[int, str, bool, str]:
    process = subprocess.Popen(
        command,
        env=env,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    previous_term = signal.getsignal(signal.SIGTERM)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    def stop(sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
    try:
        output, diagnostics = process.communicate(prompt, timeout=timeout)
        return process.returncode, output, False, diagnostics
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        stop(signal.SIGTERM)
        try:
            output, diagnostics = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            stop(signal.SIGKILL)
            output, diagnostics = process.communicate()
        if isinstance(exc, KeyboardInterrupt):
            return 130, output, False, diagnostics
        return 124, output, True, diagnostics
    finally:
        signal.signal(signal.SIGTERM, previous_term)


def exchange_path(config: dict[str, Any], name: str, workspace: Path) -> Path:
    raw = config.get("log_dir", str(home_dir() / "exchanges"))
    directory = Path(raw).expanduser()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_mode & 0o777 != 0o700:
        raise WindError(f"exchange directory must be private (mode 700): {directory}")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in workspace.name)[:40]
    return directory / f"{stamp}-{os.getpid()}-{name}-{slug}.md"


def record_exchange(
    path: Path,
    *,
    name: str,
    profile: dict[str, Any],
    status: dict[str, Any],
    workspace: Path,
    mode: str,
    command: list[str],
    prompt: str,
    output: str,
    returncode: int,
    elapsed: float,
    diagnostics: str = "",
    config: dict[str, Any] | None = None,
    model: str = "default",
    effort: str = "default",
) -> None:
    config = config or load_config()
    rendered_command = " ".join(command)
    text = (
        f"# Delegated to {name}\n\n"
        f"- Worker: {name} ({profile['label']})\n"
        f"- Account: {status['email']}\n"
        f"- Subscription: {status.get('subscriptionType') or 'unknown'}\n"
        f"- Working directory: {workspace}\n"
        f"- Workspace: {workspace}\n"
        f"- Mode: {mode}\n"
        f"- Exit code: {returncode}\n"
        f"- Exit: {returncode}\n"
        f"- Duration seconds: {elapsed:.1f}\n"
        f"- Command: `{rendered_command}`\n\n"
        "## Prompt sent\n\n"
        f"{prompt.rstrip()}\n\n"
        "## Reply\n\n"
        f"{output.rstrip()}\n"
    )
    if diagnostics:
        text += "\n## Client diagnostics (stderr)\n\n" + diagnostics[:200_000].rstrip() + "\n"
        if len(diagnostics) > 200_000:
            text += f"\nDiagnostics truncated; original length {len(diagnostics)} characters.\n"
    kind = profile.get("kind", "claude")
    sandbox = ("read-only tools, no MCP servers" if kind == "claude" and mode == "review" else
               "none, bypassPermissions" if kind == "claude" else
               "read-only" if kind == "codex" and mode == "review" else
               "none, full access" if kind == "codex" and profile.get("full_access") is True else
               "workspace-write" if kind == "codex" else mode)
    guard = kind == "codex" and (mode == "review" or profile.get("full_access") is not True)
    kept = "CURSOR_API_KEY" if kind == "cursor" and profile.get("auth") == "api_key" else ""
    cleared = [key for key in (*VENDOR_KEYS, "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY") if key != kept]
    kept_note = f"{kept}, this worker's own sign-in" if kept else "none"
    text = text.replace("## Prompt sent", f"- Sandbox: {sandbox}\n- Browser guard applied: {'yes' if guard else 'no'}\n"
                        f"- Credentials cleared: {' '.join(cleared)}\n"
                        f"- Credentials kept: {kept_note}\n\n## Prompt sent", 1)
    cap = int((config.get("log") or {}).get("max_exchange_kb", 200)) * 1024
    if len(text.encode()) > cap:
        text = text.encode()[:cap].decode("utf-8", errors="ignore") + "\n[Exchange capped on disk; stdout was complete.]\n"
    path.write_text(text)
    path.chmod(0o600)
    row = {"ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "worker": profile.get("_role", name), "account": status["email"], "mode": mode,
           "cwd": str(workspace), "model": model, "effort": effort, "exit": returncode,
           "duration_s": elapsed, "prompt_bytes": len(prompt.encode()), "reply_bytes": len(output.encode()),
           "exchange": str(path), "sandbox": sandbox, "browser_guard": guard,
           "truncated": len(text.encode()) > cap}
    ledger = path.parent / (dt.datetime.now().strftime("%Y-%m") + ".jsonl")
    fd = os.open(ledger, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(row) + "\n").encode())
    finally:
        os.close(fd)
    days = int((config.get("log") or {}).get("prune_days", 30))
    if days > 0:
        for old in path.parent.glob("*.md"):
            if old.stat().st_mtime < time.time() - days * 86400:
                old.unlink()


def command_status(args: argparse.Namespace) -> int:
    config = load_config()
    names = [args.account] if args.account else sorted(config["accounts"])
    failed = False
    for name in names:
        try:
            profile = account(config, name)
            result = auth_status(profile)
            state = "READY " if profile.get("kind", "claude") == "claude" or result.get("identity_verified") else "AUTH  "
            print(
                f"{state} {name:<19} {result['email']}  "
                f"{result.get('subscriptionType') or 'unknown'}  {result['authMethod']}"
            )
        except (WindError, OSError, subprocess.SubprocessError) as exc:
            failed = True
            print(f"BLOCKED {name:<19} {exc}")
    return 3 if failed else 0


def write_config(data: dict[str, Any]) -> None:
    root = home_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=root)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        if config_path().exists():
            backup = root / f"config.backup-{time.time_ns()}.json"
            shutil.copy2(config_path(), backup)
            backup.chmod(0o600)
        os.replace(temporary, config_path())
    finally:
        Path(temporary).unlink(missing_ok=True)


def command_migrate(args: argparse.Namespace) -> int:
    """Merge legacy account metadata; preserve sign-ins, hooks and source logs."""
    source = Path(args.config).expanduser()
    old = json.loads(source.read_text())
    if not isinstance(old, dict) or old.get("version") != 1 or not isinstance(old.get("accounts"), dict):
        raise WindError("migration expects a Codex Wind version 1 config")
    raw = json.loads(config_path().read_text()) if config_path().exists() else {
        "version": 4, "level": "reviewer", "defaults": {"mode": "review"},
        "accounts": {}, "log": {"dir": str(home_dir() / "log"), "max_exchange_kb": 200, "prune_days": 30}}
    if raw.get("version") != 4:
        raise WindError("migration destination must be Second Wind version 4")
    data = json.loads(json.dumps(raw))
    data.setdefault("accounts", {})
    data.setdefault("aliases", {})
    current = normalise_config(data)
    for name, profile in old["accounts"].items():
        account(old, name)
        kind = profile.get("kind", "claude")
        matches = []
        for target, existing in current["accounts"].items():
            if existing.get("kind", "claude") != kind:
                continue
            if kind in ("claude", "codex") and Path(existing.get("config_dir", "")).expanduser().resolve() != Path(profile["config_dir"]).expanduser().resolve():
                continue
            before, incoming = existing.get("expected_email"), profile.get("expected_email")
            if before and incoming and before.casefold() != incoming.casefold():
                raise WindError(f"migration identity conflict for {name}; saved emails differ")
            matches.append(target)
        target = matches[0] if matches else name
        reserved = name in ("primary", "secondary", "codex", "codex2", "codex3", "grok", "cursor") and name in data
        if (name in current["accounts"] or reserved) and (not matches or target != name):
            raise WindError(f"migration account-name conflict: {name}")
        if target == name and not matches:
            if name in data["aliases"]:
                raise WindError(f"migration alias conflict: {name}")
            data["accounts"][name] = profile
        elif name != target:
            if name in data["aliases"] and data["aliases"][name] != target:
                raise WindError(f"migration alias conflict: {name}")
            data["aliases"][name] = target
        if matches and profile.get("expected_email") and not current["accounts"][target].get("expected_email"):
            if target in data["accounts"]:
                data["accounts"][target]["expected_email"] = profile["expected_email"]
            else:
                data[target]["expected_email"] = profile["expected_email"]
                data[target]["account"] = profile["expected_email"]
        current = normalise_config(data)
    # Validate before writing. Migration never changes the original files.
    for name in current["accounts"]:
        account(current, name)
    destination = Path(current["log_dir"]).expanduser()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.stat().st_mode & 0o777 != 0o700:
        raise WindError("shared log directory must be private (mode 700)")
    previous_logs = Path(old.get("log_dir") or source.parent / "exchanges").expanduser()
    originals = [] if previous_logs.resolve() == destination.resolve() else sorted(previous_logs.glob("*.md"))
    for original in originals:
        target = destination / ("legacy-codex-wind-" + original.name)
        if target.exists() and target.read_bytes() != original.read_bytes():
            raise WindError(f"legacy exchange conflict: {target.name}")
    write_config(data)
    copied = 0
    known = set()
    for ledger in destination.glob("*.jsonl"):
        for line in ledger.read_text().splitlines():
            try:
                known.add(json.loads(line).get("migration_id"))
            except ValueError:
                continue
    for original in originals:
        target = destination / ("legacy-codex-wind-" + original.name)
        if target.exists() and target.read_bytes() != original.read_bytes():
            raise WindError(f"legacy exchange conflict: {target.name}")
        # The source has already been authorised for this migration. Copy only
        # exchange documents, never credentials or settings in that directory.
        if not target.exists():
            fd, temporary = tempfile.mkstemp(prefix=".migration-", dir=destination)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(original.read_bytes())
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
            copied += 1
        identity = hashlib.sha256((str(original.resolve()) + "\n").encode() + original.read_bytes()).hexdigest()
        if identity not in known:
            text = original.read_text()
            header = re.split(r"^## ", text, maxsplit=1, flags=re.M)[0]
            fields = dict(re.findall(r"^- ([^:\n]+): (.*)$", header, re.M))
            try:
                when = dt.datetime.strptime(original.name[:15], "%Y%m%d-%H%M%S").astimezone(dt.timezone.utc)
                exit_code = int(fields.get("Exit", "0"))
                duration = float(fields.get("Duration seconds", "0"))
            except ValueError:
                when = dt.datetime.fromtimestamp(original.stat().st_mtime, dt.timezone.utc)
                exit_code, duration = 2, 0
            alias = fields.get("Worker", "legacy").split(" ", 1)[0]
            row = {"ts": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "worker": data.get("aliases", {}).get(alias, alias),
                   "account": fields.get("Account", "unknown"), "mode": fields.get("Mode", "review"),
                   "cwd": fields.get("Workspace", "unknown"), "exit": exit_code, "duration_s": duration,
                   "exchange": str(target), "migration_id": identity, "prompt_bytes": 0,
                   "reply_bytes": 0, "truncated": False, "legacy": True}
            ledger = destination / (when.strftime("%Y-%m") + ".jsonl")
            fd = os.open(ledger, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, (json.dumps(row) + "\n").encode())
            finally:
                os.close(fd)
            known.add(identity)
    print(f"Second Wind configured at {config_path()}; copied {copied} legacy exchanges. Original files kept.")
    return 0


def command_run(args: argparse.Namespace) -> int:
    config = load_config()
    profile = account(config, args.account)
    status = auth_status(profile)
    workspace = Path(args.workspace).expanduser().resolve()
    prompt_path = Path(args.prompt_file).expanduser().resolve()
    if not workspace.is_dir():
        raise WindError(f"workspace is not a directory: {workspace}")
    if not prompt_path.is_file():
        raise WindError(f"prompt file does not exist: {prompt_path}")
    prompt = prompt_path.read_text()
    if not prompt.strip():
        raise WindError("prompt file is empty")

    mode = "work" if args.work else "review"
    timeout = args.timeout if args.timeout is not None else int(config.get("timeout_seconds", 7200))
    if timeout <= 0:
        raise WindError("timeout must be a positive number of seconds")

    kind = profile.get("kind", "claude")
    command = [binary(kind)]
    if kind == "claude":
        command += ["-p", "--add-dir", str(workspace)]
        if mode == "work":
            command += ["--permission-mode", "bypassPermissions"]
        else:
            command += ["--tools", "Read,Glob,Grep", "--settings", '{"disableAllHooks":true}',
            "--disallowed-tools",
            "Edit,Write,NotebookEdit,Bash,BashOutput,KillShell",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
        ]
    elif kind == "codex":
        command += ["exec", "--skip-git-repo-check", "-C", str(workspace)]
        if mode == "review":
            command += ["-s", "read-only", *codex_review_overrides(profile, workspace)]
        elif profile.get("full_access") is True:
            command += ["--dangerously-bypass-approvals-and-sandbox"]
        else:
            command += ["--approve-for-me"]
        if mode == "review" or profile.get("full_access") is not True:
            prompt = "You are sandboxed. Do not launch browsers; return browser verification to the caller.\n\n" + prompt
    elif kind == "grok":
        command += ["--output-format", "json"]
        command += (["--tools", "Read,Glob,Grep", "--disallowed-tools", "Write,Edit,Bash",
                     "--no-subagents", "--disable-web-search"] if mode == "review" else
                    ["--permission-mode", "bypassPermissions"])
    elif kind == "cursor":
        if args.effort:
            raise WindError("Cursor does not support --effort; use a parameterised --model")
        command += ["-p", "--trust"]
        command += ["--mode", "plan"] if mode == "review" else ["--force"]
    if mode == "review":
        prompt = (
            "You are in review mode. Do not edit files or run shell commands that change anything. "
            "Do not claim to have changed anything.\n\n" + prompt
        )
    if args.model:
        command += ["--model", args.model]
    if args.effort:
        if kind == "codex":
            command += ["-c", f'model_reasoning_effort="{args.effort}"']
        else:
            command += ["--reasoning-effort" if kind == "grok" else "--effort", args.effort]

    # A cached, explicitly observed identity/plan change blocks delegation.
    role = profile.get("_role", args.account)
    if config.get("version") == 4 and role in ("primary", "secondary", "codex", "codex2", "codex3", "grok", "cursor"):
        import swlib
        drift = swlib.identity_drift(role, config)
        if drift:
            raise WindError(f"{role} sign-in changed: {drift}; restore it or explicitly repin it")
    # Reserve a private record before spending allowance. A failed worker or
    # empty final message must be recorded with the same exit code we return.
    path = exchange_path(config, args.account, workspace)
    with path.open("x"):
        pass
    path.chmod(0o600)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="second-wind-") as temporary:
        final = Path(temporary) / "final.txt"
        if kind == "codex":
            command += ["-o", str(final)]
        if kind == "grok":
            # Supported by the official client; avoids exposing the prompt in ps.
            prompt_file = Path(temporary) / "prompt.txt"
            prompt_file.write_text(prompt)
            prompt_file.chmod(0o600)
            command += ["--prompt-file", str(prompt_file)]
        try:
            returncode, output, timed_out, diagnostics = run_process(
                command, env=clean_env(profile), cwd=workspace,
                prompt=prompt, timeout=timeout,
            )
            if kind == "codex" and returncode == 0:
                output = final.read_text() if final.is_file() else ""
            if kind == "grok" and returncode == 0:
                try:
                    result = json.loads(output)
                    if not isinstance(result, dict) or not isinstance(result.get("text"), str):
                        raise ValueError("missing final text")
                    output = result["text"]
                    if result.get("stopReason") not in ("end_turn", "stop_sequence"):
                        returncode = 4
                        diagnostics += f"\nGrok did not finish its turn: {result.get('stopReason')}\n"
                except ValueError as exc:
                    returncode, output = 4, ""
                    diagnostics += f"\nInvalid Grok final response: {exc}\n"
        except (OSError, subprocess.SubprocessError) as exc:
            returncode, output, timed_out, diagnostics = 2, "", False, str(exc)
    if returncode == 0 and not output.strip():
        returncode = 4
        print("second-wind: worker returned an empty response", file=sys.stderr)
    elapsed = time.monotonic() - started
    record_exchange(
        path,
        name=args.account,
        profile=profile,
        status=status,
        workspace=workspace,
        mode=mode,
        command=command,
        prompt=prompt,
        output=output,
        returncode=returncode,
        elapsed=elapsed,
        diagnostics=diagnostics, config=config, model=args.model or "default", effort=args.effort or "default",
    )
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    if diagnostics and returncode:
        print(diagnostics[-4000:], file=sys.stderr)
    elif diagnostics:
        print(f"second-wind: client diagnostics retained in exchange ({len(diagnostics)} characters)", file=sys.stderr)
    if timed_out:
        print(f"second-wind: worker timed out after {timeout}s", file=sys.stderr)
    print(f"second-wind: exchange {path}", file=sys.stderr)
    if config.get("version") == 4 and os.environ.get("SW_NO_REFRESH") != "1" and role in ("primary", "secondary", "codex", "codex2", "codex3", "grok", "cursor"):
        refresh = Path(__file__).with_name("usage-refresh.sh")
        with open(os.devnull, "w") as sink:
            subprocess.Popen(["/bin/sh", str(refresh), "--force", "--only", role, "primary"],
                             env=clean_env(profile), stdin=subprocess.DEVNULL, stdout=sink, stderr=sink,
                             start_new_session=True)
    return returncode


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="second-wind")
    commands = root.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="verify configured subscription identities without a model request")
    status.add_argument("account", nargs="?")
    status.set_defaults(handler=command_status)

    migrate = commands.add_parser("migrate-codex-wind", help="merge legacy accounts and copy exchanges into the shared history")
    migrate.add_argument("--config", default="~/.codex-wind/config.json")
    migrate.set_defaults(handler=command_migrate)

    run = commands.add_parser("run", help="delegate one task")
    run.add_argument("account")
    run.add_argument("--prompt-file", required=True)
    run.add_argument("--workspace", required=True)
    access = run.add_mutually_exclusive_group(required=True)
    access.add_argument("--review", action="store_true")
    access.add_argument("--work", action="store_true")
    run.add_argument("--model")
    run.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"))
    run.add_argument("--timeout", type=int)
    run.set_defaults(handler=command_run)
    return root


def main() -> int:
    os.umask(0o077)
    try:
        args = parser().parse_args()
        return args.handler(args)
    except (WindError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"second-wind: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
