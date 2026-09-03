#!/usr/bin/env python3
"""Parser tests over panels captured from the real clients.

Everything in tests/fixtures is the cleaned text of a panel one of these
clients actually printed on this project's test machine on 2 September 2026,
with two substitutions, because fixtures are tracked files in a public repo:
an email address becomes user@example.com, and the em dash this repo's writing
rule forbids becomes a hyphen. Nothing else was edited, with one exception,
named in the file: `codex-0.152.1-five-hour.txt` is the captured panel with a
five hour limit line added, because the plan on the capture machine shows no
five hour window at all. That case matters, so both are tested.

The `-trust.txt` fixtures were captured by starting each client in an empty and
untrusted directory under /tmp. No key was pressed to answer any of them.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, SCRIPTS)
import ptyreader  # noqa: E402
import swlib  # noqa: E402

CLAUDE_FIXTURE = "claude-2.1.251"
CODEX_FIXTURE = "codex-0.152.1"
GROK_FIXTURE = "grok-1.0.13"
CURSOR_FIXTURE = "cursor-2026.08.31-4057e58"


def script(name, filename):
    """Import a reader whose file name is not a Python identifier."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(SCRIPTS, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


claude = script("claude_usage", "claude-usage.py")
codex = script("codex_status", "codex-status.py")
grok = script("grok_usage", "grok-usage.py")
cursor = script("cursor_usage", "cursor-usage.py")
picker = script("model_picker", "model-picker.py")


def fixture(name):
    with open(os.path.join(FIXTURES, name + ".txt")) as handle:
        return handle.read()


class CleanTests(unittest.TestCase):
    """The renderer. These clients lay out a panel by moving the cursor, so a
    reader that only strips escape codes gets `Totalcost:$0.0000`."""

    def test_cursor_forward_becomes_spaces(self):
        self.assertEqual(ptyreader.clean(b"a\x1b[5Cb"), "a     b")

    def test_column_move_pads_to_that_column(self):
        self.assertEqual(ptyreader.clean(b"ab\x1b[6Cd"), "ab      d")
        self.assertEqual(ptyreader.clean(b"ab\x1b[9Gd"), "ab      d")

    def test_colour_codes_go(self):
        self.assertEqual(ptyreader.clean(b"\x1b[38;5;246mgrey\x1b[39m"), "grey")

    def test_vertical_move_starts_a_line(self):
        # This is why every marker in the readers uses \s+ and not a space: a
        # dialog painted a word at a time arrives one word per line.
        text = ptyreader.clean(b"Do\x1b[1Byou\x1b[1Btrust")
        self.assertIn("\n", text)
        self.assertIsNotNone(re.search(r"Do\s+you\s+trust", text))

    def test_a_real_panel_keeps_its_labels(self):
        text = fixture(CLAUDE_FIXTURE)
        self.assertIn("Total cost:", text)
        self.assertIn("Current week (all models)", text)


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.data = claude.parse_panel(fixture(CLAUDE_FIXTURE))

    def test_both_windows(self):
        self.assertEqual(self.data["five_hour_pct"], 23)
        self.assertEqual(self.data["seven_day_pct"], 35)

    def test_resets_read_as_written(self):
        self.assertEqual(self.data["five_hour_resets"],
                         "3:19am (Africa/Johannesburg)")
        self.assertEqual(self.data["seven_day_resets"],
                         "Sep 7 at 2:59am (Africa/Johannesburg)")

    def test_plan_and_version_from_the_banner(self):
        self.assertEqual(self.data["plan"], "Claude Team")
        self.assertEqual(self.data["client_version"], "2.1.251")

    def test_the_per_model_week_does_not_win(self):
        # The panel also prints "Current week (Fable) 42% used". The weekly
        # figure second-wind reports is the all-models one.
        self.assertIn("Current week (Fable)", fixture(CLAUDE_FIXTURE))
        self.assertEqual(self.data["seven_day_pct"], 35)

    def test_nothing_parsed_from_nothing(self):
        empty = claude.parse_panel("")
        self.assertIsNone(empty["five_hour_pct"])
        self.assertIsNone(empty["seven_day_pct"])

    def test_trust_dialog_is_recognised(self):
        trust = fixture(CLAUDE_FIXTURE + "-trust")
        self.assertIsNotNone(re.search(claude.TRUST, trust, re.I))
        # and it is not mistaken for a ready prompt
        self.assertIsNone(re.search(claude.PROMPT, trust, re.I))
        self.assertIsNone(re.search(claude.TRUST, fixture(CLAUDE_FIXTURE), re.I))


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.data = codex.parse_panel(fixture(CODEX_FIXTURE))

    def test_weekly_window(self):
        # The panel prints what is left, second-wind stores what is used.
        self.assertIn("87% left", fixture(CODEX_FIXTURE))
        self.assertEqual(self.data["seven_day_pct"], 13)
        self.assertEqual(self.data["seven_day_resets"], "18:00 on 8 Sep")

    def test_five_hour_is_null_when_the_line_is_absent(self):
        self.assertNotIn("5h limit", fixture(CODEX_FIXTURE))
        self.assertIsNone(self.data["five_hour_pct"])

    def test_five_hour_is_zero_when_the_line_says_all_left(self):
        data = codex.parse_panel(fixture(CODEX_FIXTURE + "-five-hour"))
        self.assertEqual(data["five_hour_pct"], 0)
        self.assertEqual(data["five_hour_resets"], "14:00")
        self.assertEqual(data["seven_day_pct"], 13)

    def test_account_plan_and_version(self):
        self.assertEqual(self.data["account"], "user@example.com")
        self.assertEqual(self.data["plan"], "Business Premium")
        self.assertEqual(self.data["client_version"], "0.152.1")

    def test_credits_go_in_extra(self):
        self.assertEqual(self.data["extra"].get("credits"), "Available")

    def test_trust_dialog_is_recognised(self):
        trust = fixture(CODEX_FIXTURE + "-trust")
        self.assertIsNotNone(re.search(codex.TRUST, trust, re.I))
        self.assertIsNone(re.search(codex.TRUST, fixture(CODEX_FIXTURE), re.I))


