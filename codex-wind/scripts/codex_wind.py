#!/usr/bin/env python3
"""Delegate through official clients, with a separate Codex Wind configuration."""

from __future__ import annotations

import argparse
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
    return Path(os.environ.get("CODEX_WIND_HOME", "~/.codex-wind")).expanduser()


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
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("accounts"), dict):
        raise WindError(f"unsupported configuration in {path}")
    return data


def account(config: dict[str, Any], name: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise WindError("account names must be 1-64 letters, numbers, hyphens or underscores")
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
    return value


def clean_env(profile: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    for key in VENDOR_KEYS:
        env.pop(key, None)
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
            raise WindError(f"{kind} profile directory does not exist: {configured}")
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
) -> None:
    rendered_command = " ".join(command)
    text = (
        "# Codex Wind exchange\n\n"
        f"- Worker: {name} ({profile['label']})\n"
        f"- Account: {status['email']}\n"
        f"- Subscription: {status.get('subscriptionType') or 'unknown'}\n"
        f"- Workspace: {workspace}\n"
        f"- Mode: {mode}\n"
        f"- Exit: {returncode}\n"
        f"- Duration seconds: {elapsed:.1f}\n"
        f"- Command: `{rendered_command}`\n\n"
        "## Prompt\n\n"
        f"{prompt.rstrip()}\n\n"
        "## Response\n\n"
        f"{output.rstrip()}\n"
    )
    if diagnostics:
        text += "\n## Client diagnostics (stderr)\n\n" + diagnostics[:200_000].rstrip() + "\n"
        if len(diagnostics) > 200_000:
            text += f"\nDiagnostics truncated; original length {len(diagnostics)} characters.\n"
    path.write_text(text)
    path.chmod(0o600)


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


def command_import(args: argparse.Namespace) -> int:
    """Copy labels and profile locations only, never credentials or hooks."""
    source = Path(args.config).expanduser()
    data = json.loads(source.read_text())
    if data.get("version") != 4:
        raise WindError("import expects a Second Wind version 4 config")
    config = load_config() if config_path().exists() else {"version": 1, "accounts": {}}
    for role in ("primary", "secondary", "codex", "codex2", "codex3", "grok", "cursor"):
        entry = data.get(role) or {}
        if role != "primary" and entry.get("enabled") is not True:
            continue
        kind = "claude" if role in ("primary", "secondary") else "codex" if role.startswith("codex") else role
        directory = entry.get("config_dir") or ("~/.codex" if kind == "codex" else "")
        if kind == "claude" and (not directory or "@" not in str(entry.get("account", ""))):
            print(f"SKIPPED {role}: no pinned email or profile directory; configure it before importing", file=sys.stderr)
            continue
        matches = [name for name, old in config["accounts"].items()
                   if old.get("kind", "claude") == kind and
                   (kind not in ("claude", "codex") or
                    Path(old.get("config_dir", "")).expanduser().resolve() == Path(directory).expanduser().resolve())]
        name = matches[0] if matches else {"primary": "claude-work", "secondary": "claude-personal"}.get(role, role)
        proposed = dict(config["accounts"].get(name, {}), kind=kind, label=entry.get("label", name))
        if directory:
            proposed["config_dir"] = directory
        email = entry.get("account", "")
        if "@" in email:
            proposed["expected_email"] = email
        if kind == "claude" and "expected_email" not in proposed:
            print(f"SKIPPED {role}: no pinned email; configure it before importing", file=sys.stderr)
            continue
        # Existing work access is kept; an import never grants new full access.
        proposed["full_access"] = config["accounts"].get(name, {}).get("full_access", False)
        if name in config["accounts"] and name not in matches:
            raise WindError(f"import would overwrite a different account named {name}")
        config["accounts"][name] = proposed
    for name in config["accounts"]:
        account(config, name)
    root = home_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=root)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        if config_path().exists():
            backup = root / f"config.backup-{time.time_ns()}.json"
            shutil.copy2(config_path(), backup)
            backup.chmod(0o600)
        os.replace(temporary, config_path())
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(f"Configured {len(config['accounts'])} accounts in {config_path()}. Run status to verify them.")
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

    # Reserve a private record before spending allowance. A failed worker or
    # empty final message must be recorded with the same exit code we return.
    path = exchange_path(config, args.account, workspace)
    with path.open("x"):
        pass
    path.chmod(0o600)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="codex-wind-") as temporary:
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
        print("codex-wind: worker returned an empty response", file=sys.stderr)
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
        diagnostics=diagnostics,
    )
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    if diagnostics and returncode:
        print(diagnostics[-4000:], file=sys.stderr)
    elif diagnostics:
        print(f"codex-wind: client diagnostics retained in exchange ({len(diagnostics)} characters)", file=sys.stderr)
    if timed_out:
        print(f"codex-wind: worker timed out after {timeout}s", file=sys.stderr)
    print(f"codex-wind: exchange {path}", file=sys.stderr)
    return returncode


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="codex-wind")
    commands = root.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="verify configured subscription identities without a model request")
    status.add_argument("account", nargs="?")
    status.set_defaults(handler=command_status)

    imp = commands.add_parser("import-second-wind", help="copy account labels and directories; never credentials, settings or routes")
    imp.add_argument("--config", default="~/.second-wind/config.json")
    imp.set_defaults(handler=command_import)

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
        print(f"codex-wind: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
