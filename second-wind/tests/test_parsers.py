#!/usr/bin/env python3
"""Parser tests over panels captured from the real clients.

Everything in tests/fixtures is the cleaned text of a panel one of these
clients actually printed on this project's test machine on 2 September 2026.
Fixtures are tracked files in a public repo, so identifying detail was
substituted before they were committed: an email address becomes
user@example.com, a Codex session id becomes a zero UUID, MCP server names
become mcp_a, mcp_b and so on, a credential prompt naming one service becomes a
generic one, a time zone name becomes UTC, and the em dash this repo's writing
rule forbids becomes a hyphen. Layout was preserved through each substitution,
because the parsers read columns. Nothing else was edited, with one exception,
named in the file: `codex-0.152.1-five-hour.txt` is the captured panel with a
five hour limit line added, because the plan on the capture machine shows no
five hour window at all. That case matters, so both are tested. Likewise
`codex-0.152.1-refresh.txt` is that panel with its limit line replaced by the
"refresh requested" line Codex 0.153.0 printed on 24 September 2026 when
/status landed before it had fetched the limits.

The Codex 0.159.2 and 0.160.0 fixtures were captured on 1 October 2026, and
differently, because from 0.158 Codex draws full screen and sends only the
cells that change. Each `.raw` file is the byte stream the reader took off the
PTY, driven by the reader's own read_screen; the `.txt` beside it is that
stream on the screen model, which is the text the reader parses and `--dump`
writes. The reader asked for /status more than once in each, because the parser
of the day could not read the first panel. These panels print no address, so
none needed substituting. Each session id became the zero UUID, and a capture
in which that substitution changed any other cell of the rendered screen was
left out, which is why 0.160.0 has no plain fixture. `codex-0.159.2` is a plan
with one weekly window; a `-model-week` fixture also carries a model's own week.

The `-trust.txt` fixtures were captured by starting each client in an empty and
untrusted directory under /tmp. No key was pressed to answer any of them. The
Codex 0.159.2 and 0.160.0 `-trust` captures were taken the same way in a new git
folder, because those versions show no dialog in an empty directory. What the
trust tests then check is one regex against that saved text: the pattern
matches the dialog and does not match a normal panel. No client is started
here. CodexReaderKeystrokes drives the Codex reader's read_screen against
scripted screens, the captured 0.160.0 dialog among them, so for Codex the stop
at a dialog is tested. For the other readers it is exercised by hand, and the
procedure is in TESTING.md.
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
CODEX_SCREENS = ("codex-0.159.2", "codex-0.159.2-model-week",
                 "codex-0.160.0-model-week")
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


def raw_fixture(name):
    with open(os.path.join(FIXTURES, name + ".raw"), "rb") as handle:
        return handle.read()


class CleanTests(unittest.TestCase):
    """The renderer. These clients lay out a panel by moving the cursor, so a
    reader that only strips escape codes gets `Totalcost:$0.0000`.

    The escape sequences here are written by hand, one behaviour per test, and
    the one fixture case reads a file that was already cleaned before it was
    saved. So this is a test of the renderer's rules and of the labels the saved
    panel carries, not of a raw byte stream coming off a live client."""

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


class ScreenTests(unittest.TestCase):
    """The screen model, for a client that draws full screen. Such a client
    sends only the cells that changed since its last frame, each run placed by
    a cursor move, so the order the bytes arrive in is not the order of the
    screen. Codex has drawn this way since 0.158.

    The sequences here are written by hand, one behaviour per test. The
    captured streams are read in CodexFullScreenTests."""

    def show(self, raw, rows=5, cols=40):
        return ptyreader.render(b"\x1b[?1049h" + raw, rows, cols)[0]

    def test_a_jump_along_one_row_keeps_the_row_whole(self):
        # How Codex 0.159.2 sent its weekly line: the label and the bar, then
        # a jump over each blank cell it had no need to send again.
        raw = b"\x1b[2;1H  Weekly limit: [###]\x1b[2;23H77%\x1b[2;27Hleft"
        self.assertEqual(self.show(raw).splitlines()[1],
                         "  Weekly limit: [###] 77% left")

    def test_a_letter_already_on_screen_is_not_lost(self):
        # Codex 0.160.0 repainted "Session:" over a row that already held an
        # "e" in that cell, so it sent "S", a jump, then "ssion:".
        raw = b"\x1b[1;1HSe\x1b[1;1HS\x1b[1;3Hssion:"
        self.assertEqual(self.show(raw), "Session:")

    def test_erase_in_line_clears_from_the_cursor(self):
        self.assertEqual(self.show(b"\x1b[1;1H100% left\x1b[1;4H\x1b[K"), "100")

    def test_erase_in_display_clears_from_the_cursor_or_the_lot(self):
        two = b"\x1b[1;1Hone\x1b[2;1Htwo"
        self.assertEqual(self.show(two + b"\x1b[1;2H\x1b[J"), "o")
        self.assertEqual(self.show(two + b"\x1b[2J"), "")

    def test_a_wide_character_takes_two_cells(self):
        raw = "\x1b[1;1H✨\x1b[1;3Hx".encode()
        self.assertEqual(self.show(raw), "✨x")

    def test_relative_moves_and_column_moves(self):
        raw = b"\x1b[3;3Hx\x1b[2Ay\x1b[7Gz"
        self.assertEqual(self.show(raw), "   y  z\n\n  x")

    def test_colours_titles_and_queries_change_nothing_on_screen(self):
        raw = b"\x1b[1;1H\x1b[2mdim\x1b[22m\x1b]0;title\x07\x1b[>7u\x1b[?2026h ok"
        self.assertEqual(self.show(raw), "dim ok")

    def test_a_saved_cursor_is_restored(self):
        raw = b"\x1b[1;1Hab\x1b7\x1b[3;1Hz\x1b8c"
        self.assertEqual(self.show(raw), "abc\n\nz")

    def test_full_screen_is_reported_only_while_the_alternate_screen_is_on(self):
        self.assertTrue(ptyreader.render(b"\x1b[?1049hx")[1])
        self.assertFalse(ptyreader.render(b"x")[1])
        self.assertFalse(ptyreader.render(b"\x1b[?1049hx\x1b[?1049l")[1])

    def test_leaving_the_alternate_screen_shows_the_main_one_again(self):
        raw = b"main\x1b[?1049h\x1b[1;1Halt\x1b[?1049l"
        self.assertEqual(ptyreader.render(raw)[0], "main")

    def test_a_carriage_return_writes_over_the_line(self):
        self.assertEqual(ptyreader.render(b"abc\rX")[0], "Xbc")

    def test_a_line_feed_on_the_last_row_scrolls_the_screen_up(self):
        self.assertEqual(ptyreader.render(b"a\r\nb\r\nc\r\nd", rows=3)[0],
                         "b\nc\nd")

    def test_a_line_longer_than_the_screen_wraps(self):
        self.assertEqual(ptyreader.render(b"abcdef", cols=4)[0], "abcd\nef")

    def test_a_scrolling_region_scrolls_on_its_own(self):
        four = b"1\r\n2\r\n3\r\n4\x1b[2;3r"
        # A line feed on the region's last row scrolls rows 2 and 3 only.
        self.assertEqual(ptyreader.render(four + b"\x1b[3;1H\n", rows=4)[0],
                         "1\n3\n\n4")
        # A reverse index on its first row scrolls them the other way.
        self.assertEqual(ptyreader.render(four + b"\x1b[2;1H\x1bM", rows=4)[0],
                         "1\n\n2\n4")

    def test_lines_are_inserted_and_deleted_at_the_cursor(self):
        four = b"1\r\n2\r\n3\r\n4\x1b[2;1H"
        self.assertEqual(ptyreader.render(four + b"\x1b[L", rows=4)[0],
                         "1\n\n2\n3")
        self.assertEqual(ptyreader.render(four + b"\x1b[M", rows=4)[0],
                         "1\n3\n4")

    def test_characters_are_inserted_deleted_and_erased_at_the_cursor(self):
        six = b"abcdef\x1b[1;2H"
        self.assertEqual(ptyreader.render(six + b"\x1b[2P")[0], "adef")
        self.assertEqual(ptyreader.render(six + b"\x1b[2@")[0], "a  bcdef")
        self.assertEqual(ptyreader.render(six + b"\x1b[2X")[0], "a  def")

    def test_a_combining_mark_stays_in_the_cell_before_it(self):
        raw = "é\x1b[1;2Hx".encode()
        self.assertEqual(ptyreader.render(raw)[0], "éx")

    def test_writing_over_half_a_wide_character_blanks_the_other_half(self):
        raw = "✨\x1b[1;2Hx".encode()
        self.assertEqual(ptyreader.render(raw)[0], " x")

    def test_the_other_cursor_and_erase_operations(self):
        cases = [
            ("next line", b"ab\x1b[Ecd", "ab\ncd"),
            ("previous line", b"\x1b[2;3Hab\x1b[Fcd", "cd\n  ab"),
            ("row, column kept", b"ab\x1b[3dcd", "ab\n\n  cd"),
            ("column", b"ab\x1b[5`cd", "ab  cd"),
            ("columns forward", b"ab\x1b[2acd", "ab  cd"),
            ("rows down", b"ab\x1b[ecd", "ab\n  cd"),
            ("row and column", b"\x1b[2;3fab", "\n  ab"),
            ("save and restore", b"ab\x1b[s\x1b[3;1Hz\x1b[uc", "abc\n\nz"),
            ("scroll up", b"1\r\n2\r\n3\x1b[S", "2\n3"),
            ("scroll down", b"1\r\n2\r\n3\x1b[T", "\n1\n2"),
            ("index", b"ab\x1bDcd", "ab\n  cd"),
            ("next line by escape", b"ab\x1bEcd", "ab\ncd"),
            ("reset", b"ab\x1bccd", "cd"),
            ("erase above", b"one\r\ntwo\x1b[2;2H\x1b[1J", "\n  o"),
            ("erase line to the cursor", b"abcdef\x1b[1;3H\x1b[1K", "   def"),
            ("erase the whole line", b"abcdef\x1b[1;3H\x1b[2K", ""),
            ("backspace", b"ab\bc", "ac"),
            ("tab", b"a\tb", "a       b"),
        ]
        for name, raw, wanted in cases:
            with self.subTest(name):
                self.assertEqual(ptyreader.render(raw, rows=3, cols=10)[0], wanted)

    def test_with_wrapping_off_the_last_column_is_written_over(self):
        self.assertEqual(ptyreader.render(b"\x1b[?7labcdef", cols=4)[0], "abcf")

    def test_erasing_the_saved_lines_leaves_the_screen_alone(self):
        # CSI 3 J clears the scrollback, which this model does not keep.
        self.assertEqual(self.show(b"\x1b[1;1Hpanel\x1b[3J"), "panel")

    def test_an_erase_in_a_mode_it_does_not_know_does_nothing(self):
        self.assertEqual(self.show(b"\x1b[1;1Hpanel\x1b[1;1H\x1b[9K\x1b[9J"), "panel")

    def test_a_shift_through_a_wide_character_leaves_no_half_of_it(self):
        # Deleting its left half blanks its right half.
        raw = "✨A\x1b[1;1H\x1b[P\x1b[1;2HB".encode()
        self.assertEqual(ptyreader.render(raw)[0], " B")
        # Pushed half off the end of the line, it goes.
        raw = "ab✨\x1b[1;1H\x1b[@".encode()
        self.assertEqual(ptyreader.render(raw, cols=4)[0], " ab")


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.data = claude.parse_panel(fixture(CLAUDE_FIXTURE))

    def test_both_windows(self):
        self.assertEqual(self.data["five_hour_pct"], 23)
        self.assertEqual(self.data["seven_day_pct"], 35)

    def test_resets_read_as_written(self):
        self.assertEqual(self.data["five_hour_resets"],
                         "3:19am (UTC)")
        self.assertEqual(self.data["seven_day_resets"],
                         "Sep 7 at 2:59am (UTC)")

    def test_plan_and_version_from_the_banner(self):
        self.assertEqual(self.data["plan"], "Claude Team")
        self.assertEqual(self.data["client_version"], "2.1.251")

    def test_the_per_model_week_does_not_win(self):
        # The panel also prints "Current week (Fable) 42% used". The weekly
        # figure second-wind reports is the all-models one.
        self.assertIn("Current week (Fable)", fixture(CLAUDE_FIXTURE))
        self.assertEqual(self.data["seven_day_pct"], 35)

    def test_the_per_model_week_is_kept_under_its_own_name(self):
        """Not winning is not the same as being thrown away. A model week can
        be much the fuller of the two, and it was dropped entirely."""
        self.assertEqual(self.data["model_weeks"],
                         {"Fable": {"pct": 42,
                                    "resets": "Sep 7 at 2:59am (UTC)"}})

    def test_a_window_never_takes_the_next_window_s_reset(self):
        """The fault that shipped a wrong reset time: each window was read
        from a fixed-width slice, which ran past its own reset line into the
        next window's. A window with no reset line of its own reports none,
        rather than borrowing the week's."""
        panel = "\n".join([
            "  Current session", "", "  17% used", "",
            "  Current week (all models)", "", "  48% used", "",
            "  Resets Sep 14 at 3am (Africa/Johannesburg)", ""])
        data = claude.parse_panel(panel)
        self.assertEqual(data["five_hour_pct"], 17)
        self.assertIsNone(data["five_hour_resets"])
        self.assertEqual(data["seven_day_pct"], 48)
        self.assertEqual(data["seven_day_resets"],
                         "Sep 14 at 3am (Africa/Johannesburg)")

    def test_a_widely_padded_panel_still_reads_both_windows(self):
        """A real terminal pads every line to the width of the window, which
        is what pushed the figures outside the old slice: the live panel that
        exposed this is far wider than the captured fixtures."""
        pad = " " * 400
        panel = "\n".join([
            "  Current session" + pad, pad, "  34% used" + pad, pad,
            "  Resets 12pm (Africa/Johannesburg)" + pad, pad,
            "  Current week (all models)" + pad, pad, "  48% used" + pad, pad,
            "  Resets Sep 14 at 3am (Africa/Johannesburg)" + pad, pad])
        data = claude.parse_panel(panel)
        self.assertEqual(data["five_hour_pct"], 34)
        self.assertEqual(data["five_hour_resets"], "12pm (Africa/Johannesburg)")
        self.assertEqual(data["seven_day_pct"], 48)
        self.assertEqual(data["seven_day_resets"],
                         "Sep 14 at 3am (Africa/Johannesburg)")

    def test_a_half_drawn_repaint_does_not_replace_a_full_one(self):
        """The panel repaints while it scans local sessions, so the buffer
        holds several renders. A later render with no figure under a label
        leaves the earlier reading alone."""
        good = "\n".join(["Current session", "34% used",
                           "Resets 12pm (Africa/Johannesburg)",
                           "Current week (all models)", "48% used",
                           "Resets Sep 14 at 3am (Africa/Johannesburg)"])
        data = claude.parse_panel(good + "\nCurrent session\n")
        self.assertEqual(data["five_hour_pct"], 34)
        self.assertEqual(data["five_hour_resets"], "12pm (Africa/Johannesburg)")

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


    def test_refresh_requested_is_not_a_reading(self):
        # Codex prints this before it has fetched the limits. The reader asks
        # again; it must never be taken for a panel with its windows.
        text = fixture(CODEX_FIXTURE + "-refresh")
        self.assertIsNotNone(re.search(codex.REFRESHING, text, re.I))
        self.assertFalse(codex.has_limits(codex.parse_panel(text)))
        self.assertIsNone(re.search(codex.REFRESHING, fixture(CODEX_FIXTURE), re.I))
        self.assertTrue(codex.has_limits(self.data))


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
    after it has gone is time nobody agreed to spend.

    These call budget_left directly with a deadline and a clock reading. They
    check that one function's arithmetic, and not that a reading actually stops
    on time: nothing here starts a reader or waits for anything."""

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
        # The sign-in preflight starts the real client, which would make these
        # tests depend on whichever accounts this machine happens to hold. It
        # is stubbed to "signed in" so each test exercises the reporting it is
        # about; the preflight has tests of its own below.
        self.real_auth = claude.auth_state
        claude.auth_state = lambda config_dir: (True, "user@example.com")

    def tearDown(self):
        claude.read_screen = self.real
        claude.auth_state = self.real_auth
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

    def test_a_profile_with_no_sign_in_says_so_without_starting_the_client(self):
        """The fault found on 11 September 2026: the panel said "login
        expired, sign in again" for an account that was signed in, because the
        desktop app holds that credential and the reader profile had none of
        its own. Expired and never-signed-in are different faults and the
        reader now names the second one, with the command that fixes it, and
        without spending a minute of budget finding out."""
        claude.auth_state = lambda config_dir: (False, None)
        started = []
        claude.read_screen = lambda *args: started.append(args) or ("panel", "")
        self.assertEqual(self.run_main(), 2)
        self.assertEqual(started, [])
        line = self.first_line()
        self.assertTrue(line.startswith("NO CLI SIGN-IN:"), line)
        self.assertIn("claude auth login", line)
        self.assertEqual(swlib.status_kind(message=line), "nologin")
        self.assertFalse(os.path.exists(self.out))

    def test_a_client_that_will_not_answer_still_gets_the_panel_tried(self):
        """A silent client is not evidence of a signed-out profile, so the
        reader falls through to the panel rather than reporting a fault it
        cannot prove."""
        claude.auth_state = lambda config_dir: (None, None)
        claude.read_screen = lambda *args: ("panel", fixture(CLAUDE_FIXTURE))
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.first_line(), "OK")


class ProfileChoiceTests(unittest.TestCase):
    """Which profile a role is read through, and in what order.

    The reader profile is preferred when it is configured, and the role's own
    directory stays behind it as the fallback. A reader profile whose sign-in
    has gone used to hide a profile whose sign-in was fine, and the panel then
    reported the account unreadable with a working sign-in one directory away.
    """

    def test_the_reader_profile_is_tried_first_then_the_role_s_own(self):
        cfg = {"primary": {"config_dir": "~/.claude"},
               "reader": {"enabled": True, "config_dir": "~/.claude-usage"}}
        self.assertEqual(claude.profile_candidates("primary", cfg),
                         ["~/.claude-usage", "~/.claude"])

    def test_a_disabled_reader_leaves_the_role_on_its_own(self):
        cfg = {"primary": {"config_dir": "~/.claude"},
               "reader": {"enabled": False, "config_dir": "~/.claude-usage"}}
        self.assertEqual(claude.profile_candidates("primary", cfg), ["~/.claude"])

    def test_the_reader_profile_is_only_for_the_primary(self):
        cfg = {"secondary": {"config_dir": "~/.claude-critic"},
               "reader": {"enabled": True, "config_dir": "~/.claude-usage"}}
        self.assertEqual(claude.profile_candidates("secondary", cfg),
                         ["~/.claude-critic"])

    def test_the_default_profile_is_read_with_the_variable_removed(self):
        """Setting CLAUDE_CONFIG_DIR for the default profile breaks its
        keychain lookup, so the preflight removes it rather than setting it to
        the same path."""
        env = claude.profile_env(os.path.join(swlib.HOME, ".claude"))
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)

    def test_another_profile_is_read_with_the_variable_set(self):
        env = claude.profile_env("~/.claude-usage")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"],
                         os.path.join(swlib.HOME, ".claude-usage"))

    def test_the_preflight_drops_the_same_variables_as_the_panel(self):
        """Both look at one sign-in or they are answering about different
        ones."""
        env = claude.profile_env("~/.claude-usage")
        for name in claude.ENV_DROP:
            self.assertNotIn(name, env, name)

    def test_the_command_names_the_profile_it_has_to_run_under(self):
        message = claude.no_sign_in_message(["~/.claude-usage"])
        self.assertIn("CLAUDE_CONFIG_DIR=~/.claude-usage claude auth login",
                      message)

    def test_the_default_profile_needs_no_variable_in_the_command(self):
        message = claude.no_sign_in_message([os.path.join(swlib.HOME, ".claude")])
        self.assertIn("Run: claude auth login", message)
        self.assertNotIn("CLAUDE_CONFIG_DIR", message)


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

    def test_a_routing_row_per_worker_and_per_configured_route(self):
        self.reading("primary", 2, 16)
        self.reading("secondary", 1, 10)
        self.reading("codex", None, 7)
        rows = picker.route_rows(self.cfg)
        self.assertEqual([row["model"] for row in rows],
                         ["second-wind/personal", "second-wind/codex",
                          "second-wind/codex/gpt-6.1-sol/high"])
        self.assertEqual([row["label"] for row in rows],
                         ["Route: Personal Claude", "Route: Codex",
                          "Route: Codex, gpt-6.1-sol, high"])
        self.assertEqual(rows[0]["description"],
                         "5h 1% · 7d 10% · picking this routes the next "
                         "tasks there")
        for row in rows:
            self.assertLessEqual(len(row["description"]), 90)
            # Every row is a target the hook will accept.
            self.assertTrue(swlib.parse_route(row["model"], self.cfg))

    def test_a_route_for_a_worker_that_is_not_connected_is_dropped(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["codex"]["enabled"] = False
        cfg["picker"]["routes"] = ["second-wind/grok", "second-wind/cursor"]
        self.reading("primary", 2, 16)
        self.reading("secondary", 1, 10)
        self.assertEqual([row["model"] for row in picker.route_rows(cfg)],
                         ["second-wind/personal"])

    def test_a_row_with_no_reading_says_so(self):
        self.reading("primary", 2, 16)
        rows = picker.route_rows(self.cfg)
        self.assertEqual(rows[0]["description"],
                         "no reading · picking this routes the next tasks there")

    def test_the_row_description_keeps_its_sentence_when_it_is_cut(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["secondary"]["label"] = "a label long enough to crowd the row out"
        cfg["picker"]["routes"] = []
        self.reading("primary", 2, 16)
        self.reading("secondary", 11, 22)
        self.reading("codex", 33, 44)
        for row in picker.route_rows(cfg):
            self.assertLessEqual(len(row["description"]), 90)
            self.assertTrue(row["description"].endswith(
                "picking this routes the next tasks there"), row["description"])

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
        self.assertEqual([row["model"] for row in block["options"]],
                         ["fable", "opus", "sonnet", "haiku",
                          "second-wind/personal", "second-wind/codex",
                          "second-wind/codex/gpt-6.1-sol/high"])
        self.assertEqual([row["label"] for row in block["options"][:4]],
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
        theirs = {"options": [{"model": "opus", "label": "Their own"}]}
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




class CodexFreeWorkspacePanel(unittest.TestCase):
    """A Free workspace prints one monthly line and no 5h or weekly line."""

    PANEL = (
        "│  Account:              user@example.com (Free)                                 │\n"
        "│  Monthly limit:        [██████████████████░░] 92% left (resets 15:50 on 10 Oct) │\n"
        "│  Monthly credit limit: [██████████░░░░░░░░░░] 50% left (resets 1 Nov)          │\n"
    )

    def test_the_panel_is_recognised_at_all(self):
        self.assertIsNotNone(re.search(codex.PANEL, self.PANEL, re.I))

    def test_the_monthly_window_is_read_and_kept_apart_from_credits(self):
        data = codex.parse_panel(self.PANEL)
        self.assertEqual(data["plan"], "Free")
        self.assertIsNone(data["five_hour_pct"])
        self.assertIsNone(data["seven_day_pct"])
        self.assertEqual(data["extra"]["monthly_pct"], 8)
        self.assertEqual(data["extra"]["monthly_resets"], "15:50 on 10 Oct")
        self.assertEqual(data["extra"]["monthly_credit_pct"], 50)

    def test_a_business_panel_has_no_monthly_window(self):
        data = codex.parse_panel(fixture(CODEX_FIXTURE))
        self.assertNotIn("monthly_pct", data["extra"])

class CodexModelWindowPanels(unittest.TestCase):
    """A panel can print a window belonging to one model beside the plan's own.

    Both shapes below were captured on 15 September 2026, with the address
    substituted. Read as the plan's own, a model's window replaces the figure
    that decides where work goes: on the Pro Lite panel the heading comes last,
    and a reader taking the final Weekly line called a quarter-spent week
    untouched and reported the account as idle on both windows.
    """

    PRO_LITE = (
        "\u2502  Account:                     user@example.com (Pro Lite)                     \u2502\n"
        "\u2502  Weekly limit:                [\u2588\u2588\u2588\u2591\u2591] 74% left (resets 09:48 on 22 Sep)  \u2502\n"
        "\u2502  GPT-5.3-Codex-Spark limit:                                                    \u2502\n"
        "\u2502  5h limit:                    [\u2588\u2588\u2588\u2588\u2588] 100% left (resets 17:28)           \u2502\n"
        "\u2502  Weekly limit:                [\u2588\u2588\u2588\u2588\u2588] 100% left (resets 12:28 on 22 Sep) \u2502\n"
    )
    PLUS = (
        "\u2502  Account:                    user@example.com (Plus)                           \u2502\n"
        "\u2502  gpt-reserve Weekly limit:   [\u2588\u2588\u2588\u2588\u2588] 100% left (resets 12:29 on 22 Sep) \u2502\n"
        "\u2502  5h limit:                   [\u2588\u2588\u2588\u2591\u2591] 62% left (resets 14:10)            \u2502\n"
        "\u2502  Weekly limit:               [\u2588\u2588\u2591\u2591\u2591] 37% left (resets 10:09 on 19 Sep)  \u2502\n"
    )

    def test_a_heading_below_the_plan_week_does_not_take_it_over(self):
        data = codex.parse_panel(self.PRO_LITE)
        self.assertEqual(data["seven_day_pct"], 26)
        self.assertEqual(data["seven_day_resets"], "09:48 on 22 Sep")
        # Pro Lite prints no five-hour window of the plan's own, and the one
        # under the heading is not it.
        self.assertIsNone(data["five_hour_pct"])

    def test_the_model_under_a_heading_is_kept_under_its_own_name(self):
        extra = codex.parse_panel(self.PRO_LITE)["extra"]
        self.assertEqual(extra["model_weeks"]["GPT-5.3-Codex-Spark"]["pct"], 0)
        self.assertEqual(extra["model_five_hours"]["GPT-5.3-Codex-Spark"]["resets"],
                         "17:28")

    def test_a_model_named_in_front_of_a_label_is_not_the_plan_week(self):
        data = codex.parse_panel(self.PLUS)
        self.assertEqual(data["five_hour_pct"], 38)
        self.assertEqual(data["seven_day_pct"], 63)
        self.assertEqual(data["extra"]["model_weeks"]["gpt-reserve"]["pct"], 0)

    def test_a_panel_with_no_model_window_carries_no_model_block(self):
        extra = codex.parse_panel(fixture(CODEX_FIXTURE))["extra"]
        self.assertNotIn("model_weeks", extra)
        self.assertNotIn("model_five_hours", extra)

    def test_what_is_left_of_a_panel_scrolled_off_the_top_lends_nothing(self):
        # On the screen an earlier panel can be half scrolled away, its
        # heading gone and the model's windows under it still showing. Read as
        # the plan's own, the model's 5h window became the plan's.
        top = ("│  5h limit:                    [█████] 100% left (resets 17:28)           │\n"
               "│  Weekly limit:                [█████] 100% left (resets 12:28 on 22 Sep) │\n")
        data = codex.parse_panel(top + self.PRO_LITE)
        self.assertIsNone(data["five_hour_pct"])
        self.assertEqual(data["seven_day_pct"], 26)


class CodexFullScreenTests(unittest.TestCase):
    """Codex 0.159.2 and 0.160.0, captured as bytes. The fault these caught:
    the plan's weekly row came off the PTY in three runs with a jump between
    each, so the text read in the order painted split it over three lines and
    the reading reported PARSER MISMATCH."""

    WEEK = ("  Weekly limit:               [" + "█" * 15 + "░" * 5
            + "] 77% left (resets 13:31 on 4 Oct)")

    def screen_of(self, raw):
        screen = ptyreader.Screen(["codex"])
        screen.chunks = [(0.0, raw)]
        return screen

    def test_each_capture_renders_to_the_screen_saved_beside_it(self):
        """A snapshot lock: each .txt was made by this renderer from its .raw,
        so this catches a change in the rendering, not a wrong first one. The
        rows checked by hand in the next test are the independent check."""
        for name in CODEX_SCREENS + ("codex-0.159.2-trust", "codex-0.160.0-trust"):
            with self.subTest(name):
                text, full_screen = ptyreader.render(raw_fixture(name))
                self.assertTrue(full_screen)
                self.assertEqual(text, fixture(name))

    def test_the_rows_the_client_sent_in_pieces_are_whole_on_screen(self):
        # Worked out by hand from the bytes: the label and bar to column 52,
        # a jump to 54 for "77%", a jump to 58 for "left (resets ...)".
        lines = fixture("codex-0.160.0-model-week").splitlines()
        self.assertIn(self.WEEK, lines)
        # These two were each missing a letter that was already on screen.
        self.assertIn("  Session:                    "
                      "00000000-0000-4000-8000-000000000000", lines)
        self.assertIn("  Collaboration mode:         Default", lines)

    def test_a_full_screen_client_is_read_from_the_screen_it_shows(self):
        raw = raw_fixture("codex-0.159.2-model-week")
        text = codex.panel_text(self.screen_of(raw))
        self.assertEqual(text, fixture("codex-0.159.2-model-week"))
        self.assertEqual(codex.parse_panel(text)["seven_day_pct"], 23)
        # The same bytes read in the order painted lose the plan's week.
        self.assertIsNone(
            codex.parse_panel(ptyreader.clean(raw))["seven_day_pct"])

    def test_a_client_that_prints_inline_is_read_from_what_it_printed(self):
        # A carriage return writes over the line on a terminal, while the text
        # in paint order keeps both, so here the two readings differ.
        screen = self.screen_of(
            b"  Weekly limit: [###] 87% left (resets 18:00 on 8 Sep)\rX")
        self.assertNotEqual(screen.text, screen.display[0])
        self.assertEqual(codex.panel_text(screen), screen.text)

    def test_the_name_the_panel_printed_is_kept_beside_the_plan(self):
        data = codex.parse_panel(fixture("codex-0.160.0-model-week"))
        self.assertEqual(data["plan"], "Pro Lite")
        self.assertEqual(data["extra"]["plan_printed"], "Pro 100")
        self.assertEqual(codex.parse_panel(fixture(CODEX_FIXTURE))
                         ["extra"]["plan_printed"], "Business Premium")

    def test_the_plan_week_beside_a_model_week(self):
        for name, resets in (("codex-0.159.2-model-week", "17:28 on 8 Oct"),
                             ("codex-0.160.0-model-week", "17:21 on 8 Oct")):
            with self.subTest(name):
                data = codex.parse_panel(fixture(name))
                self.assertEqual(data["seven_day_pct"], 23)
                self.assertEqual(data["seven_day_resets"], "13:31 on 4 Oct")
                self.assertIsNone(data["five_hour_pct"])
                self.assertEqual(data["extra"]["model_weeks"],
                                 {"Luna Reserve": {"pct": 0, "resets": resets}})

    def test_one_weekly_window(self):
        data = codex.parse_panel(fixture("codex-0.159.2"))
        self.assertEqual(data["seven_day_pct"], 11)
        self.assertEqual(data["seven_day_resets"], "02:57 on 4 Oct")
        self.assertNotIn("model_weeks", data["extra"])

    def test_the_plan_printed_alone_is_not_taken_for_the_account(self):
        # The panel says "Account: Pro" on 0.159.2 and "Account: Pro 100" on
        # 0.160.0, for the same Pro Lite subscription, and names no address.
        for name, version in (("codex-0.159.2", "0.159.2"),
                              ("codex-0.159.2-model-week", "0.159.2"),
                              ("codex-0.160.0-model-week", "0.160.0")):
            with self.subTest(name):
                data = codex.parse_panel(fixture(name))
                self.assertIsNone(data["account"])
                self.assertEqual(data["plan"], "Pro Lite")
                self.assertEqual(data["client_version"], version)


class CodexAccountLine(unittest.TestCase):
    """What Codex prints after "Account:", per codex-rs/tui/src/status/card.rs:
    the address and the plan, the address alone, the plan alone, "ChatGPT"
    when it knows neither, or a sentence for an API key. Running behind its
    background server, as 0.159.2 and 0.160.0 do here, it prints the plan
    alone. Only something shaped like an address is ever the account."""

    def read(self, value, version="0.160.0"):
        data = codex.parse_panel("  >_ OpenAI Codex (v%s)\n  Account:   %s\n"
                                 % (version, value))
        return data["account"], data["plan"]

    def test_an_address_and_a_plan(self):
        self.assertEqual(self.read("user@example.com (Pro 100)"),
                         ("user@example.com", "Pro Lite"))

    def test_an_address_alone(self):
        self.assertEqual(self.read("user@example.com"), ("user@example.com", None))

    def test_a_plan_alone(self):
        self.assertEqual(self.read("Pro", "0.159.2"), (None, "Pro Lite"))
        self.assertEqual(self.read("Business Premium"), (None, "Business Premium"))

    def test_neither(self):
        self.assertEqual(self.read("ChatGPT"), (None, None))
        self.assertEqual(self.read("chatgpt"), (None, None))
        self.assertEqual(
            self.read("API key configured (run codex login to use ChatGPT)"),
            (None, None))

    def test_words_after_an_address_never_become_the_plan(self):
        self.assertEqual(self.read("user@example.com · Team Space"),
                         ("user@example.com", None))

    def test_an_address_and_a_bare_pro_either_side_of_the_rename(self):
        self.assertEqual(self.read("user@example.com (Pro)", "0.157.1"),
                         ("user@example.com", "Pro"))
        self.assertEqual(self.read("user@example.com (Pro)", "0.159.2"),
                         ("user@example.com", "Pro Lite"))


class CodexFolderTrust(unittest.TestCase):
    """Codex 0.159.2 and 0.160.0 ask about a folder in new words, and only in a
    git folder: an empty untrusted directory under /tmp opens straight to the
    composer. Captured on 1 October 2026 in a new git folder under /tmp, with
    no key pressed. The composer is painted first and the dialog replaces it a
    moment later, so the composer's words are already in the painted text."""

    def test_the_folder_dialog_is_recognised(self):
        for version in ("0.159.2", "0.160.0"):
            with self.subTest(version):
                self.assertIsNotNone(re.search(
                    codex.TRUST, fixture("codex-%s-trust" % version), re.I))

    def test_no_panel_is_taken_for_it(self):
        for name in (CODEX_FIXTURE,) + CODEX_SCREENS:
            with self.subTest(name):
                self.assertIsNone(re.search(codex.TRUST, fixture(name), re.I))


