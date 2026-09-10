#!/usr/bin/env python3
"""Tests for the five hooks and what they say to a session.

Each hook is run as a subprocess with the event JSON on stdin and SW_HOME
pointing at a fixture directory, which is the shape Claude Code invokes it in.
Claude Code itself is not in the picture: nothing here proves the harness
dispatches these hooks on those events, that settings.json wires them up, or
that a session does anything with what they print. That end of it is checked by
hand against a real session, and TESTING.md says how. Nothing touches the real
~/.second-wind.

Two stubs stand in for the outside world, both on PATH or in the staged copy of
scripts/:

  usage-refresh.sh   records the arguments it was called with, and starts no
                     reader, so a test can prove a hook asked for a refresh
                     without driving a real client
  osascript          records the notification, so the suite proves notify() was
                     called without popping a real one on the machine running it
"""
import glob
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

# The guard must never wait on a notification. This stub takes three seconds,
# so a hook that waits for it cannot come back inside the test's one second.
SLOW_OSASCRIPT_STUB = """#!/bin/sh
sleep 3
printf '%s\\n' "$*" >> "$SW_HOME/osascript-args.txt"
"""

INSTRUCTION_START = "The block above is the usage panel."
INSTRUCTION_END = ("If this message already contains a task, print the panel and "
                   "then get on with the task without further preamble.")


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
    slow = os.path.join(STAGE, "bin-slow")
    os.makedirs(slow)
    stub = os.path.join(slow, "osascript")
    with open(stub, "w") as handle:
        handle.write(SLOW_OSASCRIPT_STUB)
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

    def run_hook(self, name, payload=None, profile="primary", env=None,
                 binaries="bin"):
        path = os.path.join(STAGE, "scripts", "hooks", name)
        environment = dict(os.environ)
        environment["SW_HOME"] = self.home
        environment["PATH"] = os.path.join(STAGE, binaries) + os.pathsep + \
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
        self.assertIn("5-hour", text)
        self.assertIn("weekly", text)
        for label, five, week in (("work Claude", "12%", "20%"),
                                  ("spare Claude", "3%", "8%"),
                                  ("codex", "5%", "11%")):
            row = next(line for line in text.splitlines()
                       if line.startswith(label))
            self.assertEqual([five, week],
                             [part for part in row.split() if part.endswith("%")])
        self.assertIn(" old.", text)
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
            "The block above is the usage panel. Print it before anything else, "
            "verbatim, every line including the header and the age footnote, as "
            "monospace text so the columns line up. Do not redraw the bars, "
            "change their width, reorder or drop rows, add colour, bold, emoji "
            "or a table around it, and do not restate the figures in prose "
            "underneath. The bars are drawn to scale and a percentage below 100 "
            "is deliberately never a full bar. Do not offer a model or effort "
            "picker unless asked. Do not volunteer this panel again later in "
            "the session, but if usage, limits or where to route work come up "
            "again, render it again the same way, regenerated with 'python3 "
            "\"$SW/scripts/setup.py\" --card' rather than retyped from memory "
            "or redrawn in a new shape. If this message already contains a "
            "task, print the panel and then get on with the task without "
            "further preamble."))

    def test_dead_reading_says_so_and_refreshes(self):
        self.write_config(secondary={"enabled": False}, codex={"enabled": False})
        self.write_usage("primary", five=12, week=20, age=7200)
        text = self.context(self.run_hook("session-start.py", {
            "source": "startup"}), "SessionStart")
        self.assertIn("no current reading, refreshing", text)
        self.assertNotIn("12%", text)
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

    def test_the_brief_offers_the_picker_when_nothing_is_routed(self):
        self.fresh_house()
        text = self.context(self.run_hook("session-start.py", {
            "source": "startup"}), "SessionStart")
        self.assertIn("Type /second-wind to pick where the next tasks run.",
                      text)

    def test_the_brief_names_a_route_armed_in_this_session(self):
        self.fresh_house()
        session = "5555aaaa-1111-2222-3333-444455556666"
        self.write_json(os.path.join(self.home, "mode"),
                        {"worker": "codex", "label": "Codex", "model": None,
                         "effort": None, "set_at": self.now,
                         "session_id": session})
        text = self.context(self.run_hook("session-start.py", {
            "source": "startup", "session_id": session}), "SessionStart")
        self.assertIn("Routing to Codex is on for this session; /second-wind "
                      "off stops it.", text)
        self.assertNotIn("Type /second-wind to pick", text)

    def test_a_route_armed_elsewhere_is_not_announced_here(self):
        self.fresh_house()
        self.write_json(os.path.join(self.home, "mode"),
                        {"worker": "codex", "label": "Codex", "model": None,
                         "effort": None, "set_at": self.now,
                         "session_id": "somebody-elses-session"})
        text = self.context(self.run_hook("session-start.py", {
            "source": "startup", "session_id": "mine"}), "SessionStart")
        self.assertIn("Type /second-wind to pick where the next tasks run.",
                      text)
        self.assertNotIn("Routing to", text)

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
        time.sleep(1.0)
        with open(os.path.join(self.home, "osascript-args.txt")) as handle:
            self.assertEqual(len(handle.read().strip().splitlines()), 1)
        self.assertTrue(os.path.exists(
            os.path.join(self.home, "notify-handover-primary.stamp")))

    def test_does_not_wait_for_the_notification(self):
        # The prompt is the one moment a person is watching the cursor. The
        # notification goes out detached, so a three second osascript costs the
        # hook nothing.
        self.write_config()
        self.write_usage("primary", five=93, week=40, age=60)
        started = time.time()
        text = self.context(self.prompt(binaries="bin-slow"), "UserPromptSubmit")
        elapsed = time.time() - started
        self.assertIn("This account is running low", text)
        self.assertLess(elapsed, 1.0,
                        "the guard waited %.2fs on the notification" % elapsed)
        # It still deduped, which is the stamp being written by the hook itself
        # rather than by whatever osascript does later.
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

    def test_a_json_mode_file_from_the_picker(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.touch("mode", json.dumps({"worker": "secondary", "model": None,
                                       "effort": None, "set_at": self.now,
                                       "label": "spare Claude"}))
        text = self.context(self.prompt(), "UserPromptSubmit")
        self.assertIn("Manual routing override 'secondary' is active", text)
        self.assertIn("routing the heavy work to spare Claude",
                      text.replace("\n", " "))
        self.assertNotIn("--model", text)
        self.assertNotIn("--effort", text)
        self.assertIn("to return to usage-based handover", text)

    def test_a_json_mode_file_carrying_a_model_and_an_effort(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.touch("mode", json.dumps({"worker": "codex", "model": "gpt-5.6",
                                       "effort": "high", "set_at": self.now,
                                       "label": "Codex"}))
        text = self.context(self.prompt(), "UserPromptSubmit").replace("\n", " ")
        self.assertIn("routing the heavy work to Codex", text)
        self.assertIn("through second-wind, passing --model gpt-5.6 and "
                      "--effort high to the runner", text)

    def test_a_json_mode_file_with_a_model_only(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.touch("mode", json.dumps({"worker": "codex", "model": "gpt-5.6",
                                       "effort": None, "label": "Codex"}))
        text = self.context(self.prompt(), "UserPromptSubmit").replace("\n", " ")
        self.assertIn("passing --model gpt-5.6 to the runner", text)
        self.assertNotIn("--effort", text)

    def test_a_json_mode_file_with_no_label_names_the_worker(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.touch("mode", json.dumps({"worker": "grok"}))
        text = self.context(self.prompt(), "UserPromptSubmit").replace("\n", " ")
        self.assertIn("routing the heavy work to Grok Build", text)

    def test_the_mode_file_takes_every_spelling_of_a_worker(self):
        # The guard reads the mode file with the same parser the routing hook
        # writes it with, so a picker id, a bare alias and the config's own role
        # name all mean the same account.
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        for contents in ("personal\n", "second-wind/personal\n", "secondary\n",
                         json.dumps({"worker": "personal"})):
            self.touch("mode", contents)
            text = self.context(self.prompt(), "UserPromptSubmit").replace("\n", " ")
            self.assertIn("routing the heavy work to spare Claude", text, contents)

    def test_a_bare_routing_id_can_carry_a_model_and_an_effort(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.touch("mode", "second-wind/codex/gpt-5.6/high\n")
        text = self.context(self.prompt(), "UserPromptSubmit").replace("\n", " ")
        self.assertIn("routing the heavy work to Codex", text)
        self.assertIn("passing --model gpt-5.6 and --effort high to the runner",
                      text)

    SESSION = "7777aaaa-1111-2222-3333-444455556666"

    def scoped_prompt(self, session=None, **kwargs):
        """A prompt carrying a session id. SW_DESKTOP_STORE points nowhere, so
        no test can read the store of whoever is running the suite."""
        return self.run_hook("prompt-guard.py", {
            "hook_event_name": "UserPromptSubmit", "prompt": "Write the report",
            "session_id": session or self.SESSION},
            env={"SW_DESKTOP_STORE": os.path.join(self.home, "gone")}, **kwargs)

    def write_route(self, **fields):
        data = {"worker": "codex", "model": None, "effort": None,
                "set_at": self.now, "label": "Codex"}
        data.update(fields)
        self.write_json(os.path.join(self.home, "mode"), data)

    def test_a_route_armed_in_this_session_applies(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.write_route(session_id=self.SESSION)
        text = self.context(self.scoped_prompt(), "UserPromptSubmit")
        self.assertIn("routing the heavy work to Codex", text.replace("\n", " "))

    def test_a_route_armed_in_another_session_is_ignored_and_kept(self):
        # The fault this scoping exists for: a route armed in a test chat told
        # every other session to send its work away.
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.write_route(session_id="somebody-elses-session")
        self.assertEqual(self.scoped_prompt().strip(), "")
        self.assertTrue(os.path.exists(os.path.join(self.home, "mode")))

    def test_a_route_marked_for_every_session_applies_anywhere(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.write_route(session_id="*")
        for session in (self.SESSION, "another-session"):
            text = self.context(self.scoped_prompt(session), "UserPromptSubmit")
            self.assertIn("routing the heavy work to Codex",
                          text.replace("\n", " "), session)

    def test_a_route_naming_no_session_still_applies(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.write_route()
        text = self.context(self.scoped_prompt("any-session"),
                            "UserPromptSubmit")
        self.assertIn("routing the heavy work to Codex", text.replace("\n", " "))

    def test_a_route_past_twelve_hours_is_ignored_and_deleted(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.write_route(session_id=self.SESSION, set_at=self.now - 13 * 3600)
        self.assertEqual(self.scoped_prompt().strip(), "")
        self.assertFalse(os.path.exists(os.path.join(self.home, "mode")),
                         "a route nobody remembers arming should be dropped")

    def test_a_route_carrying_a_mode_names_the_flag(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.write_route(session_id="*", mode="review")
        text = self.context(self.scoped_prompt(), "UserPromptSubmit")
        self.assertIn("passing --review to the runner", text.replace("\n", " "))

    def test_a_malformed_mode_file_is_silent(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        for contents in ("{not json", "{}", "[]", '{"worker": "banana"}',
                         '{"worker": null}', "", "   \n"):
            self.touch("mode", contents)
            self.assertEqual(self.prompt().strip(), "", contents)

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

    def test_stale_handover_pending_is_dropped(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.touch("handover-pending", "%d\n" % (self.now - 7 * 3600))
        self.assertEqual(self.prompt().strip(), "")
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "handover-pending")),
            "a note past its window should be dropped, not kept")

    def test_handover_pending_waits_for_a_worker(self):
        self.write_config(secondary={"enabled": False}, codex={"enabled": False})
        self.write_usage("primary", five=2, week=3, age=60)
        self.touch("handover-pending", "%d\n" % self.now)
        self.assertEqual(self.prompt().strip(), "")
        # Nowhere to hand to yet, so the note survives for when there is.
        self.assertTrue(os.path.exists(
            os.path.join(self.home, "handover-pending")))


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

    def test_does_not_wait_for_the_notification(self):
        # The notification is a side effect, so this hook has no reason to sit
        # waiting for osascript any more than the prompt guard does. The slow
        # stub takes three seconds; the hook must be back well inside one.
        self.write_config()
        self.write_usage("primary", five=99, week=40, age=60)
        started = time.time()
        self.fire(binaries="bin-slow")
        elapsed = time.time() - started
        self.assertLess(elapsed, 1.0,
                        "stop-failure waited %.2fs on the notification" % elapsed)
        # Stamped by the hook itself, not by whatever osascript does later.
        self.assertTrue(os.path.exists(
            os.path.join(self.home, "notify-rate-limit-primary.stamp")))

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

    def test_config_without_failover_at_all_is_relief(self):
        # Absent means enabled, which is how the prompt guard reads it too.
        cfg = {"version": 2,
               "primary": {"config_dir": self.primary_dir},
               "secondary": {"enabled": True, "config_dir": self.secondary_dir},
               "refresh": {"interval_minutes": 15}}
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


class ModelRoute(Base):
    """The PreModelSwitch hook. It refuses a routing id on purpose and writes
    the mode file; it must never refuse a real model."""

    def switch(self, to_model, requested=None, **kwargs):
        return self.run_hook("model-route.py", {
            "hook_event_name": "PreModelSwitch", "from_model": "claude-haiku-4-5",
            "to_model": to_model,
            "requested_model": to_model if requested is None else requested,
            "source": "picker"}, **kwargs)

    def mode(self):
        with open(os.path.join(self.home, "mode")) as handle:
            return json.load(handle)

    def blocked(self, stdout):
        data = json.loads(stdout)
        self.assertEqual(data["decision"], "block")
        return data["reason"]

    def test_a_worker_row_blocks_the_switch_and_writes_the_mode_file(self):
        self.write_config()
        reason = self.blocked(self.switch("second-wind/personal"))
        self.assertIn("the next tasks route to spare Claude", reason)
        self.assertIn("Your session model is unchanged", reason)
        self.assertIn("Pick any normal model to stop routing", reason)
        self.assertNotIn(", model", reason)
        self.assertNotIn(", effort", reason)
        written = self.mode()
        self.assertEqual(written["worker"], "secondary")
        self.assertIsNone(written["model"])
        self.assertIsNone(written["effort"])
        self.assertEqual(written["label"], "spare Claude")
        self.assertGreaterEqual(written["set_at"], self.now)

    def pick(self, target, session):
        return self.blocked(self.run_hook("model-route.py", {
            "hook_event_name": "PreModelSwitch", "to_model": target,
            "requested_model": target, "source": "picker",
            "session_id": session}))

    def route(self, session):
        with open(os.path.join(self.home, "routes", session + ".json")) as handle:
            return json.load(handle)

    def test_the_route_is_scoped_to_the_session_that_picked_it(self):
        self.write_config()
        session = "3333aaaa-1111-2222-3333-444455556666"
        self.pick("second-wind/codex", session)
        self.assertEqual(self.route(session)["session_id"], session)
        self.assertFalse(os.path.exists(os.path.join(self.home, "mode")),
                         "a route for one session must not land in the file "
                         "every session reads")

    def test_two_sessions_are_armed_separately(self):
        # The live fault: both routes went to one file, so the second arming
        # overwrote the first and both chats routed to the same worker.
        self.write_config()
        first = "1111aaaa-1111-2222-3333-444455556666"
        second = "2222bbbb-1111-2222-3333-444455556666"
        self.pick("second-wind/codex", first)
        self.pick("second-wind/personal", second)
        self.assertEqual(self.route(first)["worker"], "codex")
        self.assertEqual(self.route(second)["worker"], "secondary")

    def test_a_real_model_leaves_another_sessions_route_alone(self):
        self.write_config()
        theirs = "9999cccc-1111-2222-3333-444455556666"
        self.pick("second-wind/codex", theirs)
        out = self.run_hook("model-route.py", {
            "hook_event_name": "PreModelSwitch", "to_model": "claude-opus-5",
            "requested_model": "opus", "source": "picker",
            "session_id": "somebody-else"})
        self.assertEqual(out.strip(), "",
                         "picking a model here is not a decision about that chat")
        self.assertEqual(self.route(theirs)["worker"], "codex")

    def test_a_real_model_clears_this_sessions_own_route(self):
        self.write_config()
        session = "4444dddd-1111-2222-3333-444455556666"
        self.pick("second-wind/codex", session)
        out = self.run_hook("model-route.py", {
            "hook_event_name": "PreModelSwitch", "to_model": "claude-opus-5",
            "requested_model": "opus", "source": "picker",
            "session_id": session})
        self.assertIn("routing off", out)
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "routes", session + ".json")))

    def test_an_event_with_no_session_id_arms_a_route_for_every_session(self):
        self.write_config()
        self.blocked(self.switch("second-wind/codex"))
        self.assertNotIn("session_id", self.mode())

    def test_a_model_and_effort_row_records_both(self):
        self.write_config()
        reason = self.blocked(self.switch("second-wind/codex/gpt-5.6/high"))
        self.assertIn("route to Codex, model gpt-5.6, effort high", reason)
        written = self.mode()
        self.assertEqual((written["worker"], written["model"], written["effort"]),
                         ("codex", "gpt-5.6", "high"))

    def test_a_bare_alias_typed_in_the_desktop_app(self):
        self.write_config()
        # The desktop picker shows no rows of ours, so somebody types an id and
        # Claude Code passes it through without canonicalising it.
        reason = self.blocked(self.switch("claude-personal",
                                          requested="claude-personal"))
        self.assertIn("route to spare Claude", reason)
        self.assertEqual(self.mode()["worker"], "secondary")

    def test_what_the_person_asked_for_decides(self):
        # requested_model is what was typed or picked. to_model is whatever
        # Claude Code managed to canonicalise, which on one of our ids is
        # nothing useful, so it does not get a vote when the other field is set.
        self.write_config()
        self.blocked(self.switch("claude-haiku-4-5", requested="second-wind/codex"))
        self.assertEqual(self.mode()["worker"], "codex")

    def test_a_routing_id_in_the_other_field_never_blocks_a_real_switch(self):
        self.write_config()
        self.blocked(self.switch("second-wind/personal"))
        out = self.switch("second-wind/codex", requested="claude-opus-5")
        self.assertNotIn("block", out)
        self.assertIn("routing off", out)
        self.assertFalse(os.path.exists(os.path.join(self.home, "mode")))

    def test_the_target_is_read_when_nothing_was_requested(self):
        self.write_config()
        out = self.run_hook("model-route.py", {
            "hook_event_name": "PreModelSwitch", "to_model": "second-wind/grok",
            "source": "picker"})
        self.assertIn("that worker is not connected", self.blocked(out))

    def test_an_event_naming_nothing_leaves_an_active_override_alone(self):
        self.write_config()
        self.blocked(self.switch("second-wind/codex"))
        out = self.run_hook("model-route.py", {
            "hook_event_name": "PreModelSwitch", "source": "picker"})
        self.assertEqual(out.strip(), "")
        self.assertEqual(self.mode()["worker"], "codex")

    def test_a_worker_that_is_not_connected_is_refused_and_writes_nothing(self):
        self.write_config()
        reason = self.blocked(self.switch("second-wind/cursor"))
        self.assertIn("that worker is not connected; run setup", reason)
        self.assertFalse(os.path.exists(os.path.join(self.home, "mode")))

    def test_an_unconfigured_machine_refuses_the_row(self):
        reason = self.blocked(self.switch("second-wind/codex"))
        self.assertIn("not connected", reason)
        self.assertFalse(os.path.exists(os.path.join(self.home, "mode")))

    def test_a_real_model_turns_routing_off(self):
        self.write_config()
        self.blocked(self.switch("second-wind/codex"))
        out = self.switch("claude-opus-5", requested="opus")
        self.assertEqual(json.loads(out),
                         {"systemMessage": "second-wind: routing off, back to "
                                           "usage-based handover."})
        self.assertFalse(os.path.exists(os.path.join(self.home, "mode")))

    def test_a_real_model_with_no_routing_on_says_nothing(self):
        self.write_config()
        for target in ("claude-opus-5", "haiku", "claude-sonnet-5[1m]",
                       "some-gateway/model-x"):
            self.assertEqual(self.switch(target).strip(), "", target)

    def test_a_real_model_is_never_blocked(self):
        self.write_config()
        for contents in ("codex\n", "{not json", '{"worker": "codex"}'):
            self.touch("mode", contents)
            out = self.switch("claude-opus-5", requested="opus").strip()
            if out:
                self.assertNotIn("block", out)
            self.assertFalse(os.path.exists(os.path.join(self.home, "mode")),
                             contents)

    def raw_switch(self, raw):
        path = os.path.join(STAGE, "scripts", "hooks", "model-route.py")
        environment = dict(os.environ)
        environment["SW_HOME"] = self.home
        environment["CLAUDE_CONFIG_DIR"] = self.primary_dir
        return subprocess.run([path], input=raw, text=True, capture_output=True,
                              timeout=20, env=environment)

    def test_rubbish_on_stdin_is_silent(self):
        self.write_config()
        for raw in ("", "not json", "[]", "null"):
            done = self.raw_switch(raw)
            self.assertEqual(done.returncode, 0, raw)
            self.assertEqual(done.stdout.strip(), "", raw)
            self.assertEqual(done.stderr, "", raw)

    def test_rubbish_on_stdin_leaves_an_active_override_alone(self):
        # Unreadable input is not somebody choosing a real model, and treating
        # it as one turned every malformed event into "routing off".
        self.write_config()
        self.blocked(self.switch("second-wind/codex"))
        for raw in ("", "not json", "[]", "null", "{}"):
            done = self.raw_switch(raw)
            self.assertEqual(done.returncode, 0, raw)
            self.assertEqual(done.stdout.strip(), "", raw)
            self.assertEqual(self.mode()["worker"], "codex", raw)

    def test_it_comes_back_quickly(self):
        self.write_config()
        started = time.time()
        self.blocked(self.switch("second-wind/codex"))
        self.assertLess(time.time() - started, 5.0)


class DesktopRoute(Base):
    """The desktop app's model menu takes a typed name and fires no
    PreModelSwitch hook, so the prompt guard reads the app's own session file
    and refuses the prompt that would otherwise fail on a model the API has
    never heard of.

    SW_DESKTOP_STORE points every test here at a fixture store. It is always
    set, including in the "no store" case, so no test can read the store of
    whoever is running the suite.
    """

    SESSION = "0eaa1111-2222-3333-4444-555566667777"

    # ------------------------------------------------------------ fixtures

    def entry(self, root, name, data):
        """One store file, in the app's two-level layout."""
        folder = os.path.join(root, "%s-outer" % name, "%s-inner" % name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "local_%s.json" % name)
        self.write_json(path, data)
        return path

    def store(self, model, session=None):
        """Three files, one of them this session's, as the real store holds
        every session the app has ever opened."""
        root = os.path.join(self.home, "desktop-store")
        self.others = [
            self.entry(root, "other-one",
                       {"cliSessionId": "aaaa1111-0000-0000-0000-000000000000",
                        "model": "claude-opus-5", "cwd": self.home}),
            self.entry(root, "other-two",
                       {"cliSessionId": "bbbb2222-0000-0000-0000-000000000000",
                        "model": "second-wind/codex", "cwd": self.home}),
        ]
        self.match = self.entry(root, "mine", {
            "sessionId": "local_cccc3333-0000-0000-0000-000000000000",
            "cliSessionId": session or self.SESSION, "model": model,
            "cwd": self.home, "lastActivityAt": self.now})
        return root

    def prompt(self, store=None, session=None, text="Write the report", **kwargs):
        return self.run_hook("prompt-guard.py", {
            "hook_event_name": "UserPromptSubmit", "prompt": text,
            "session_id": session or self.SESSION},
            env={"SW_DESKTOP_STORE": store or os.path.join(self.home, "gone")},
            **kwargs)

    def mode(self, session=None):
        """The route armed for a session, which is its own file now."""
        path = os.path.join(self.home, "routes",
                            (session or self.SESSION) + ".json")
        with open(path) as handle:
            return json.load(handle)

    def assert_nothing_armed(self, message=None):
        """No global route, and no per-session file wherever it might land."""
        self.assertFalse(os.path.exists(os.path.join(self.home, "mode")),
                         message)
        self.assertEqual(
            glob.glob(os.path.join(self.home, "routes", "*.json")), [], message)

    def denied(self, stdout):
        self.assertTrue(stdout.strip(), "expected a refusal, got nothing")
        data = json.loads(stdout)
        self.assertEqual(data["decision"], "block")
        self.assertNotIn("hookSpecificOutput", data)
        return data["reason"]

    # ------------------------------------------------------------ tests

    def test_a_typed_routing_name_arms_the_route_and_refuses_the_prompt(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        reason = self.denied(self.prompt(self.store("claude-personal")))
        self.assertIn("routing to spare Claude is armed for your next tasks",
                      reason)
        self.assertIn("pick any normal model from the menu and send your "
                      "message again", reason)
        written = self.mode()
        self.assertEqual(written["worker"], "secondary")
        self.assertEqual(written["label"], "spare Claude")
        self.assertIsNone(written["model"])
        self.assertIsNone(written["effort"])
        self.assertEqual(written["source"], "desktop")

    def test_a_typed_picker_id_carries_its_model_and_effort(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        reason = self.denied(
            self.prompt(self.store("second-wind/codex/gpt-5.6/high")))
        self.assertIn("routing to Codex is armed", reason)
        written = self.mode()
        self.assertEqual((written["worker"], written["model"], written["effort"]),
                         ("codex", "gpt-5.6", "high"))

    def test_a_real_model_is_left_alone(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.assertEqual(self.prompt(self.store("claude-fable-5-1")).strip(), "")
        self.assert_nothing_armed()

    def test_a_missing_store_is_silent(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.assertEqual(self.prompt().strip(), "")
        self.assert_nothing_armed()
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "session-map.json")))

    def test_another_session_in_the_store_is_not_this_one(self):
        # The second fixture file holds a routing name against a different
        # session. Matching on anything looser than cliSessionId would refuse
        # a prompt in a session nobody had touched.
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        root = self.store("claude-fable-5-1")
        self.assertEqual(self.prompt(root).strip(), "")
        self.assert_nothing_armed()

    def snapshot(self, root):
        """Every store file, its mtime and its contents."""
        state = []
        for path in sorted(glob.glob(
                os.path.join(root, "*", "*", "local_*.json"))):
            with open(path) as handle:
                state.append((path, os.path.getmtime(path), handle.read()))
        return state

    def test_the_store_is_never_written_to(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        root = self.store("claude-personal")
        before = self.snapshot(root)
        self.denied(self.prompt(root))
        self.assertEqual(before, self.snapshot(root))

    def test_the_session_map_is_written_and_then_reused(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        root = self.store("claude-personal")
        self.denied(self.prompt(root))
        with open(os.path.join(self.home, "session-map.json")) as handle:
            self.assertEqual(json.load(handle), {self.SESSION: self.match})
        # Point the scan at an empty store and delete every other file. A rescan
        # would find nothing; the cached path still resolves, so a second
        # refusal proves the cache was read instead of the store.
        for path in self.others:
            os.unlink(path)
        empty = os.path.join(self.home, "empty-store")
        os.makedirs(empty)
        self.assertIn("routing to spare Claude is armed",
                      self.denied(self.prompt(empty)))

    def test_a_stale_cache_entry_falls_back_to_a_rescan(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        root = self.store("claude-personal")
        self.write_json(os.path.join(self.home, "session-map.json"),
                        {self.SESSION: os.path.join(self.home, "went-away.json")})
        self.assertIn("routing to spare Claude is armed",
                      self.denied(self.prompt(root)))
        with open(os.path.join(self.home, "session-map.json")) as handle:
            self.assertEqual(json.load(handle)[self.SESSION], self.match)

    def test_a_cached_file_that_now_holds_another_session_is_rescanned(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        root = self.store("claude-personal")
        self.write_json(os.path.join(self.home, "session-map.json"),
                        {self.SESSION: self.others[0]})
        self.assertIn("routing to spare Claude is armed",
                      self.denied(self.prompt(root)))

    def test_a_worker_that_is_not_connected_is_refused_and_writes_nothing(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        reason = self.denied(self.prompt(self.store("cursor")))
        self.assertIn("Cursor Agent is not connected", reason)
        self.assertIn("pick any normal model from the menu", reason)
        self.assert_nothing_armed()

    def test_the_refusal_comes_before_the_usage_reading(self):
        # A spent account and a broken model at once: the prompt cannot be sent
        # either way, so the refusal wins and the handover line waits.
        self.write_config()
        self.write_usage("primary", five=97, week=90, age=60)
        reason = self.denied(self.prompt(self.store("claude-personal")))
        self.assertIn("routing to spare Claude is armed", reason)

    def test_the_master_switch_still_wins(self):
        # no-failover means this account's work stays here. Arming a route
        # against it would be second-wind overruling the one file that says no.
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.touch("no-failover")
        self.assertEqual(self.prompt(self.store("claude-personal")).strip(), "")
        self.assert_nothing_armed()

    def test_machinery_prompts_are_left_alone(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        root = self.store("claude-personal")
        self.assertEqual(
            self.prompt(root, text="<system-reminder>x</system-reminder>").strip(),
            "")

    def test_an_armed_desktop_route_announces_itself_on_the_next_prompt(self):
        # Once a real model is chosen the guard's own mode file path takes over,
        # so the route the desktop arming wrote is what the next prompt reads.
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.denied(self.prompt(self.store("claude-personal")))
        text = self.context(self.prompt(self.store("claude-fable-5-1")),
                            "UserPromptSubmit").replace("\n", " ")
        self.assertIn("routing the heavy work to spare Claude", text)

    def test_the_armed_route_is_scoped_to_this_session(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.denied(self.prompt(self.store("claude-personal")))
        self.assertEqual(self.mode()["session_id"], self.SESSION)
        # Another session sees nothing, whatever is armed here.
        self.assertEqual(self.prompt(
            self.store("claude-fable-5-1", session="another-session"),
            session="another-session").strip(), "")

    def test_the_skills_own_name_typed_as_a_model_asks_for_the_command(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        for typed in ("second-wind", "second wind", "Second-Wind"):
            reason = self.denied(self.prompt(self.store(typed)))
            self.assertIn("type /second-wind as a command to open the picker",
                          reason, typed)
            self.assertIn("Pick any real model from the menu first", reason)
            self.assert_nothing_armed(typed)

    def test_two_sessions_armed_at_once_each_see_only_their_own(self):
        # The live fault: a route armed from the desktop app replaced the route
        # another chat had set from the picker, so both chats routed together.
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        other = "1234bbbb-2222-3333-4444-555566667777"
        self.denied(self.prompt(self.store("claude-personal")))
        self.denied(self.prompt(self.store("second-wind/codex", session=other),
                                session=other))
        self.assertEqual(self.mode()["worker"], "secondary")
        self.assertEqual(self.mode(other)["worker"], "codex")
        mine = self.context(self.prompt(self.store("claude-fable-5-1")),
                            "UserPromptSubmit").replace("\n", " ")
        self.assertIn("routing the heavy work to spare Claude", mine)
        self.assertNotIn("Codex", mine)
        theirs = self.context(
            self.prompt(self.store("claude-fable-5-1", session=other),
                        session=other), "UserPromptSubmit").replace("\n", " ")
        self.assertIn("routing the heavy work to Codex", theirs)
        self.assertNotIn("spare Claude", theirs)

    def test_the_announcement_names_the_file_that_holds_the_route(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        self.denied(self.prompt(self.store("claude-personal")))
        text = self.context(self.prompt(self.store("claude-fable-5-1")),
                            "UserPromptSubmit").replace("\n", " ")
        self.assertIn("routes/%s.json" % self.SESSION, text)

    def test_it_comes_back_quickly(self):
        self.write_config()
        self.write_usage("primary", five=2, week=3, age=60)
        root = self.store("claude-personal")
        started = time.time()
        self.denied(self.prompt(root))
        self.assertLess(time.time() - started, 5.0)


class StatusLine(Base):
    """The status line is the only place the real 5-hour and 7-day figures can
    be had, so what it writes into the cache is load bearing for every other
    part of the tool."""

    def setUp(self):
        super(StatusLine, self).setUp()
        if shutil.which("jq") is None:
            self.skipTest("jq is not installed")
        self.write_config(primary={"plan": "Claude Team"})
        # A profile the config names keeps its own sign-in file. Writing one
        # here keeps the test off the real ~/.claude.json.
        self.write_json(os.path.join(self.primary_dir, ".claude.json"),
                        {"oauthAccount": {"emailAddress": "one@example.com"}})

    def run_statusline(self, payload, profile="primary"):
        environment = dict(os.environ)
        environment["SW_HOME"] = self.home
        environment["CLAUDE_CONFIG_DIR"] = (self.primary_dir if profile == "primary"
                                            else self.secondary_dir)
        done = subprocess.run(["/bin/sh",
                               os.path.join(STAGE, "scripts", "statusline.sh")],
                              input=payload, text=True, capture_output=True,
                              timeout=20, env=environment)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def payload(self):
        return json.dumps({
            "version": "2.1.251",
            "model": {"display_name": "Opus 5"},
            "effort": {"level": "high"},
            "context_window": {"remaining_percentage": 72.4},
            "rate_limits": {
                "five_hour": {"used_percentage": 93.2,
                              "resets_at": self.now + 3600},
                "seven_day": {"used_percentage": 38.9,
                              "resets_at": self.now + 300000}}})

    def test_cache_matches_the_shared_schema(self):
        bar = self.run_statusline(self.payload())
        self.assertIn("work Claude", bar)
        self.assertIn("5h 93%", bar)
        self.assertIn("7d 39%", bar)

        with open(os.path.join(self.home, "usage-primary.json")) as handle:
            cache = json.load(handle)
        self.assertEqual(cache["role"], "primary")
        self.assertEqual(cache["worker"], "claude")
        self.assertEqual(cache["account"], "one@example.com")
        self.assertEqual(cache["plan"], "Claude Team")
        # Integers, not the quoted strings this used to write.
        self.assertEqual(cache["five_hour_pct"], 93)
        self.assertEqual(cache["seven_day_pct"], 39)
        self.assertIsInstance(cache["five_hour_pct"], int)
        self.assertIsInstance(cache["seven_day_pct"], int)
        self.assertEqual(cache["five_hour_resets_at"], self.now + 3600)
        self.assertEqual(cache["seven_day_resets_at"], self.now + 300000)
        self.assertTrue(cache["five_hour_resets"])
        self.assertTrue(cache["seven_day_resets"])
        self.assertEqual(cache["client_version"], "2.1.251")
        self.assertEqual(cache["extra"], {"model": "Opus 5", "effort": "high"})
        self.assertGreaterEqual(cache["cached_at"], self.now)

        with open(os.path.join(self.home,
                               "refresh-status-primary.txt")) as handle:
            self.assertTrue(handle.readline().startswith("OK"))

    def test_the_reading_is_usable_by_the_library(self):
        self.run_statusline(self.payload())
        environment = dict(os.environ)
        environment["SW_HOME"] = self.home
        done = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import swlib;"
             "print(swlib.freshness('primary'));"
             "print(swlib.headroom('primary', swlib.load_usage('primary')))",
             SCRIPTS],
            capture_output=True, text=True, timeout=20, env=environment,
            cwd=self.home)
        self.assertEqual(done.returncode, 0, done.stderr)
        # A quoted percentage used to make headroom unreadable.
        self.assertEqual(done.stdout.split(), ["fresh", "7"])

    def test_the_role_comes_from_the_config_dir(self):
        self.write_json(os.path.join(self.secondary_dir, ".claude.json"),
                        {"oauthAccount": {"emailAddress": "two@example.com"}})
        self.run_statusline(self.payload(), profile="secondary")
        self.assertTrue(os.path.exists(
            os.path.join(self.home, "usage-secondary.json")))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "usage-primary.json")))

    def test_the_reader_profile_caches_under_primary(self):
        # The reader profile is a second credential store for the same account,
        # so its figures are the primary's. A usage-reader.json would be read by
        # nothing at all.
        reader_dir = os.path.join(self.home, "profiles", "reader")
        os.makedirs(reader_dir)
        self.write_config(reader={"enabled": True, "config_dir": reader_dir})
        self.write_json(os.path.join(reader_dir, ".claude.json"),
                        {"oauthAccount": {"emailAddress": "one@example.com"}})
        environment = dict(os.environ)
        environment["SW_HOME"] = self.home
        environment["CLAUDE_CONFIG_DIR"] = reader_dir
        done = subprocess.run(["/bin/sh",
                               os.path.join(STAGE, "scripts", "statusline.sh")],
                              input=self.payload(), text=True,
                              capture_output=True, timeout=20, env=environment)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assert_absent("usage-reader.json", settle=0)
        self.assert_absent("refresh-status-reader.txt", settle=0)
        with open(os.path.join(self.home, "usage-primary.json")) as handle:
            cache = json.load(handle)
        self.assertEqual(cache["role"], "primary")
        self.assertEqual(cache["five_hour_pct"], 93)
        with open(os.path.join(self.home,
                               "refresh-status-primary.txt")) as handle:
            self.assertTrue(handle.readline().startswith("OK"))

    def test_the_status_file_is_renamed_into_place(self):
        # Written to a temp name and moved, so a hook reading it never sees a
        # half-written line. No stray temp file may be left behind either.
        self.run_statusline(self.payload())
        leftovers = [name for name in os.listdir(self.home)
                     if name.startswith("refresh-status-primary.txt.tmp")]
        self.assertEqual(leftovers, [])
        with open(os.path.join(self.home,
                               "refresh-status-primary.txt")) as handle:
            self.assertEqual(handle.read(),
                             "OK: interactive status line wrote this reading.\n")

    def test_unreadable_payload_writes_no_ok(self):
        self.write_status("primary", "FAILED: the usage reader stopped without "
                                     "saying why.")
        bar = self.run_statusline("this is not JSON at all")
        # The label still comes from the config; no figure is invented.
        self.assertNotIn("%", bar)
        self.assertNotIn("5h", bar)
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "usage-primary.json")),
            "a payload it could not read must not be cached as a reading")
        with open(os.path.join(self.home,
                               "refresh-status-primary.txt")) as handle:
            self.assertFalse(handle.readline().startswith("OK"))


class Installed(unittest.TestCase):
    """The hook files themselves. Setup installs them by absolute path with no
    interpreter, so the shebang and the executable bit are load bearing."""

    def test_every_hook_is_runnable(self):
        for name in ("session-start.py", "prompt-guard.py", "stop-failure.py",
                     "notification.py", "model-switch.py", "model-route.py"):
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