class GrokTests(unittest.TestCase):
    def setUp(self):
        self.data = grok.parse_panel(fixture(GROK_FIXTURE))

    def test_weekly_only(self):
        self.assertEqual(self.data["seven_day_pct"], 0)

    def test_plan_and_version(self):
        self.assertEqual(self.data["plan"], "X Premium+")
        self.assertEqual(self.data["client_version"], "1.0.13")

    def test_no_reset_line_is_no_reset_string(self):
        # This panel prints no reset time at all. A missing string is null
        # rather than a guess.
        self.assertIsNone(self.data["seven_day_resets"])

    def test_a_reset_line_stops_at_the_spinner(self):
        # The same client does print one on other runs, with its spinner and a
        # counter painted on the lines underneath. Those are not the reset time.
        text = fixture(GROK_FIXTURE).replace(
            "0%", "0%\nResets: September 4, 13:11\n3\n⠹\n4", 1)
        data = grok.parse_panel(text)
        self.assertEqual(data["seven_day_resets"], "September 4, 13:11")

    def test_no_trust_dialog_in_this_client(self):
        # Grok 1.0.13 started in an untrusted temporary directory without
        # asking anything. The pattern is defensive, so it must not fire.
        self.assertIsNone(re.search(grok.TRUST,
                                    fixture(GROK_FIXTURE + "-trust"), re.I))


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.data = cursor.parse_panel(fixture(CURSOR_FIXTURE))

    def test_monthly_pools(self):
        self.assertEqual(self.data["included_pct"], 1)
        self.assertEqual(self.data["auto_pct"], 1)
        self.assertEqual(self.data["api_pct"], 0)

    def test_on_demand_and_resets(self):
        self.assertEqual(self.data["on_demand"], "Disabled")
        self.assertEqual(self.data["resets"], "Sep 30")

    def test_plan_and_version(self):
        self.assertEqual(self.data["plan"], "Pro")
        self.assertEqual(self.data["client_version"], "2026.08.31-4057e58")

    def test_trust_dialog_is_recognised(self):
        trust = fixture(CURSOR_FIXTURE + "-trust")
        self.assertIsNotNone(re.search(cursor.TRUST, trust, re.I))
        self.assertIsNone(re.search(cursor.TRUST, fixture(CURSOR_FIXTURE), re.I))


class BudgetTests(unittest.TestCase):
    """The budget is an upper bound on the whole reading, so a wait started
    after it has gone is time nobody agreed to spend."""

    def test_a_wait_never_outlasts_the_budget(self):
        now = 1000.0
        self.assertEqual(ptyreader.budget_left(now + 10, 6, now), 6)
        self.assertEqual(ptyreader.budget_left(now + 2, 6, now), 2.0)

    def test_zero_once_the_budget_has_gone(self):
        now = 1000.0
        self.assertEqual(ptyreader.budget_left(now, 6, now), 0.0)
        self.assertEqual(ptyreader.budget_left(now - 30, 6, now), 0.0)
        # Every reader turns that zero into None, which is what makes it stop
        # instead of spending another second per wait past its budget.
        self.assertIsNone(ptyreader.budget_left(now - 30, 6, now) or None)