class ScriptedScreen(ptyreader.Screen):
    """A client played from a script rather than run on a PTY. Each pump paints
    the next waiting run of bytes, and a key the reader sends queues the reply
    scripted for it. Every key is kept, so a test can say what was typed."""

    def __init__(self, paints, replies=None):
        super().__init__(["codex"])
        self.waiting, self.replies, self.sent = list(paints), dict(replies or {}), []

    def start(self):
        return self

    def pump(self, seconds=0.4):
        if self.waiting:
            self.chunks.append((time.time(), self.waiting.pop(0)))
        else:
            time.sleep(min(seconds, 0.05))

    def send(self, data):
        self.sent.append(data)
        self.waiting += self.replies.get(data, [])

    def close(self):
        self.closed = True


class CodexReaderKeystrokes(unittest.TestCase):
    """What read_screen types, driven against a scripted screen. Each of these
    takes a few seconds, because the reader waits for the screen to go quiet
    before it types anything."""

    # The input line as Codex 0.159.2 and 0.160.0 draw it: a "›" in the first
    # column and the placeholder from the third.
    COMPOSER = "\x1b[?1049h\x1b[52;1H› Ask Codex to do anything".encode()

    def drive(self, screen, budget=15):
        real = codex.ptyreader.Screen
        codex.ptyreader.Screen = lambda *args, **kwargs: screen
        try:
            return codex.read_screen("/tmp", budget)
        finally:
            codex.ptyreader.Screen = real

    def test_the_folder_dialog_painted_after_the_composer_gets_no_key(self):
        raw = raw_fixture("codex-0.160.0-trust")
        cut = raw.rindex(b"\x1b", 0, raw.index(b"Folder access"))
        screen = ScriptedScreen([raw[:cut], raw[cut:]])
        outcome, _ = self.drive(screen)
        self.assertEqual(outcome, "trust")
        self.assertEqual(screen.sent, [])

    def test_a_dialog_whose_words_were_painted_in_pieces_gets_no_key(self):
        # The "o" was already on screen, so the client sent "D", a jump, and
        # the rest: the painted text never says "Do you trust". The input
        # line stays in view, so only reading the screen catches the dialog.
        old = b"\x1b[3;2Ho"
        dialog = (b"\x1b[3;1HD\x1b[3;3H you trust the contents of this "
                  b"directory?")
        screen = ScriptedScreen([self.COMPOSER + old, dialog])
        outcome, _ = self.drive(screen)
        self.assertEqual(outcome, "trust")
        self.assertEqual(screen.sent, [])

    def test_a_dialog_in_place_of_the_command_list_gets_no_escape(self):
        dialog = (b"\x1b[2J\x1b[2;3HFolder access\x1b[4;3HTrust this folder? "
                  b"Codex can read, edit, and run files here.")
        screen = ScriptedScreen([self.COMPOSER], {b"/status": [dialog]})
        outcome, _ = self.drive(screen, budget=6)
        self.assertEqual(outcome, "trust")
        self.assertEqual(screen.sent, [b"/status"])

    def test_a_screen_it_does_not_recognise_gets_no_key(self):
        unknown = ("\x1b[2J\x1b[5;3HPick a workspace\x1b[7;1H› 1. Personal"
                   "\x1b[8;3H2. Team").encode()
        screen = ScriptedScreen([self.COMPOSER, unknown])
        outcome, _ = self.drive(screen)
        self.assertEqual(outcome, "unknown dialog")
        self.assertEqual(screen.sent, [])

    def test_a_dialog_over_the_input_line_gets_no_key_when_history_shows_status(self):
        # A /status sent earlier stays in the history as a bare "/status". Only
        # the input line itself, marked with "›", lets a key through.
        history = b"\x1b[9;1H/status"
        dialog = ("\x1b[50;1H\x1b[J\x1b[50;3HSelect a workspace"
                  "\x1b[51;1H› 1. Personal\x1b[52;3H2. Team").encode()
        screen = ScriptedScreen([self.COMPOSER + history, dialog])
        outcome, _ = self.drive(screen)
        self.assertEqual(outcome, "unknown dialog")
        self.assertEqual(screen.sent, [])

    def test_a_panel_is_read_from_one_ask_when_its_label_lost_a_letter(self):
        # Row 10 already holds "We", so the client sends the label as "W", a
        # jump, then "ekly limit:", and the painted text never says "Weekly".
        # The typed command and the list beneath it are drawn as both versions
        # draw them: "› /status" on the input line, "› /status  show ..." below.
        opening = ("\x1b[?1049h\x1b[10;1H  We\x1b[52;1H› Ask Codex to do "
                   "anything").encode()
        menu = ("\x1b[52;3H/status\x1b[K\x1b[54;1H› /status      show current "
                "session configuration and token usage").encode()
        panel = (b"\x1b[54;1H\x1b[K\x1b[2;3H>_ OpenAI Codex (v0.160.0)"
                 b"\x1b[6;3HAccount:   Pro 100\x1b[10;1H  W\x1b[10;5Hekly limit: "
                 b"[###]\x1b[10;23H77%\x1b[10;27Hleft (resets 13:31 on 4 Oct)"
                 b"\x1b[52;3HAsk Codex to do anything")
        screen = ScriptedScreen([opening], {b"/status": [menu], b"\r": [panel]})
        outcome, text = self.drive(screen)
        self.assertEqual(outcome, "panel")
        self.assertEqual(screen.sent, [b"/status", b"\r"])
        self.assertEqual(codex.parse_panel(text)["seven_day_pct"], 23)


