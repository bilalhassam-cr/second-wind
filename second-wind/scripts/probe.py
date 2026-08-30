#!/usr/bin/env python3
"""Test what each worker can actually do, rather than assuming.

The browser probe exists because of a real failure: Codex runs inside a seatbelt
sandbox, and Chrome aborts at launch there (SIGABRT in TransformProcessType)
because it cannot reach the window server. The user sees a "Google Chrome quit
unexpectedly" dialog and nothing explains why. Any project whose instructions say
to verify rendered output in a real browser will trigger it. So we find out once,
record it, and refuse to delegate browser work to a worker that cannot do it.

Prints JSON. Changes nothing except a temporary Chrome profile it removes.
"""
import json, os, shutil, socket, subprocess, sys, tempfile

def probe_browser_in_codex_sandbox(codex_bin):
    """Returns (can_launch, detail). A worker that fails here must never be sent
    browser work, or it crashes a browser on the user's desktop."""
    browsers = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
    ]
    browser = next((b for b in browsers if b and os.path.exists(b)), None)
    if not browser:
        return None, "no Chrome or Chromium found, so nothing to test"
    if not codex_bin:
        return None, "codex not installed"

    # a free port, not a fixed one: a second setup run or anything already
    # listening would otherwise produce a false "inconclusive"
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    tmp = tempfile.mkdtemp(prefix="second-wind-probe-")
    script = os.path.join(tmp, "probe.sh")
    with open(script, "w") as f:
        f.write(
            '#!/bin/sh\n'
            'D=$(mktemp -d)\n'
            # Headed, not headless. The crash this guard exists for is Chrome
            # aborting in TransformProcessType because it cannot reach the window
            # server, and a headless launch never asks the window server for
            # anything. Probing headless would report "can launch" and the real
            # crash would still happen.
            f'"{browser}" --remote-debugging-port={port} '
            '--user-data-dir="$D" --no-first-run --no-default-browser-check '
            '--disable-extensions --disable-gpu about:blank >/dev/null 2>&1 &\n'
            'P=$!\n'
            'sleep 4\n'
            'if kill -0 $P 2>/dev/null; then echo ALIVE; kill $P 2>/dev/null; '
            'else echo DIED; fi\n'
            'rm -rf "$D"\n'
        )
    os.chmod(script, 0o755)
    try:
        p = subprocess.run(
            [codex_bin, "sandbox", "-c", 'sandbox_mode="workspace-write"', "--", script],
            capture_output=True, text=True, timeout=90)
        out = (p.stdout + p.stderr)
        if "ALIVE" in out:
            return True, "browser launched inside the sandbox"
        if "DIED" in out or "Abort" in out:
            return False, "browser aborted inside the sandbox (cannot reach the window server)"
        return None, "probe inconclusive: " + out.strip().splitlines()[-1][:120] if out.strip() else "no output"
    except subprocess.TimeoutExpired:
        return None, "probe timed out"
    except Exception as e:
        return None, f"probe failed: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def probe_browser_direct():
    """The primary session is not sandboxed, so this should always pass. If it
    does not, the machine has no usable browser and neither worker can do
    browser work either."""
    browsers = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome") or "", shutil.which("chromium") or "",
    ]
    b = next((x for x in browsers if x and os.path.exists(x)), None)
    return (b is not None), (b or "no browser found")

def main():
    codex_bin = shutil.which("codex")
    can, detail = probe_browser_in_codex_sandbox(codex_bin)
    direct_ok, direct_detail = probe_browser_direct()
    print(json.dumps({
        "codex": {"can_launch_browser": can, "detail": detail},
        "host": {"browser_present": direct_ok, "detail": direct_detail},
    }, indent=2))

if __name__ == "__main__":
    main()