class ProviderKeyTests(unittest.TestCase):
    """No reader may leave a provider API key in its client's environment.

    A key that is set makes the client bill the key rather than the
    subscription the reading is supposed to measure, and the reading then
    describes an allowance nobody is spending. Screen is replaced by a recorder,
    so nothing is started and no client is driven.
    """

    STOP = RuntimeError("recorded")

    def setUp(self):
        self.real = ptyreader.Screen
        self.seen = {}
        recorder = self.seen
        stop = self.STOP

        class Recorder:
            def __init__(self, argv, **kwargs):
                recorder["argv"] = list(argv)
                recorder["env_drop"] = tuple(kwargs.get("env_drop") or ())
                raise stop

        ptyreader.Screen = Recorder

    def tearDown(self):
        ptyreader.Screen = self.real

    def drops(self, module):
        folder = tempfile.mkdtemp(prefix="second-wind-env-")
        try:
            with contextlib.suppress(type(self.STOP)):
                module.read_screen(folder, 30)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        self.assertIn("env_drop", self.seen, "read_screen built no Screen")
        return self.seen["env_drop"]

    def test_codex_drops_its_api_key(self):
        self.assertIn("OPENAI_API_KEY", self.drops(codex))

    def test_grok_drops_its_api_key(self):
        self.assertIn("XAI_API_KEY", self.drops(grok))

    def test_cursor_drops_its_api_key(self):
        self.assertIn("CURSOR_API_KEY", self.drops(cursor))

    def test_the_claude_reader_drops_its_nested_session_markers(self):
        folder = tempfile.mkdtemp(prefix="second-wind-env-")
        try:
            with contextlib.suppress(type(self.STOP)):
                claude.read_screen(folder, folder, 30)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        self.assertIn("CLAUDECODE", self.seen["env_drop"])


