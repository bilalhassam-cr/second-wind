#!/usr/bin/env python3
"""What run.sh hands to a worker, and what it says it handed over.

run.sh is driven as a real shell script here. The worker itself is a stub on
PATH that writes its own environment to a file and prints a line, so the test
sees exactly what the client would have seen without any client being started
and without any subscription being spent. SW_HOME, the log directory and the
whole environment point at a temporary directory.
"""
import glob
import json
import os
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts")
RUN = os.path.join(SCRIPTS, "run.sh")
VENDOR_KEYS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
               "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "XAI_API_KEY",
               "CURSOR_API_KEY")
STUB = """#!/bin/sh
env > "$SW_TEST_ENV"
echo 'stub worker reply'
"""


@unittest.skipUnless(shutil.which("jq"), "run.sh needs jq")
class Credentials(unittest.TestCase):
    """Every worker gets every vendor key cleared, whoever the key belongs to.

    Clearing only the worker's own vendor key left the other five in the
    environment of a process that can read its own environment, which is a key
    handed to somebody it was never issued for.
    """

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="second-wind-run-")
        self.bin = os.path.join(self.home, "bin")
        self.log = os.path.join(self.home, "log")
        self.work = os.path.join(self.home, "work")
        for folder in (self.bin, self.work):
            os.makedirs(folder)
        for name in ("claude", "codex", "grok", "cursor-agent"):
            path = os.path.join(self.bin, name)
            with open(path, "w") as handle:
                handle.write(STUB)
            os.chmod(path, 0o755)
        self.prompt = os.path.join(self.home, "prompt.txt")
        with open(self.prompt, "w") as handle:
            handle.write("say something short\n")
        self.seen = os.path.join(self.home, "worker-env.txt")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def write_config(self, cursor_auth="interactive"):
        cfg = {
            "version": 4,
            "level": "relief",
            "primary": {"config_dir": "~/.claude", "account": "user@example.com"},
            "secondary": {"enabled": True, "config_dir": "~/.claude-secondary",
                          "account": "user@example.com"},
            "codex": {"enabled": True, "account": "user@example.com"},
            "grok": {"enabled": True, "account": "user@example.com"},
            "cursor": {"enabled": True, "account": "user@example.com",
                       "auth": cursor_auth},
            "defaults": {"mode": "review"},
            "log": {"dir": self.log, "max_exchange_kb": 200, "prune_days": 30},
            "timeout_seconds": 60,
        }
        if cursor_auth is None:
            cfg["cursor"].pop("auth")
        with open(os.path.join(self.home, "config.json"), "w") as handle:
            json.dump(cfg, handle)

    def run_worker(self, worker):
        environment = dict(os.environ)
        environment["SW_HOME"] = self.home
        environment["PATH"] = self.bin + os.pathsep + environment.get("PATH", "")
        environment["SW_TEST_ENV"] = self.seen
        for name in VENDOR_KEYS:
            environment[name] = "test-value-for-%s" % name
        done = subprocess.run(["/bin/sh", RUN, worker, self.prompt, "--review"],
                              cwd=self.work, capture_output=True, text=True,
                              timeout=120, env=environment)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done

    def worker_saw(self):
        """The vendor keys the stub actually found in its own environment."""
        with open(self.seen) as handle:
            names = set()
            for line in handle:
                name = line.split("=", 1)[0]
                if name in VENDOR_KEYS:
                    names.add(name)
        return names

    def exchange(self):
        files = glob.glob(os.path.join(self.log, "*.md"))
        self.assertEqual(len(files), 1, files)
        with open(files[0]) as handle:
            return handle.read()

    def header(self, text, label):
        for line in text.splitlines():
            if line.startswith("- %s:" % label):
                return line.split(":", 1)[1].strip()
        self.fail("no %s line in the exchange" % label)

    def test_a_codex_run_is_handed_no_vendor_key_at_all(self):
        self.write_config()
        self.run_worker("codex")
        self.assertEqual(self.worker_saw(), set())
        text = self.exchange()
        self.assertEqual(self.header(text, "Credentials cleared").split(),
                         list(VENDOR_KEYS))
        self.assertEqual(self.header(text, "Credentials kept"), "none")

    def test_a_claude_run_is_handed_no_vendor_key_either(self):
        # The one that used to keep four of them, including the two keys for a
        # vendor this worker has no account with.
        self.write_config()
        self.run_worker("secondary")
        self.assertEqual(self.worker_saw(), set())
        # The header says what the client was actually started with, for every
        # worker, not only for Codex.
        self.assertEqual(self.header(self.exchange(), "Sandbox"),
                         "read-only tools, no MCP servers")

    def test_a_grok_run_is_handed_no_vendor_key_either(self):
        self.write_config()
        self.run_worker("grok")
        self.assertEqual(self.worker_saw(), set())

    def test_an_api_key_cursor_keeps_its_own_key_and_nothing_else(self):
        # An API key is one of Cursor's two sign-ins, so clearing it would break
        # the delegation. It is the only key any worker is allowed to keep.
        self.write_config(cursor_auth="api_key")
        self.run_worker("cursor")
        self.assertEqual(self.worker_saw(), {"CURSOR_API_KEY"})
        text = self.exchange()
        cleared = self.header(text, "Credentials cleared").split()
        self.assertNotIn("CURSOR_API_KEY", cleared)
        self.assertEqual(set(cleared), set(VENDOR_KEYS) - {"CURSOR_API_KEY"})
        self.assertEqual(self.header(text, "Credentials kept"),
                         "CURSOR_API_KEY, this worker's own sign-in")

    def test_an_unrecorded_cursor_sign_in_keeps_the_key_too(self):
        # A config written before the auth field existed is more likely an old
        # config than a bill nobody meant to run up.
        self.write_config(cursor_auth=None)
        self.run_worker("cursor")
        self.assertEqual(self.worker_saw(), {"CURSOR_API_KEY"})
        self.assertEqual(self.header(self.exchange(), "Credentials kept"),
                         "CURSOR_API_KEY, this worker's own sign-in")

    def test_an_interactive_cursor_is_handed_no_key(self):
        # The browser session pays, so a key in the environment could only bill
        # somebody twice.
        self.write_config(cursor_auth="interactive")
        self.run_worker("cursor")
        self.assertEqual(self.worker_saw(), set())
        text = self.exchange()
        self.assertEqual(self.header(text, "Credentials cleared").split(),
                         list(VENDOR_KEYS))
        self.assertEqual(self.header(text, "Credentials kept"), "none")




