#!/usr/bin/env python3
"""Tests for the five hooks and what they say to a session.

Every hook runs here the way Claude Code runs it: as a subprocess, with the
event JSON on stdin, and with SW_HOME pointing at a fixture directory. Nothing
touches the real ~/.second-wind.

Two stubs stand in for the outside world, both on PATH or in the staged copy of
scripts/:

  usage-refresh.sh   records the arguments it was called with, and starts no
                     reader, so a test can prove a hook asked for a refresh
                     without driving a real client
  osascript          records the notification, so the suite proves notify() was
                     called without popping a real one on the machine running it
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
STAGE = None

REFRESH_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$SW_HOME/refresh-args.txt"
"""

OSASCRIPT_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$SW_HOME/osascript-args.txt"
"""

INSTRUCTION_START = "Present these as the session brief before anything else."
INSTRUCTION_END = ("If this message already contains a task, keep the brief to "
                   "one line and start the task.")


def setUpModule():
    """One staged copy of scripts/ for the whole file. The hooks find swlib and
    the refresh wrapper relative to their own path, so the copy is what makes
    the stub wrapper reachable."""
    global STAGE
    STAGE = tempfile.mkdtemp(prefix="sw-hooks-stage-")
    shutil.copytree(SCRIPTS, os.path.join(STAGE, "scripts"))
    refresh = os.path.join(STAGE, "scripts", "usage-refresh.sh")
    with open(refresh, "w") as handle:
        handle.write(REFRESH_STUB)
    os.chmod(refresh, 0o755)
    binaries = os.path.join(STAGE, "bin")
    os.makedirs(binaries)
    stub = os.path.join(binaries, "osascript")
    with open(stub, "w") as handle:
        handle.write(OSASCRIPT_STUB)
    os.chmod(stub, 0o755)


def tearDownModule():
    shutil.rmtree(STAGE, ignore_errors=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="sw-hooks-")
        self.primary_dir = os.path.join(self.home, "profiles", "primary")
        self.secondary_dir = os.path.join(self.home, "profiles", "secondary")
        os.makedirs(self.primary_dir)
        os.makedirs(self.secondary_dir)
        self.now = int(time.time())

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    # ------------------------------------------------------------ fixtures

    def write_config(self, **overrides):
        cfg = {
            "version": 4,
            "level": "relief",
            "primary": {"config_dir": self.primary_dir, "label": "work Claude",
                        "account": "one@example.com"},
            "secondary": {"enabled": True, "config_dir": self.secondary_dir,
                          "label": "spare Claude", "account": "two@example.com"},
            "reader": {"enabled": False},
            "codex": {"enabled": True, "label": "codex"},
            "grok": {"enabled": False},
            "cursor": {"enabled": False},
            "thresholds": {"five_hour_pct": 90, "seven_day_pct": 80},
            "refresh": {"interval_minutes": 15},
            "failover": {"enabled": True, "announce": True},
        }
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
        self.write_json(os.path.join(self.home, "config.json"), cfg)
        return cfg

    def write_json(self, path, data):
        with open(path, "w") as handle:
            json.dump(data, handle)

    def write_usage(self, role, five=10, week=10, age=60, **fields):
        data = {"role": role, "worker": "claude", "five_hour_pct": five,
                "seven_day_pct": week, "cached_at": self.now - age}
        data.update(fields)
        self.write_json(os.path.join(self.home, "usage-%s.json" % role), data)

    def write_status(self, role, line):
        path = os.path.join(self.home, "refresh-status-%s.txt" % role)
        with open(path, "w") as handle:
            handle.write(line + "\n")
        # A status file counts only when it is at least as new as the reading.
        os.utime(path, (self.now + 5, self.now + 5))

    def touch(self, name, text=""):
        path = os.path.join(self.home, name)
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def fresh_house(self):
        """The ordinary state: three enabled accounts, all read recently."""
        self.write_config()
        self.write_usage("primary", five=12, week=20, age=60)
        self.write_usage("secondary", five=3, week=8, age=90)
        self.write_usage("codex", five=5, week=11, age=120)

    # ------------------------------------------------------------ running

    def run_hook(self, name, payload=None, profile="primary", env=None):
        path = os.path.join(STAGE, "scripts", "hooks", name)
        environment = dict(os.environ)
        environment["SW_HOME"] = self.home
        environment["PATH"] = os.path.join(STAGE, "bin") + os.pathsep + \
            environment.get("PATH", "")
        if profile == "primary":
            environment["CLAUDE_CONFIG_DIR"] = self.primary_dir
        elif profile == "secondary":
            environment["CLAUDE_CONFIG_DIR"] = self.secondary_dir
        else:
            environment.pop("CLAUDE_CONFIG_DIR", None)
        environment.update(env or {})
        done = subprocess.run([path], input=json.dumps(payload or {}), text=True,
                              capture_output=True, timeout=20, env=environment)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "", "a hook wrote to stderr: " + done.stderr)
        return done.stdout

    def context(self, stdout, event):
        """The additionalContext a hook emitted, asserting the envelope."""
        self.assertTrue(stdout.strip(), "expected output, got none")
        data = json.loads(stdout)
        block = data["hookSpecificOutput"]
        self.assertEqual(block["hookEventName"], event)
        return block["additionalContext"]

    def wait_for(self, name, seconds=6):
        """A detached refresh writes after the hook has already exited."""
        path = os.path.join(self.home, name)
        deadline = time.time() + seconds
        while time.time() < deadline:
            if os.path.exists(path):
                with open(path) as handle:
                    return handle.read()
            time.sleep(0.05)
        self.fail("%s never appeared" % name)

    def assert_absent(self, name, settle=1.0):
        time.sleep(settle)
        self.assertFalse(os.path.exists(os.path.join(self.home, name)),
                         "%s should not exist" % name)


class SessionStart(Base):
    def test_fresh_readings_brief_and_instruction(self):
        self.fresh_house()
        text = self.context(self.run_hook("session-start.py", {
            "hook_event_name": "SessionStart", "source": "startup"}),
            "SessionStart")
        self.assertIn("work Claude: 5h 12%, 7d 20% (", text)
        self.assertIn(" ago)", text)
        self.assertIn("spare Claude: 5h 3%, 7d 8%", text)
        self.assertIn("codex: 5h 5%, 7d 11%", text)
        self.assertIn(INSTRUCTION_START, text)
        self.assertIn(INSTRUCTION_END, text)
        self.assertNotIn("no current reading", text)
        # Everything is fresh, so nothing is fetched.
        self.assert_absent("refresh-args.txt")

    def test_instruction_is_verbatim(self):
        self.fresh_house()
        text = self.context(self.run_hook("session-start.py", {
            "source": "startup"}), "SessionStart")
        instruction = text.split("\n\n", 1)[1]
        self.assertEqual(instruction, (
            "Present these as the session brief before anything else. If a "
            "widget or inline HTML rendering tool is available in this session, "
            "render them as one compact card: one row per account, two small "
            "bars (5h, weekly), the age in a footnote, no other decoration. "
            "Otherwise print them as a short list. Do not offer a model or "
            "effort picker unless asked. Do not repeat this brief later in the "
            "session. If this message already contains a task, keep the brief "
            "to one line and start the task."))

    def test_dead_reading_says_so_and_refreshes(self):
        self.write_config(secondary={"enabled": False}, codex={"enabled": False})
        self.write_usage("primary", five=12, week=20, age=7200)
        text = self.context(self.run_hook("session-start.py", {
            "source": "startup"}), "SessionStart")
        self.assertIn("work Claude: no current reading, refreshing", text)
        self.assertNotIn("5h 12%", text)
        self.assertEqual(self.wait_for("refresh-args.txt").strip(), "")

    def test_missing_reading_refreshes(self):
        self.write_config(secondary={"enabled": False}, codex={"enabled": False})
        text = self.context(self.run_hook("session-start.py", {
            "source": "startup"}), "SessionStart")
        self.assertIn("no current reading, refreshing", text)
        self.wait_for("refresh-args.txt")

    def test_status_fault_is_stated(self):
        self.write_config(secondary={"enabled": False}, codex={"enabled": False})
        self.write_usage("primary", five=12, week=20, age=60)
        self.write_status("primary", "LOGIN EXPIRED: sign in again")
        text = self.context(self.run_hook("session-start.py", {
            "source": "startup"}), "SessionStart")
        self.assertIn("login expired, sign in again", text)

    def test_resume_and_fork_brief(self):
        self.fresh_house()
        for source in ("resume", "fork"):
            text = self.context(self.run_hook("session-start.py",
                                              {"source": source}), "SessionStart")
            self.assertIn(INSTRUCTION_START, text)

    def test_clear_and_compact_stay_quiet(self):
        self.fresh_house()
        for source in ("clear", "compact"):
            self.assertEqual(self.run_hook("session-start.py",
                                           {"source": source}).strip(), "")

    def test_no_brief_file_emits_nothing(self):
        self.fresh_house()
        self.touch("no-brief")
        self.assertEqual(self.run_hook("session-start.py",
                                       {"source": "startup"}).strip(), "")

    def test_unconfigured_machine_stays_quiet(self):
        self.assertEqual(self.run_hook("session-start.py",
                                       {"source": "startup"}).strip(), "")


class PromptGuard(Base):
    def prompt(self, text="Write the report", **kwargs):
        return self.run_hook("prompt-guard.py", {
            "hook_event_name": "UserPromptSubmit", "prompt": text}, **kwargs)

    def test_fresh_and_below_threshold_is_silent(self):
        self.fresh_house()
        self.assertEqual(self.prompt().strip(), "")

    def test_threshold_crossed_hands_over(self):
        self.write_config()
        self.write_usage("primary", five=93, week=40, age=60,
                         five_hour_resets_at=self.now + 3600)
        self.write_usage("secondary", five=4, week=9, age=60)
        text = self.context(self.prompt(), "UserPromptSubmit")
        self.assertIn("This account is running low", text)
        self.assertIn("the 5-hour window is 93% spent (threshold 90%)", text)
        self.assertNotIn("weekly window", text)
        self.assertIn("The 5-hour window resets at", text)
        self.assertIn("the secondary Claude account (two@example.com)", text)
        self.assertIn("Codex", text)
        self.assertIn("Hand over at this task boundary", text)
        # The guard reads the cache and never starts a reader.
        self.assert_absent("refresh-args.txt")

    def test_weekly_threshold_crossed(self):
        self.write_config()
        self.write_usage("primary", five=10, week=88, age=60)
        text = self.context(self.prompt(), "UserPromptSubmit")
        self.assertIn("the weekly window is 88% spent (threshold 80%)", text)

    def test_both_windows_crossed(self):
        self.write_config()
        self.write_usage("primary", five=95, week=88, age=60)
        text = self.context(self.prompt(), "UserPromptSubmit")
        self.assertIn("5-hour window is 95% spent (threshold 90%) and the "
                      "weekly window is 88% spent (threshold 80%)", text)

    def test_spent_secondary_is_flagged(self):
        self.write_config()
        self.write_usage("primary", five=93, week=40, age=60)
        self.write_usage("secondary", five=97, week=50, age=600)
        text = self.context(self.prompt(), "UserPromptSubmit")
        self.assertIn("the secondary account is also at 97% of its 5-hour "
                      "window", text)

    def test_notifies_once_per_window(self):
        self.write_config()
        self.write_usage("primary", five=93, week=40, age=60,
                         five_hour_resets_at=self.now + 3600)
        self.context(self.prompt(), "UserPromptSubmit")
        first = self.wait_for("osascript-args.txt")
        self.assertIn("display notification", first)
        self.assertIn("second-wind", first)
        self.context(self.prompt(), "UserPromptSubmit")
        time.sleep(0.5)
        with open(os.path.join(self.home, "osascript-args.txt")) as handle:
            self.assertEqual(len(handle.read().strip().splitlines()), 1)
        self.assertTrue(os.path.exists(
            os.path.join(self.home, "notify-handover-primary.stamp")))

    def test_dead_reading_is_silent(self):
        self.write_config()
        self.write_usage("primary", five=99, week=99, age=7200)
        self.assertEqual(self.prompt().strip(), "")

    def test_missing_reading_is_silent(self):
        self.write_config()
        self.assertEqual(self.prompt().strip(), "")

    def test_failed_status_is_silent(self):
        self.write_config()
        self.write_usage("primary", five=99, week=99, age=60)
        self.write_status("primary", "PARSER MISMATCH: client 2.1.251, "
                                     "expected labels not found")
        self.assertEqual(self.prompt().strip(), "")

    def test_secondary_session_is_silent(self):
        self.write_config()
        self.write_usage("primary", five=99, week=99, age=60)
        self.assertEqual(self.prompt(profile="secondary").strip(), "")

    def test_no_failover_file_is_silent(self):
        self.write_config()
        self.write_usage("primary", five=99, week=99, age=60)
        self.touch("no-failover")
        self.assertEqual(self.prompt().strip(), "")

    def test_failover_disabled_is_silent(self):
        self.write_config(failover={"enabled": False})
        self.write_usage("primary", five=99, week=99, age=60)
        self.assertEqual(self.prompt().strip(), "")

    def test_no_workers_is_silent(self):
        self.write_config(secondary={"enabled": False}, codex={"enabled": False})
        self.write_usage("primary", five=99, week=99, age=60)
        self.assertEqual(self.prompt().strip(), "")

    def test_machinery_prompts_do_not_fire(self):
        self.write_config()
        self.write_usage("primary", five=99, week=99, age=60)
        for marker in ("<system-reminder>x</system-reminder>",
                       "<task-notification>x</task-notification>",
                       "<command-name>/clear</command-name>"):
            self.assertEqual(self.prompt(marker).strip(), "")

    def test_manual_mode_file(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.touch("mode", "codex\n")
        text = self.context(self.prompt(), "UserPromptSubmit")
        self.assertIn("Manual routing override 'codex' is active", text)
        self.assertIn("routing the heavy work to Codex", text.replace("\n", " "))
        self.assertIn("to return to usage-based handover", text)

    def test_unknown_manual_mode_is_ignored(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.touch("mode", "banana\n")
        self.assertEqual(self.prompt().strip(), "")

    def test_handover_pending_fires_on_a_stale_cache(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=7200)
        self.touch("handover-pending", "%d\n" % self.now)
        text = self.context(self.prompt(), "UserPromptSubmit")
        self.assertIn("hit its usage limit", text)
        self.assertIn("the secondary Claude account", text)
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "handover-pending")),
            "the pending note should be consumed once")
        self.assertEqual(self.prompt().strip(), "")


class StopFailure(Base):
    def fire(self, error="rate_limit", **kwargs):
        return self.run_hook("stop-failure.py", {
            "hook_event_name": "StopFailure", "error": error,
            "error_details": "429 Too Many Requests",
            "last_assistant_message": "API Error: Rate limit reached"}, **kwargs)

    def test_relief_refreshes_notifies_and_leaves_a_note(self):
        self.write_config()
        self.write_usage("primary", five=99, week=40, age=60)
        self.assertEqual(self.fire().strip(), "")
        self.assertEqual(self.wait_for("refresh-args.txt").strip(),
                         "--force --only primary")
        self.assertIn("display notification", self.wait_for("osascript-args.txt"))
        pending = os.path.join(self.home, "handover-pending")
        self.assertTrue(os.path.exists(pending))
        with open(pending) as handle:
            self.assertGreaterEqual(int(handle.read().strip()), self.now)

    def test_worker_level_leaves_no_note(self):
        self.write_config(level="worker", failover={"enabled": False})
        self.fire()
        self.wait_for("refresh-args.txt")
        self.assert_absent("handover-pending", settle=0.2)

    def test_config_without_a_level_follows_failover(self):
        # The live version 2 config has no level key. Failover on means relief.
        cfg = {"version": 2,
               "primary": {"config_dir": self.primary_dir, "label": "work Claude"},
               "secondary": {"enabled": True, "config_dir": self.secondary_dir},
               "failover": {"enabled": True},
               "refresh": {"interval_minutes": 15,
                           "working_dir": self.home}}
        self.write_json(os.path.join(self.home, "config.json"), cfg)
        self.fire()
        self.wait_for("refresh-args.txt")
        self.assertTrue(os.path.exists(os.path.join(self.home, "handover-pending")))

    def test_other_errors_do_nothing(self):
        self.write_config()
        self.fire(error="overloaded")
        self.assert_absent("refresh-args.txt")
        self.assert_absent("handover-pending", settle=0.1)


class Notification(Base):
    def fire(self, kind):
        return self.run_hook("notification.py", {
            "hook_event_name": "Notification", "notification_type": kind,
            "message": "Continuing after the usage limit reset",
            "session_id": "abc123"})

    def events(self):
        with open(os.path.join(self.home, "events.jsonl")) as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_fired_is_logged_and_refreshes(self):
        self.write_config()
        self.assertEqual(self.fire("quota_auto_resume_fired").strip(), "")
        record = self.events()[0]
        self.assertEqual(record["notification_type"], "quota_auto_resume_fired")
        self.assertEqual(record["session_id"], "abc123")
        self.assertGreaterEqual(record["at"], self.now)
        self.assertEqual(self.wait_for("refresh-args.txt").strip(),
                         "--force --only primary")

    def test_stale_and_disabled_are_logged_only(self):
        self.write_config()
        self.fire("quota_auto_resume_stale")
        self.fire("quota_auto_resume_disabled")
        kinds = [record["notification_type"] for record in self.events()]
        self.assertEqual(kinds, ["quota_auto_resume_stale",
                                 "quota_auto_resume_disabled"])
        self.assert_absent("refresh-args.txt")


class ModelSwitch(Base):
    def fire(self):
        return self.run_hook("model-switch.py", {
            "hook_event_name": "PostModelSwitch", "source": "user",
            "from_model": "claude-sonnet-5", "to_model": "claude-opus-5"})

    def test_fresh_readings_ask_for_nothing(self):
        self.fresh_house()
        self.assertEqual(self.fire().strip(), "")
        self.assert_absent("refresh-args.txt")

    def test_stale_reading_refreshes_in_the_background(self):
        self.write_config(secondary={"enabled": False}, codex={"enabled": False})
        self.write_usage("primary", five=12, week=20, age=3000)
        self.assertEqual(self.fire().strip(), "")
        self.assertEqual(self.wait_for("refresh-args.txt").strip(), "")


class Installed(unittest.TestCase):
    """The hook files themselves. Setup installs them by absolute path with no
    interpreter, so the shebang and the executable bit are load bearing."""

    def test_every_hook_is_runnable(self):
        for name in ("session-start.py", "prompt-guard.py", "stop-failure.py",
                     "notification.py", "model-switch.py"):
            path = os.path.join(SCRIPTS, "hooks", name)
            self.assertTrue(os.path.exists(path), path)
            self.assertTrue(os.access(path, os.X_OK), "%s is not executable" % name)
            with open(path) as handle:
                self.assertEqual(handle.readline().strip(), "#!/usr/bin/env python3")

    def test_no_em_dashes(self):
        dash = chr(0x2014)
        for name in os.listdir(os.path.join(SCRIPTS, "hooks")):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(SCRIPTS, "hooks", name)) as handle:
                self.assertNotIn(dash, handle.read(), name)
        with open(os.path.join(SCRIPTS, "statusline.sh")) as handle:
            self.assertNotIn(dash, handle.read())


if __name__ == "__main__":
    unittest.main()