class ReaderMainTests(unittest.TestCase):
    """What each reader leaves behind when the screen never gets read. The
    client is not started: read_screen is replaced, because these are tests of
    the reporting, not of the driving."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="second-wind-reader-")
        self.status = os.path.join(self.tmp, "status.txt")
        self.out = os.path.join(self.tmp, "usage.json")
        self.real = claude.read_screen

    def tearDown(self):
        claude.read_screen = self.real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self):
        argv = sys.argv
        sys.argv = ["claude-usage.py", "--role", "primary",
                    "--config-dir", self.tmp, "--cwd", self.tmp,
                    "--out", self.out, "--status", self.status]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return claude.main()
        finally:
            sys.argv = argv

    def first_line(self):
        with open(self.status) as handle:
            return handle.readline().strip()

    def test_an_os_failure_writes_a_status_rather_than_a_traceback(self):
        def explode(*args):
            raise OSError("out of pty devices\nsecond line")
        claude.read_screen = explode
        self.assertEqual(self.run_main(), 1)
        self.assertEqual(self.first_line(),
                         "FAILED: OSError: out of pty devices")
        self.assertFalse(os.path.exists(self.out))

    def test_an_exhausted_budget_says_which_state_it_stopped_in(self):
        claude.read_screen = lambda *args: ("budget: the prompt box",
                                            "a screen with no figures on it")
        self.assertEqual(self.run_main(), 1)
        self.assertTrue(self.first_line().startswith(
            "FAILED: budget exhausted before the prompt box"), self.first_line())
        self.assertIn("a screen with no figures on it", self.first_line())
        self.assertFalse(os.path.exists(self.out))

    def test_a_panel_writes_the_cache_and_says_OK(self):
        claude.read_screen = lambda *args: ("panel", fixture(CLAUDE_FIXTURE))
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.first_line(), "OK")
        with open(self.out) as handle:
            record = json.load(handle)
        self.assertEqual(record["role"], "primary")
        self.assertEqual(record["worker"], "claude")
        self.assertEqual(record["five_hour_pct"], 23)
        self.assertEqual(record["seven_day_pct"], 35)
        self.assertEqual(record["client_version"], "2.1.251")
        self.assertIsInstance(record["cached_at"], int)


class PickerTests(unittest.TestCase):
    """The model picker edits a real settings.json, so these tests give it a
    throwaway one and check that the keys it did not come for survive."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="second-wind-picker-")
        self.state = os.path.join(self.tmp, "state")
        self.profile = os.path.join(self.tmp, "profile")
        os.makedirs(self.profile)
        os.environ["SW_HOME"] = self.state
        with open(os.path.join(FIXTURES, "config-picker.json")) as handle:
            self.cfg = json.load(handle)
        self.cfg["primary"]["config_dir"] = self.profile
        swlib.write_json_atomic(swlib.config_path(), self.cfg)
        self.settings = os.path.join(self.profile, "settings.json")

    def tearDown(self):
        os.environ.pop("SW_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def reading(self, role, five, week):
        swlib.write_json_atomic(swlib.usage_path(role), {
            "role": role, "worker": role, "five_hour_pct": five,
            "seven_day_pct": week, "extra": {},
            "cached_at": int(time.time())})

    def test_description_from_the_fixture_config(self):
        self.reading("primary", 2, 16)
        self.reading("secondary", 1, 10)
        self.reading("codex", None, 7)
        self.assertEqual(picker.describe(self.cfg),
                         "Work 5h 2% · 7d 16% | Personal 5h 1% · 7d 10% "
                         "| Codex 7d 7%")

    def test_a_missing_reading_says_so_rather_than_showing_a_figure(self):
        self.reading("primary", 2, 16)
        self.reading("codex", None, 7)
        self.assertEqual(picker.describe(self.cfg),
                         "Work 5h 2% · 7d 16% | Personal no reading "
                         "| Codex 7d 7%")

    def test_the_description_is_capped(self):
        long_cfg = json.loads(json.dumps(self.cfg))
        long_cfg["primary"]["label"] = "Work-with-a-very-long-name Claude"
        long_cfg["secondary"]["label"] = "Personal-with-a-long-name-too Claude"
        for role in ("primary", "secondary"):
            self.reading(role, 22, 33)
        self.reading("codex", 11, 7)
        text = picker.describe(long_cfg)
        self.assertLessEqual(len(text), 90)
        self.assertTrue(text)

    def test_other_settings_keys_survive(self):
        self.reading("primary", 2, 16)
        self.reading("secondary", 1, 10)
        self.reading("codex", None, 7)
        swlib.write_json_atomic(self.settings, {
            "theme": "dark",
            "hooks": {"SessionStart": [{"hooks": [{"type": "command",
                                                   "command": "/bin/true"}]}]},
            "permissions": {"allow": ["Bash(ls:*)"]}})
        picker.apply(self.cfg)
        with open(self.settings) as handle:
            data = json.load(handle)
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["permissions"], {"allow": ["Bash(ls:*)"]})
        self.assertIn("SessionStart", data["hooks"])
        block = data["modelPicker"]
        self.assertIs(block["_second_wind"], True)
        self.assertIs(block["replaceBuiltInOptions"], True)
        self.assertEqual([row["value"] for row in block["options"]],
                         ["fable", "opus", "sonnet", "haiku"])
        self.assertEqual([row["label"] for row in block["options"]],
                         ["Fable", "Opus", "Sonnet", "Haiku"])
        self.assertEqual(block["options"][0]["description"],
                         picker.describe(self.cfg))

    def test_a_backup_is_taken_before_the_first_edit(self):
        self.reading("primary", 2, 16)
        swlib.write_json_atomic(self.settings, {"theme": "dark"})
        picker.apply(self.cfg)
        self.assertTrue(os.path.exists(self.settings + ".second-wind-original"))

    def test_off_removes_only_our_own_key(self):
        self.reading("primary", 2, 16)
        swlib.write_json_atomic(self.settings, {"theme": "dark"})
        picker.apply(self.cfg)
        picker.apply(self.cfg, on=False)
        with open(self.settings) as handle:
            data = json.load(handle)
        self.assertNotIn("modelPicker", data)
        self.assertEqual(data["theme"], "dark")

    def test_off_leaves_someone_elses_picker_alone(self):
        theirs = {"options": [{"value": "opus", "label": "Their own"}]}
        swlib.write_json_atomic(self.settings, {"modelPicker": theirs})
        picker.apply(self.cfg, on=False)
        with open(self.settings) as handle:
            data = json.load(handle)
        self.assertEqual(data["modelPicker"], theirs)

    def test_settings_that_are_not_json_are_left_alone(self):
        with open(self.settings, "w") as handle:
            handle.write("{not json")
        message = picker.apply(self.cfg)
        self.assertIn("not valid JSON", message)
        with open(self.settings) as handle:
            self.assertEqual(handle.read(), "{not json")


if __name__ == "__main__":
    unittest.main()