ARGS_STUB = """#!/bin/sh
env > "$SW_TEST_ENV"
printf '%s\\n' "$@" > "$SW_TEST_ARGS"
echo 'stub worker reply'
"""


@unittest.skipUnless(shutil.which("jq"), "run.sh needs jq")
class CodexHomes(unittest.TestCase):
    """Two Codex roles, each under its own CODEX_HOME, and the full-access flag.

    The stub for codex also records the arguments it was started with, so the
    test sees the sandbox flag the client would have seen. Not a subclass of
    Credentials: that would run its tests again against this config.
    """

    tearDown = Credentials.tearDown
    exchange = Credentials.exchange
    header = Credentials.header

    def setUp(self):
        Credentials.setUp(self)
        path = os.path.join(self.bin, "codex")
        with open(path, "w") as handle:
            handle.write(ARGS_STUB)
        os.chmod(path, 0o755)
        self.args = os.path.join(self.home, "worker-args.txt")
        self.codex2_home = os.path.join(self.home, "codex-personal")
        os.makedirs(self.codex2_home)

    def write_config(self, full_access=False, codex2_dir=None, mode="review"):
        cfg = {
            "version": 4,
            "level": "relief",
            "primary": {"config_dir": "~/.claude", "account": "user@example.com"},
            "secondary": {"enabled": False},
            "codex": {"enabled": True, "account": "user@example.com",
                      "config_dir": "~/.codex", "full_access": full_access},
            "codex2": {"enabled": True, "account": "user2@example.com",
                       "config_dir": codex2_dir or self.codex2_home,
                       "full_access": full_access},
            "grok": {"enabled": False},
            "cursor": {"enabled": False},
            "defaults": {"mode": mode},
            "log": {"dir": self.log, "max_exchange_kb": 200, "prune_days": 30},
            "timeout_seconds": 60,
        }
        with open(os.path.join(self.home, "config.json"), "w") as handle:
            json.dump(cfg, handle)

    def run_codex(self, worker, flag="--review", expect=0):
        environment = dict(os.environ)
        environment["SW_HOME"] = self.home
        environment["PATH"] = self.bin + os.pathsep + environment.get("PATH", "")
        environment["SW_TEST_ENV"] = self.seen
        environment["SW_TEST_ARGS"] = self.args
        # A CODEX_HOME already in the caller's environment must not leak into
        # the role that owns the default directory.
        environment["CODEX_HOME"] = "/should/not/leak"
        done = subprocess.run(["/bin/sh", RUN, worker, self.prompt, flag],
                              cwd=self.work, capture_output=True, text=True,
                              timeout=120, env=environment)
        self.assertEqual(done.returncode, expect, done.stderr)
        return done

    def env_seen(self):
        with open(self.seen) as handle:
            return dict(line.rstrip("\n").split("=", 1) for line in handle if "=" in line)

    def args_seen(self):
        with open(self.args) as handle:
            return handle.read().split("\n")

    def test_the_second_codex_runs_under_its_own_home(self):
        self.write_config()
        self.run_codex("codex2")
        self.assertEqual(self.env_seen().get("CODEX_HOME"), self.codex2_home)
        self.assertEqual(self.header(self.exchange(), "Account"), "user2@example.com")

    def test_the_first_codex_runs_with_the_variable_unset(self):
        self.write_config()
        self.run_codex("codex")
        self.assertNotIn("CODEX_HOME", self.env_seen())

    def test_a_missing_home_stops_before_the_client_starts(self):
        self.write_config(codex2_dir=os.path.join(self.home, "nowhere"))
        done = self.run_codex("codex2", expect=2)
        self.assertIn("does not exist", done.stderr)
        self.assertIn("codex login", done.stderr)
        self.assertFalse(os.path.exists(self.seen), "the stub must not have run")

    def test_review_mode_is_read_only_whatever_full_access_says(self):
        self.write_config(full_access=True)
        self.run_codex("codex2", "--review")
        args = self.args_seen()
        self.assertIn("read-only", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        text = self.exchange()
        self.assertEqual(self.header(text, "Browser guard applied"), "yes")
        self.assertEqual(self.header(text, "Sandbox"), "read-only")

    def test_work_mode_without_full_access_keeps_the_sandbox_and_the_guard(self):
        self.write_config(full_access=False)
        self.run_codex("codex2", "--work")
        args = self.args_seen()
        self.assertIn("--approve-for-me", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        text = self.exchange()
        self.assertEqual(self.header(text, "Browser guard applied"), "yes")
        self.assertEqual(self.header(text, "Sandbox"), "workspace-write")
        self.assertIn("cannot launch a web browser", text)

    def test_work_mode_with_full_access_drops_the_sandbox_and_the_guard(self):
        self.write_config(full_access=True)
        self.run_codex("codex2", "--work")
        args = self.args_seen()
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertNotIn("--approve-for-me", args)
        text = self.exchange()
        self.assertEqual(self.header(text, "Browser guard applied"), "no")
        self.assertEqual(self.header(text, "Sandbox"), "none, full access")
        self.assertNotIn("cannot launch a web browser", text)
        # The header still ends properly: the line that was once lost to a
        # shell parsing quirk is present.
        self.assertEqual(self.header(text, "Credentials kept"), "none")

    def test_an_unsigned_role_is_refused_before_the_client_starts(self):
        # Without this, Codex retries the API five times with no token and the
        # 401s are logged as the reply.
        stub = """#!/bin/sh
if [ "$1" = login ]; then echo 'Not logged in' >&2; exit 1; fi
env > "$SW_TEST_ENV"
echo 'stub worker reply'
"""
        path = os.path.join(self.bin, "codex")
        with open(path, "w") as handle:
            handle.write(stub)
        os.chmod(path, 0o755)
        self.write_config()
        done = self.run_codex("codex2", expect=2)
        self.assertIn("not signed in", done.stderr)
        self.assertIn("CODEX_HOME=%s codex login" % self.codex2_home, done.stderr)
        self.assertFalse(os.path.exists(self.seen), "the client must not have run")
        self.assertEqual(glob.glob(os.path.join(self.log, "*.md")), [])


if __name__ == "__main__":
    unittest.main()
