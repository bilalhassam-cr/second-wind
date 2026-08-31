#!/usr/bin/env python3
"""Find every supported account and worker command on this machine.

Prints JSON on stdout and changes nothing. Grok Build does not expose a passive
login-status command, so discovery reports its installation state without
starting the client or guessing about authentication.

It reports rather than decides on purpose: which account should be primary is a
judgement about how someone works, not something a script can infer.
"""
import base64, json, os, shutil, subprocess, sys, glob

HOME = os.path.expanduser("~")

def run(cmd, env=None, timeout=25, want_stderr=False):
    try:
        e = dict(os.environ); e.update(env or {})
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=e)
        out = p.stdout.strip()
        if want_stderr and not out:
            out = p.stderr.strip()
        return out
    except Exception:
        return ""

def first_json(text):
    """auth status can be preceded by warnings from MCP servers, so find the object."""
    if not text:
        return {}
    i = text.find("{")
    if i < 0:
        return {}
    try:
        return json.loads(text[i:])
    except Exception:
        return {}

def account_from_file(config_dir):
    """The default profile keeps oauthAccount in ~/.claude.json; a custom config
    dir keeps its own. Never fall back from a custom dir to the default file: a
    fresh or signed-out secondary would then report the PRIMARY account's email,
    and the confirmation step that exists to stop the user getting the two
    backwards would show the wrong account and look right."""
    if os.path.realpath(config_dir) == os.path.realpath(os.path.join(HOME, ".claude")):
        candidates = [os.path.join(HOME, ".claude.json")]
    else:
        candidates = [os.path.join(config_dir, ".claude.json")]
    for f in candidates:
        try:
            with open(f) as fh:
                a = json.load(fh).get("oauthAccount") or {}
            if a.get("emailAddress"):
                return a["emailAddress"], a.get("organizationName", "")
        except Exception:
            continue
    return "", ""

def claude_profiles(claude_bin):
    dirs = []
    d = os.path.join(HOME, ".claude")
    if os.path.isdir(d):
        dirs.append(d)
    for d in sorted(glob.glob(os.path.join(HOME, ".claude-*"))):
        if os.path.isdir(d):
            dirs.append(d)
    out = []
    for d in dirs:
        acct, org = account_from_file(d)
        sub, logged = "", False
        if claude_bin:
            # Never pass CLAUDE_CONFIG_DIR for the default profile. Setting it
            # explicitly to ~/.claude makes the CLI look for a hashed keychain
            # entry that does not exist, and it reports logged_in false for an
            # account that is in fact signed in. Verified on macOS 26.5.
            env = {} if d == os.path.join(HOME, ".claude") else {"CLAUDE_CONFIG_DIR": d}
            st = first_json(run([claude_bin, "auth", "status", "--json"], env))
            logged = bool(st.get("loggedIn"))
            sub = st.get("subscriptionType", "") or ""
            acct = st.get("email", "") or acct
            org = st.get("orgName", "") or org
        out.append({
            "config_dir": d.replace(HOME, "~", 1),
            "account": acct or "unknown",
            "org": org,
            "subscription": sub,
            "logged_in": logged,
            "is_default_dir": d == os.path.join(HOME, ".claude"),
        })
    return out

def codex_info():
    b = shutil.which("codex")
    if not b:
        return {"installed": False}
    # codex writes its login status to stderr, not stdout
    status = run([b, "login", "status"], want_stderr=True).splitlines()
    acct = ""
    try:
        with open(os.path.join(HOME, ".codex", "auth.json")) as fh:
            tok = (json.load(fh).get("tokens") or {}).get("id_token", "")
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        acct = claims.get("email", "")
    except Exception:
        pass
    return {
        "installed": True,
        "bin": b,
        "status": status[0] if status else "",
        "logged_in": bool(status and "logged in" in status[0].lower()),
        "account": acct or "unknown",
    }

def grok_info():
    b = shutil.which("grok")
    if not b:
        return {"installed": False, "logged_in": None}
    return {
        "installed": True,
        "bin": b,
        "logged_in": None,
        "status": "not checked: Grok has no passive login-status command",
    }

def cursor_info():
    b = shutil.which("cursor-agent")
    if not b:
        return {"installed": False, "logged_in": False}
    try:
        p = subprocess.run(
            [b, "status", "--format", "json"], capture_output=True,
            text=True, timeout=25, env=dict(os.environ),
        )
        data = first_json(p.stdout or p.stderr)
    except Exception:
        p, data = None, {}
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    account = (data.get("email") or data.get("account") or
               user.get("email") or "unknown")
    logged = bool(p and p.returncode == 0 and data)
    if os.environ.get("CURSOR_API_KEY"):
        logged = True
    return {
        "installed": True,
        "bin": b,
        "logged_in": logged,
        "account": account,
        "auth": "interactive login or CURSOR_API_KEY" if logged else "not signed in",
    }

def main():
    cb = shutil.which("claude")
    print(json.dumps({
        "claude_bin": cb or "",
        "claude_profiles": claude_profiles(cb),
        "codex": codex_info(),
        "grok": grok_info(),
        "cursor": cursor_info(),
    }, indent=2))

if __name__ == "__main__":
    main()