class CodexPlanNames(unittest.TestCase):
    """Codex renamed its Pro plans twice, so one subscription prints under a
    different name depending on the client: Pro Lite is "Pro Lite" up to
    0.157, "Pro" from 0.158.0 to 0.159.3 and "Pro 100" from 0.160.0, read from
    codex-rs/tui/src/status/helpers.rs and subscription.rs at each release.
    The drift check compares the plan with the one pinned at setup, so a name
    taken as printed reports a sign-in change that never happened."""

    def test_each_plan_keeps_the_first_name_codex_gave_it(self):
        cases = [
            ("Pro Lite", "0.153.0", "Pro Lite"),
            ("Pro", "0.157.1", "Pro"),
            ("Pro", "0.158.0", "Pro Lite"),
            ("Pro", "0.159.2", "Pro Lite"),
            ("Pro (More)", "0.159.2", "Pro"),
            ("Pro (Max)", "0.158.0", "Pro (Max)"),
            ("Pro 100", "0.160.0", "Pro Lite"),
            ("Pro 200", "0.160.0", "Pro"),
            ("Pro 500", "0.160.0", "Pro (Max)"),
            # Only 0.158.0 to 0.159.3 ever printed Pro Lite as a bare "Pro".
            ("Pro", "0.160.0", "Pro"),
            ("Business Premium", "0.160.0", "Business Premium"),
            ("Plus", "0.159.2", "Plus"),
        ]
        for printed, version, wanted in cases:
            with self.subTest(printed=printed, version=version):
                self.assertEqual(codex.plan_name(printed, version), wanted)

    def test_a_bare_pro_from_an_unknown_client_is_not_guessed(self):
        self.assertIsNone(codex.plan_name("Pro", ""))


class CodexReaderEnvironment(unittest.TestCase):
    """Which sign-in the reader drives is decided by the environment it builds."""

    def setUp(self):
        self.previous = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = "/inherited/from/the/shell"

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.previous

    def test_the_default_home_drops_an_inherited_variable_everywhere(self):
        env, env_set, env_drop = codex.role_env(None)
        self.assertNotIn("CODEX_HOME", env)
        self.assertEqual(env_set, {})
        self.assertIn("CODEX_HOME", env_drop)
        self.assertIn("OPENAI_API_KEY", env_drop)

    def test_a_home_of_its_own_is_set_for_both_the_check_and_the_panel(self):
        env, env_set, env_drop = codex.role_env("/tmp/codex-personal")
        self.assertEqual(env["CODEX_HOME"], "/tmp/codex-personal")
        self.assertEqual(env_set, {"CODEX_HOME": "/tmp/codex-personal"})
        self.assertNotIn("CODEX_HOME", env_drop)
        self.assertNotIn("OPENAI_API_KEY", env)


if __name__ == "__main__":
    unittest.main()
