#!/usr/bin/env python3
"""Find every supported account and worker command on this machine.

Prints JSON on stdout and changes nothing, and it starts no model prompt.

It reports rather than decides on purpose: which account should be primary is a
judgement about how someone works, not something a script can infer.
"""
import json, os, re, shutil, subprocess, sys, glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swlib

HOME = os.path.expanduser("~")

def run(cmd, env=None, timeout=25, want_stderr=False, drop=()):
    try:
        e = dict(os.environ); e.update(env or {})
        for key in drop:
            e.pop(key, None)
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

def codex_info(home=None):
    """One Codex sign-in. `home` is a CODEX_HOME of its own; None means the
    default ~/.codex, which the client finds without being told."""
    b = shutil.which("codex")
    if not b:
        return {"installed": False}
    env = {"CODEX_HOME": home} if home else None
    # The default home is probed with the variable unset, whatever the shell
    # had, or an inherited CODEX_HOME would report another sign-in as this one.
    drop = () if home else ("CODEX_HOME",)
    if home and not os.path.isdir(home):
        return {"installed": True, "bin": b, "status": "%s does not exist" % home,
                "logged_in": False, "account": "unknown", "config_dir": home}
    # codex writes its login status to stderr, not stdout
    status = run([b, "login", "status"], env=env, want_stderr=True,
                 drop=drop).splitlines()
    # Whatever the status text volunteers, and nothing more. Reading
    # ~/.codex/auth.json to decode the id_token would be opening a credential
    # store to learn a label, which is a line this repository does not cross.
    # Some versions print the account and some print only the sign-in kind, so
    # "unknown" is a normal answer here rather than a fault.
    acct = ""
    for line in status:
        found = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", line)
        if found:
            acct = found.group(0)
            break
    return {
        "installed": True,
        "bin": b,
        "status": status[0] if status else "",
        "logged_in": bool(status and "logged in" in status[0].lower()),
        "account": acct or "unknown",
        "config_dir": home or os.path.join(HOME, ".codex"),
    }

def codex_homes():
    """Every ~/.codex-* directory and whether it is signed in, so the wizard can
    offer what already exists instead of asking for a directory that is there.
    The default ~/.codex is reported by codex_info() and left out here."""
    if not shutil.which("codex"):
        return []
    out = []
    for name in sorted(os.listdir(HOME)):
        path = os.path.join(HOME, name)
        if name.startswith(".codex-") and os.path.isdir(path):
            info = codex_info(path)
            out.append({"config_dir": path, "logged_in": info.get("logged_in", False),
                        "account": info.get("account", "unknown")})
    return out

def grok_info():
    """`grok models` is the login check. Grok has no status command, but listing
    models needs a session, so exit 0 means signed in. It sends no prompt."""
    b = shutil.which("grok")
    if not b:
        return {"installed": False, "logged_in": False}
    try:
        p = subprocess.run([b, "models"], capture_output=True, text=True,
                           timeout=25, env=dict(os.environ))
        logged = p.returncode == 0
        first = (p.stdout or p.stderr or "").strip().splitlines()
    except Exception:
        logged, first = False, []
    return {
        "installed": True,
        "bin": b,
        "logged_in": logged,
        "status": (first[0] if first else
                   ("signed in" if logged else "not signed in")),
    }

def cursor_info():
    """Cursor has two kinds of sign-in and they are not interchangeable.

    An interactive login carries a refresh token and an account, and its usage
    panel can be read. A CURSOR_API_KEY authorises delegation and nothing else:
    the panel needs a session, so usage stays unknown. Setup enables the reader
    for the first kind only."""
    b = shutil.which("cursor-agent")
    if not b:
        return {"installed": False, "logged_in": False, "auth": "none",
                "can_read_usage": False}
    try:
        p = subprocess.run(
            [b, "status", "--format", "json"], capture_output=True,
            text=True, timeout=25, env=dict(os.environ),
        )
        data = first_json(p.stdout or p.stderr)
        rc = p.returncode
    except Exception:
        data, rc = {}, 1
    info = data.get("userInfo") if isinstance(data.get("userInfo"), dict) else {}
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    account = (data.get("email") or data.get("account") or
               info.get("email") or user.get("email") or "")
    interactive = bool(rc == 0 and data.get("isAuthenticated") is not False
                       and (data.get("hasRefreshToken") is True or account))
    api_key = bool(os.environ.get("CURSOR_API_KEY"))
    auth = "interactive" if interactive else ("api_key" if api_key else "none")
    return {
        "installed": True,
        "bin": b,
        "logged_in": interactive or api_key,
        "account": account or "unknown",
        "auth": auth,
        "can_read_usage": interactive,
    }

def main():
    cb = shutil.which("claude")
    print(json.dumps({
        "claude_bin": cb or "",
        "claude_profiles": claude_profiles(cb),
        "codex": codex_info(),
        "codex_homes": codex_homes(),
        "grok": grok_info(),
        "cursor": cursor_info(),
        "client_versions": swlib.client_versions(),
        "jq": swlib.has_jq(),
    }, indent=2))

if __name__ == "__main__":
    main()
