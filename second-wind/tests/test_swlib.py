#!/usr/bin/env python3
"""Tests for the shared library and the parts of setup that decide things.

Every test points SW_HOME at a temporary directory, so nothing here reads or
writes the real ~/.second-wind.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts")
sys.path.insert(0, SCRIPTS)
import swlib  # noqa: E402
import setup as sw_setup  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
NOW = 1_700_000_000

# The matchers that stop a hook firing on every unrelated event. Written out
# here rather than imported, so a change to the source has to be deliberate.
HOOK_MATCHERS = {
    "StopFailure": "rate_limit",
    "Notification": "quota_auto_resume_fired|quota_auto_resume_stale|"
                    "quota_auto_resume_disabled",
}


def read_text(path):
    with open(path) as handle:
        return handle.read()


def read_json(path):
    return json.loads(read_text(path))


def npm_install(prefix, package, command):
    """A client laid out the way npm -g leaves one: the package under
    lib/node_modules, a relative link to it in bin, and npm beside the link.
    Returns the link, which is what PATH finds."""
    entry = os.path.join(prefix, "lib", "node_modules", *package.split("/"),
                         "bin", command)
    folder = os.path.join(prefix, "bin")
    os.makedirs(os.path.dirname(entry))
    os.makedirs(folder, exist_ok=True)
    for path in (entry, os.path.join(folder, "npm")):
        with open(path, "w") as handle:
            handle.write("#!/bin/sh\n")
        os.chmod(path, 0o755)
    link = os.path.join(folder, command)
    os.symlink(os.path.relpath(entry, folder), link)
    return link


def brew_install(prefix, token, command):
    """A client laid out the way Homebrew leaves a cask: the build under
    Caskroom, a link to it in bin, and Homebrew's own npm in the same bin."""
    entry = os.path.join(prefix, "Caskroom", token, "1.0.0", command)
    folder = os.path.join(prefix, "bin")
    os.makedirs(os.path.dirname(entry))
    os.makedirs(folder, exist_ok=True)
    for path in (entry, os.path.join(folder, "npm")):
        with open(path, "w") as handle:
            handle.write("#!/bin/sh\n")
        os.chmod(path, 0o755)
    link = os.path.join(folder, command)
    os.symlink(entry, link)
    return link


def card_account(label, now=NOW):
    """An account's figures line and its notes line, or None when it has no
    notes. The notes line is the next entry, indented rather than labelled."""
    card = swlib.brief_card(now=now)
    i = next(i for i, line in enumerate(card) if line.startswith(label))
    following = card[i + 1] if i + 1 < len(card) else ""
    return card[i], following.strip() if following.startswith(" ") else None


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="sw-test-")
        self.previous = os.environ.get("SW_HOME")
        os.environ["SW_HOME"] = self.home
        self.previous_dir = os.environ.get("CLAUDE_CONFIG_DIR")

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("SW_HOME", None)
        else:
            os.environ["SW_HOME"] = self.previous
        if self.previous_dir is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.previous_dir
        shutil.rmtree(self.home, ignore_errors=True)

    def write_config(self, **overrides):
        cfg = {
            "version": 4,
            "level": "relief",
            "primary": {"config_dir": "~/.claude", "label": "primary"},
            "secondary": {"enabled": False},
            "reader": {"enabled": False},
            "codex": {"enabled": False},
            "grok": {"enabled": False},
            "cursor": {"enabled": False},
            "thresholds": {"five_hour_pct": 90, "seven_day_pct": 80},
            "refresh": {"interval_minutes": 15},
        }
        cfg.update(overrides)
        swlib.write_json_atomic(swlib.config_path(), cfg)
        return cfg

    def write_usage(self, role, age_seconds=0, **fields):
        data = {"role": role, "cached_at": NOW - age_seconds}
        data.update(fields)
        path = swlib.usage_path(role)
        swlib.write_json_atomic(path, data)
        os.utime(path, (NOW - age_seconds, NOW - age_seconds))
        return path

    def write_usage_live(self, role, age_seconds=0, **fields):
        """A reading timed against the real clock, for code that calls
        freshness() without a fixed now."""
        drift = NOW - int(time.time())
        return self.write_usage(role, age_seconds=age_seconds + drift, **fields)

    def write_status_live(self, role, message):
        return self.write_status(role, message, when=time.time())

    def write_status(self, role, message, when=NOW):
        path = swlib.status_path(role)
        swlib.write_text_atomic(path, message + "\n")
        os.utime(path, (when, when))
        return path


class Freshness(Base):
    def setUp(self):
        super().setUp()
        self.write_config()

    def state(self, age):
        self.write_usage("primary", age_seconds=age)
        return swlib.freshness("primary", now=NOW)

    def test_no_reading_is_none(self):
        self.assertEqual(swlib.freshness("primary", now=NOW), "none")

    def test_a_reading_without_a_timestamp_is_none(self):
        swlib.write_json_atomic(swlib.usage_path("primary"), {"role": "primary"})
        self.assertEqual(swlib.freshness("primary", now=NOW), "none")

    def test_boundaries(self):
        self.assertEqual(self.state(0), "fresh")
        self.assertEqual(self.state(899), "fresh")
        self.assertEqual(self.state(900), "stale")
        self.assertEqual(self.state(3599), "stale")
        self.assertEqual(self.state(3600), "dead")
        self.assertEqual(self.state(86400), "dead")

    def test_interval_moves_the_fresh_boundary(self):
        self.write_config(refresh={"interval_minutes": 60})
        self.assertEqual(self.state(1800), "fresh")
        self.assertEqual(self.state(3599), "fresh")
        self.assertEqual(self.state(3600), "dead")

    def test_small_clock_skew_is_tolerated(self):
        self.assertEqual(self.state(-30), "fresh")

    def test_a_wild_future_timestamp_is_not_a_reading(self):
        self.assertEqual(self.state(-600), "none")

    def test_short_age(self):
        self.assertEqual(swlib.short_age(None), "unknown")
        self.assertEqual(swlib.short_age(45), "45s")
        self.assertEqual(swlib.short_age(180), "3m")
        self.assertEqual(swlib.short_age(7200), "2h")
        self.assertEqual(swlib.short_age(3 * 86400), "3d")


class Headroom(Base):
    def test_claude_uses_the_worst_window_it_was_given(self):
        self.assertEqual(swlib.headroom("primary",
                                        {"five_hour_pct": 20, "seven_day_pct": 40}), 60)
        self.assertEqual(swlib.headroom("secondary",
                                        {"five_hour_pct": 95, "seven_day_pct": 10}), 5)

    def test_a_window_the_panel_did_not_print_is_skipped(self):
        # the Codex panel on some plans prints the weekly limit and no 5-hour
        # line, and treating that as unreadable made Codex unroutable
        self.assertEqual(swlib.headroom("codex", {"five_hour_pct": None,
                                                  "seven_day_pct": 13}), 87)
        self.assertEqual(swlib.headroom("codex", {"seven_day_pct": 13}), 87)
        self.assertEqual(swlib.headroom("primary", {"five_hour_pct": 20,
                                                    "seven_day_pct": None}), 80)
        self.assertEqual(swlib.headroom("primary", {"seven_day_pct": 20}), 80)

    def test_neither_window_is_unknown(self):
        for role in ("primary", "secondary", "codex"):
            self.assertIsNone(swlib.headroom(role, {"five_hour_pct": None,
                                                    "seven_day_pct": None}), role)
            self.assertIsNone(swlib.headroom(role, {"plan": "max"}), role)

    def test_codex_still_uses_the_worst_of_two_windows(self):
        self.assertEqual(swlib.headroom("codex",
                                        {"five_hour_pct": 5, "seven_day_pct": 13}), 87)
        self.assertEqual(swlib.headroom("codex",
                                        {"five_hour_pct": 40, "seven_day_pct": 13}), 60)

    def test_grok_is_weekly_only(self):
        self.assertEqual(swlib.headroom("grok", {"seven_day_pct": 13,
                                                 "five_hour_pct": None}), 87)
        self.assertIsNone(swlib.headroom("grok", {"five_hour_pct": 10}))

    def test_cursor_takes_the_worst_pool(self):
        usage = {"extra": {"included_pct": 10, "auto_pct": 50, "api_pct": 0}}
        self.assertEqual(swlib.headroom("cursor", usage), 50)
        legacy = {"included_pct": 1, "auto_pct": 1, "api_pct": 0}
        self.assertEqual(swlib.headroom("cursor", legacy), 99)
        self.assertIsNone(swlib.headroom("cursor", {"on_demand": "available"}))

    def test_out_of_range_figures_are_clamped(self):
        self.assertEqual(swlib.headroom("primary", {"five_hour_pct": 120,
                                                    "seven_day_pct": 0}), 0)
        self.assertEqual(swlib.headroom("grok", {"seven_day_pct": -5}), 100)

    def test_no_reading_has_no_headroom(self):
        self.assertIsNone(swlib.headroom("primary", {}))
        self.assertIsNone(swlib.headroom("nonsense", {"seven_day_pct": 1}))


class Status(Base):
    def setUp(self):
        super().setUp()
        self.write_config()

    def test_a_status_older_than_the_reading_is_dropped(self):
        self.write_status("primary", "FAILED: the panel never appeared", when=NOW - 600)
        self.write_usage("primary", age_seconds=0)
        self.assertEqual(swlib.status("primary"), "")
        self.assertEqual(swlib.status_kind("primary"), "none")

    def test_a_status_newer_than_the_reading_wins(self):
        self.write_usage("primary", age_seconds=600)
        self.write_status("primary", "LOGIN EXPIRED: sign in again", when=NOW)
        self.assertEqual(swlib.status_kind("primary"), "login")

    def test_every_kind_is_recognised(self):
        pairs = {
            "OK": "ok",
            "OK: prompt-free usage panel read.": "ok",
            "LOGIN EXPIRED: sign in to primary": "login",
            "NO CLI SIGN-IN: ~/.claude-usage has no Claude Code sign-in of "
            "its own. Run: CLAUDE_CONFIG_DIR=~/.claude-usage claude auth login":
                "nologin",
            "TRUST PROMPT: codex asked to trust the directory": "trust",
            "PARSER MISMATCH: client 0.152.1, expected labels not found": "parser",
            "FAILED: the panel never appeared": "failed",
            "": "none",
        }
        for message, kind in pairs.items():
            self.assertEqual(swlib.status_kind(message=message), kind, message)

    def test_words_are_short_enough_for_a_column(self):
        for kind in swlib.STATUS_KINDS:
            words = swlib.status_words(kind)
            self.assertTrue(words)
            self.assertLessEqual(len(words.split()), 3, kind)


class Brief(Base):
    def config(self, **overrides):
        base = {
            "primary": {"config_dir": "~/.claude", "label": "Work Claude"},
            "secondary": {"enabled": False},
        }
        base.update(overrides)
        return self.write_config(**base)

    def line(self, cfg=None):
        return swlib.brief_lines(cfg=cfg or swlib.load_config(), now=NOW)[0]

    def test_fresh_reading(self):
        self.config()
        self.write_usage("primary", age_seconds=180, five_hour_pct=2, seven_day_pct=16)
        self.assertEqual(self.line(), "Work Claude: 5h 2%, 7d 16% (3m ago)")

    def test_the_label_falls_back_to_the_role(self):
        self.write_config(primary={"config_dir": "~/.claude"})
        self.write_usage("primary", age_seconds=60, five_hour_pct=2, seven_day_pct=16)
        self.assertEqual(self.line(), "primary: 5h 2%, 7d 16% (60s ago)")

    def test_stale_reading_still_shows_its_age(self):
        self.config()
        self.write_usage("primary", age_seconds=1800, five_hour_pct=2, seven_day_pct=16)
        self.assertEqual(self.line(), "Work Claude: 5h 2%, 7d 16% (30m ago)")

    def test_dead_reading_says_so(self):
        self.config()
        self.write_usage("primary", age_seconds=7200, five_hour_pct=2, seven_day_pct=16)
        self.assertEqual(self.line(), "Work Claude: no current reading, refreshing")

    def test_missing_reading_says_so(self):
        self.config()
        self.assertEqual(self.line(), "Work Claude: no current reading, refreshing")

    def test_a_blocking_status_is_stated_after_the_figures(self):
        self.config()
        self.write_usage("primary", age_seconds=120, five_hour_pct=2, seven_day_pct=16)
        self.write_status("primary", "LOGIN EXPIRED: sign in again", when=NOW)
        self.assertEqual(self.line(),
                         "Work Claude: 5h 2%, 7d 16% (2m ago), login expired, "
                         "sign in again")

    def test_a_blocking_status_replaces_a_dead_reading(self):
        self.config()
        self.write_usage("primary", age_seconds=7200, five_hour_pct=2, seven_day_pct=16)
        self.write_status("primary", "TRUST PROMPT: the reader was asked to trust",
                          when=NOW)
        self.assertEqual(self.line(),
                         "Work Claude: a trust prompt blocked the reader")

    def test_each_status_kind_has_its_own_wording(self):
        self.config()
        cases = {
            "PARSER MISMATCH: client 2.1.251, expected labels not found":
                "Work Claude: the usage panel labels moved, the parser needs an update",
            "NO CLI SIGN-IN: ~/.claude-usage has no Claude Code sign-in":
                "Work Claude: no Claude Code sign-in for its reader profile",
            "FAILED: the panel never appeared":
                "Work Claude: the last refresh failed",
            "OK": "Work Claude: no current reading, refreshing",
        }
        for message, expected in cases.items():
            self.write_usage("primary", age_seconds=7200)
            self.write_status("primary", message, when=NOW)
            self.assertEqual(self.line(), expected, message)

    def test_one_line_per_enabled_account(self):
        self.write_config(
            primary={"config_dir": "~/.claude", "label": "Work Claude"},
            secondary={"enabled": True, "config_dir": "~/.claude-secondary",
                       "label": "Personal Claude"},
            grok={"enabled": True},
            cursor={"enabled": True},
        )
        self.write_usage("primary", age_seconds=60, five_hour_pct=2, seven_day_pct=16)
        self.write_usage("secondary", age_seconds=60, five_hour_pct=0, seven_day_pct=13)
        self.write_usage("grok", age_seconds=60, seven_day_pct=4, five_hour_pct=None)
        self.write_usage("cursor", age_seconds=60,
                         extra={"included_pct": 1, "auto_pct": 2, "api_pct": 0})
        lines = swlib.brief_lines(now=NOW)
        self.assertEqual(lines, [
            "Work Claude: 5h 2%, 7d 16% (60s ago)",
            "Personal Claude: 5h 0%, 7d 13% (60s ago)",
            "grok: 7d 4% (60s ago)",
            "cursor: included 1%, auto 2%, api 0% (60s ago)",
        ])

    def test_a_sign_in_that_cannot_report_usage_says_that_instead(self):
        self.write_config(
            primary={"config_dir": "~/.claude", "label": "Work Claude"},
            cursor={"enabled": True},
            refresh={"interval_minutes": 15, "cursor": False},
        )
        self.write_usage("primary", age_seconds=60, five_hour_pct=2, seven_day_pct=16)
        self.assertEqual(swlib.brief_lines(now=NOW)[1],
                         "cursor: no usage reading available for this sign-in")


class UsageBar(Base):
    """The bar is a function of the number and nothing else, and three of its
    properties are honesty rules rather than taste."""

    def test_width_is_fixed_whatever_goes_in(self):
        for pct in (None, 0, 0.4, 1, 33, 50, 97.6, 99, 100, 140, -5):
            self.assertEqual(len(swlib.usage_bar(pct)), swlib.BAR_CELLS, pct)

    def test_nothing_spent_draws_empty_and_everything_draws_full(self):
        self.assertEqual(swlib.usage_bar(0), swlib.BAR_EMPTY * swlib.BAR_CELLS)
        self.assertEqual(swlib.usage_bar(100), swlib.BAR_FULL * swlib.BAR_CELLS)

    def test_below_a_hundred_is_never_a_full_bar(self):
        # The reason the eighth blocks exist. 99% of a weekly limit rounds to a
        # full bar in whole cells, and reading it as spent is the mistake the
        # panel is meant to prevent.
        for pct in (90, 95, 98, 99, 99.9):
            self.assertNotEqual(swlib.usage_bar(pct),
                                swlib.BAR_FULL * swlib.BAR_CELLS, pct)

    def test_something_spent_is_never_an_empty_bar(self):
        for pct in (0.1, 1, 3):
            self.assertNotEqual(swlib.usage_bar(pct),
                                swlib.BAR_EMPTY * swlib.BAR_CELLS, pct)

    def test_a_window_the_client_never_reported_is_not_drawn_as_zero(self):
        self.assertNotEqual(swlib.usage_bar(None), swlib.usage_bar(0))
        self.assertEqual(swlib.usage_bar(None), swlib.BAR_ABSENT * swlib.BAR_CELLS)

    def test_the_same_reading_always_draws_the_same_picture(self):
        self.assertEqual(swlib.usage_bar(48), swlib.usage_bar(48.0))


class BriefCard(Base):
    def card(self):
        self.write_config(
            primary={"config_dir": "~/.claude", "label": "Work Claude"},
            secondary={"enabled": True, "config_dir": "~/.claude-secondary",
                       "label": "Personal Claude"},
            codex={"enabled": True, "label": "Codex"},
            cursor={"enabled": True, "label": "Cursor"},
        )
        self.write_usage("primary", age_seconds=60, five_hour_pct=2, seven_day_pct=16)
        self.write_usage("secondary", age_seconds=60, five_hour_pct=0, seven_day_pct=13)
        self.write_usage("codex", age_seconds=60, seven_day_pct=98, five_hour_pct=None)
        self.write_usage("cursor", age_seconds=60,
                         extra={"included_pct": 1, "auto_pct": 2, "api_pct": 0})
        return swlib.brief_card(now=NOW)

    @staticmethod
    def bar_offsets(row):
        """Where each bar begins in a row, by its glyphs."""
        offsets, previous = [], -2
        for i, char in enumerate(row):
            if char in swlib.BAR_CHARS:
                if i != previous + 1:
                    offsets.append(i)
                previous = i
        return offsets

    def test_every_row_starts_in_the_same_column(self):
        # The label field is as wide as the longest label, so whatever follows
        # it begins in one column on every row, header included.
        width = len("Personal Claude")
        for row in self.card()[:-1]:
            self.assertEqual(row[width:width + 2], "  ", row)
            self.assertNotEqual(row[width + 2:width + 3], " ", row)

    def test_the_windowed_rows_put_both_bars_in_the_same_two_columns(self):
        windowed = [row for row in self.card()[1:-1] if self.bar_offsets(row)]
        self.assertEqual(len(windowed), 3)
        self.assertEqual(len({tuple(self.bar_offsets(row)) for row in windowed}), 1,
                          "both bars begin at the same offset on every row")

    def test_a_missing_window_reads_as_not_available_not_as_zero(self):
        codex = next(row for row in self.card() if row.startswith("Codex"))
        self.assertIn(swlib.BAR_ABSENT * swlib.BAR_CELLS, codex)
        self.assertIn("n/a", codex)

    def test_cursor_pools_are_not_filed_under_the_weekly_column(self):
        cursor = next(row for row in self.card() if row.startswith("Cursor"))
        self.assertIn("monthly pools: included 1%, auto 2%, api 0%", cursor)
        self.assertNotIn(swlib.BAR_EMPTY, cursor)

    def test_a_known_reset_is_a_trailing_note_not_a_third_column(self):
        self.write_config(primary={"config_dir": "~/.claude", "label": "Work Claude"})
        self.write_usage("primary", age_seconds=60, five_hour_pct=2, seven_day_pct=16,
                         seven_day_resets="Sep 21 at 3am (Africa/Johannesburg)")
        row, notes = card_account("Work Claude")
        # the timezone parenthetical is trimmed and the time/date reordered;
        # the note sits under the bars, not beside them
        self.assertTrue(row.endswith("16%"), row)
        self.assertNotIn("resets", row)
        self.assertEqual(notes, "resets 03:00 Sep 21")

    def test_no_reset_reported_adds_nothing(self):
        self.write_config(primary={"config_dir": "~/.claude", "label": "Work Claude"})
        self.write_usage("primary", age_seconds=60, five_hour_pct=2, seven_day_pct=16)
        row, notes = card_account("Work Claude")
        self.assertNotIn("resets", row)
        self.assertIsNone(notes)

    def test_cursors_reset_is_its_own_pools_reset_not_a_weekly_one(self):
        self.card()
        cursor, notes = card_account("Cursor")
        self.assertNotIn("resets", cursor)  # the fixture set no cursor reset
        self.assertIsNone(notes)
        self.write_usage("cursor", age_seconds=60,
                         extra={"included_pct": 1, "auto_pct": 2, "api_pct": 0,
                                "resets": "Sep 30"})
        cursor, notes = card_account("Cursor")
        self.assertTrue(cursor.endswith("api 0%"), cursor)
        self.assertEqual(notes, "resets Sep 30")

    def test_the_footnote_reports_the_oldest_reading(self):
        self.assertEqual(self.card()[-1], "Readings 60s old.")

    def test_a_span_of_ages_is_reported_as_a_span(self):
        self.write_config(
            primary={"config_dir": "~/.claude", "label": "Work Claude"},
            codex={"enabled": True, "label": "Codex"},
        )
        self.write_usage("primary", age_seconds=60, five_hour_pct=2, seven_day_pct=16)
        self.write_usage("codex", age_seconds=600, seven_day_pct=98, five_hour_pct=None)
        self.assertEqual(swlib.brief_card(now=NOW)[-1], "Readings 60s to 10m old.")

    def test_a_dead_reading_carries_the_reason_instead_of_a_bar(self):
        self.write_config(
            primary={"config_dir": "~/.claude", "label": "Work Claude"},
            codex={"enabled": True, "label": "Codex"},
        )
        self.write_usage("primary", age_seconds=60, five_hour_pct=2, seven_day_pct=16)
        self.write_usage("codex", age_seconds=99999, seven_day_pct=98, five_hour_pct=None)
        codex = next(row for row in swlib.brief_card(now=NOW)
                     if row.startswith("Codex"))
        self.assertNotIn(swlib.BAR_FULL, codex)
        self.assertNotIn("98", codex)

    def test_a_model_week_goes_on_the_row_and_never_in_a_column(self):
        """Some plans carry a weekly window named after one model, and it can
        be the fuller of the two. It belongs on the row, not in the weekly
        column, where it would be read as the weekly figure."""
        self.write_config(primary={"config_dir": "~/.claude",
                                   "label": "Work Claude"})
        self.write_usage("primary", age_seconds=60, five_hour_pct=35,
                         seven_day_pct=48,
                         extra={"model_weeks": {"Fable": {"pct": 81}}})
        row, notes = card_account("Work Claude")
        self.assertIn(" 35%", row)
        self.assertIn(" 48%", row)
        # the note sits under both bars, not inside them
        self.assertNotIn("Fable", row)
        self.assertEqual(notes, "Fable week 81%")

    def test_a_row_with_no_notes_is_one_line(self):
        self.write_config(primary={"config_dir": "~/.claude", "label": "Work Claude"},
                          codex={"enabled": True, "label": "Codex"})
        self.write_usage("primary", age_seconds=60, five_hour_pct=2, seven_day_pct=16)
        self.write_usage("codex", age_seconds=60, seven_day_pct=98, five_hour_pct=None,
                         seven_day_resets="16:17 on 19 Sep")
        card = swlib.brief_card(now=NOW)
        self.assertEqual(len(card), 5, card)  # header, Work Claude, Codex + notes, footnote
        self.assertTrue(card[2].startswith("Codex"), card)
        self.assertFalse(any(line.strip() == "" for line in card), card)

    def test_several_notes_share_one_line_joined_by_a_middle_dot(self):
        self.write_config(primary={"config_dir": "~/.claude", "label": "Work Claude"})
        self.write_usage("primary", age_seconds=60, five_hour_pct=35, seven_day_pct=48,
                         seven_day_resets="Sep 21 at 3am",
                         extra={"model_weeks": {"Fable": {"pct": 81}}})
        card = swlib.brief_card(now=NOW)
        row, notes = card_account("Work Claude")
        self.assertEqual(notes, "resets 03:00 Sep 21 · Fable week 81%")
        self.assertNotIn("  ", notes)
        # indented to the label column, so the notes start where the bars do
        self.assertEqual(card[2].index("resets"), self.bar_offsets(row)[0])

    def test_the_fullest_model_week_is_named_first(self):
        note = swlib.model_week_note(
            {"extra": {"model_weeks": {"Quiet": {"pct": 12},
                                       "Loud": {"pct": 91}}}})
        self.assertEqual(note, "Loud week 91%, Quiet week 12%")

    def test_no_model_week_adds_nothing_to_the_row(self):
        self.assertEqual(swlib.model_week_note({"extra": {}}), "")
        self.assertEqual(swlib.model_week_note({}), "")
        self.assertEqual(
            swlib.model_week_note({"extra": {"model_weeks":
                                             {"Fable": {"pct": None}}}}), "")

    def test_an_untouched_model_week_is_left_off(self):
        # A window nobody has spent anything from is not a reason to route
        # away from that model, so it is not worth a line in the panel.
        self.assertEqual(
            swlib.model_week_note(
                {"extra": {"model_weeks": {"GPT-5.3-Codex-Spark": {"pct": 0}}}}), "")
        # Untouched models are dropped; a spent one among them still shows.
        self.assertEqual(
            swlib.model_week_note(
                {"extra": {"model_weeks": {"gpt-reserve": {"pct": 0},
                                           "GPT-5.3-Codex-Spark": {"pct": 12}}}}),
            "GPT-5.3-Codex-Spark week 12%")

    def test_no_accounts_means_no_card_rather_than_a_bare_header(self):
        self.assertEqual(swlib.brief_card(), [])


class ResetNote(unittest.TestCase):
    """The trailing note naming when the row's window resets. No disk, no
    config: reset_note is a pure function of the usage a reader already
    wrote."""

    def test_weekly_reset_for_an_ordinary_role(self):
        self.assertEqual(
            swlib.reset_note("primary", {"seven_day_resets": "16:17 on 19 Sep"}),
            "resets 16:17 Sep 19")

    def test_claudes_timezone_parenthetical_is_trimmed(self):
        self.assertEqual(
            swlib.reset_note(
                "primary",
                {"seven_day_resets": "Sep 21 at 3am (Africa/Johannesburg)"}),
            "resets 03:00 Sep 21")

    def test_every_clients_wording_normalises_to_the_same_shape(self):
        # Claude, Codex and Grok each order the date and the clock
        # differently, and Claude alone uses 12-hour time. One row next to
        # another should read the same way regardless of which wrote it.
        self.assertEqual(swlib._normalize_reset("Sep 21 at 3am"), "03:00 Sep 21")
        self.assertEqual(swlib._normalize_reset("Sep 17 at 6:59pm"), "18:59 Sep 17")
        self.assertEqual(swlib._normalize_reset("16:17 on 19 Sep"), "16:17 Sep 19")
        self.assertEqual(swlib._normalize_reset("September 18, 13:11"), "13:11 Sep 18")

    def test_a_date_with_no_time_is_left_as_a_date(self):
        # Cursor's pools reset on a day, with no time of day reported, and
        # there is nothing to invent one from.
        self.assertEqual(swlib._normalize_reset("Sep 30"), "Sep 30")

    def test_midnight_and_noon_convert_correctly(self):
        self.assertEqual(swlib._normalize_reset("Sep 21 at 12am"), "00:00 Sep 21")
        self.assertEqual(swlib._normalize_reset("Sep 21 at 12pm"), "12:00 Sep 21")

    def test_an_unrecognised_string_is_returned_exactly_as_given(self):
        # A wrong reformat is worse than an unfamiliar one, so a string this
        # cannot parse comes back untouched rather than mangled.
        self.assertEqual(swlib._normalize_reset("whenever it feels like it"),
                         "whenever it feels like it")

    def test_a_parenthetical_that_is_not_a_trailing_timezone_is_left_alone(self):
        # Only a trailing "(...)" is trimmed; one in the middle of the string
        # is part of what the client actually said and stays.
        self.assertEqual(
            swlib.reset_note("primary", {"seven_day_resets": "Sep (approx) 21"}),
            "resets Sep (approx) 21")

    def test_nothing_reported_is_an_empty_note_not_a_bare_label(self):
        self.assertEqual(swlib.reset_note("primary", {}), "")
        self.assertEqual(swlib.reset_note("primary", {"seven_day_resets": None}), "")
        self.assertEqual(swlib.reset_note("primary", {"seven_day_resets": "unknown"}), "")

    def test_cursor_uses_its_own_pools_reset(self):
        self.assertEqual(
            swlib.reset_note("cursor", {"extra": {"resets": "Sep 30"}}),
            "resets Sep 30")
        self.assertEqual(
            swlib.reset_note("cursor", {"seven_day_resets": "16:17 on 19 Sep"}), "",
            "cursor has no weekly window, so a weekly reset string must not leak in")

    def test_a_codex_free_workspace_uses_the_monthly_reset(self):
        usage = {"five_hour_pct": None, "seven_day_pct": None,
                 "seven_day_resets": "16:17 on 19 Sep",
                 "extra": {"monthly_pct": 8, "monthly_resets": "02:00 on 1 Oct"}}
        self.assertEqual(swlib.reset_note("codex", usage), "resets 02:00 Oct 1")

    def test_a_codex_role_with_a_real_weekly_window_ignores_the_monthly_field(self):
        usage = {"five_hour_pct": None, "seven_day_pct": 100,
                 "seven_day_resets": "16:17 on 19 Sep",
                 "extra": {"monthly_resets": "02:00 on 1 Oct"}}
        self.assertEqual(swlib.reset_note("codex", usage), "resets 16:17 Sep 19")


class CodexRoles(Base):
    """Two Codex sign-ins, each its own CODEX_HOME."""

    def test_the_default_directory_is_never_exported(self):
        self.write_config(codex={"enabled": True})
        self.assertIsNone(swlib.codex_home("codex"))
        self.write_config(codex={"enabled": True, "config_dir": "~/.codex"})
        self.assertIsNone(swlib.codex_home("codex"))
        self.write_config(codex={"enabled": True, "config_dir": ""})
        self.assertIsNone(swlib.codex_home("codex"))

    def test_a_directory_of_its_own_is_returned_expanded(self):
        self.write_config(codex2={"enabled": True, "config_dir": "~/.codex-personal"})
        self.assertEqual(swlib.codex_home("codex2"),
                         os.path.join(swlib.HOME, ".codex-personal"))

    def test_full_access_is_off_unless_the_config_says_true(self):
        self.write_config(codex={"enabled": True},
                          codex2={"enabled": True, "full_access": "yes"})
        self.assertFalse(swlib.full_access("codex"))
        self.assertFalse(swlib.full_access("codex2"))
        self.write_config(codex2={"enabled": True, "full_access": True})
        self.assertTrue(swlib.full_access("codex2"))

    def test_the_second_codex_is_a_worker_a_route_and_a_destination(self):
        cfg = self.write_config(codex2={"enabled": True, "label": "Personal Codex"})
        self.assertIn("codex2", swlib.enabled_roles())
        self.assertEqual(swlib.parse_route("second-wind/codex2", cfg)["worker"], "codex2")
        self.assertEqual(swlib.parse_route("codex-personal", cfg)["worker"], "codex2")
        self.assertEqual(swlib.parse_route("second-wind/codex2/gpt-6.1-sol/high", cfg),
                         {"worker": "codex2", "model": "gpt-6.1-sol", "effort": "high",
                          "label": "Personal Codex"})
        self.assertEqual(swlib.route_id("codex2"), "second-wind/codex2")
        self.write_usage("codex2", age_seconds=60, seven_day_pct=40, five_hour_pct=None)
        self.assertEqual([row["worker"] for row in swlib.destinations(now=NOW)],
                         ["codex2"])
        self.assertEqual(swlib.model_menu("codex2"), swlib.model_menu("codex"))

    def test_a_third_codex_role_is_wired_the_same_way(self):
        cfg = self.write_config(codex3={"enabled": True})
        self.assertIn("codex3", swlib.enabled_roles())
        self.assertEqual(swlib.parse_route("second-wind/codex3/gpt-6.1-sol/low", cfg)["worker"],
                         "codex3")
        self.assertEqual(swlib.route_label("codex3", cfg), "the third Codex account")
        self.assertEqual(swlib.model_menu("codex3"), swlib.model_menu("codex"))
        self.assertEqual(swlib.headroom("codex3", {"seven_day_pct": 25}), 75)
        self.assertIn("codex3", swlib.CODEX_ROLES)

    def test_headroom_treats_both_codex_roles_alike(self):
        self.assertEqual(swlib.headroom("codex2", {"seven_day_pct": 13}), 87)
        self.assertEqual(swlib.headroom("codex2", {"five_hour_pct": 30,
                                                   "seven_day_pct": 13}), 70)

    def test_a_free_workspace_has_only_a_monthly_window_and_it_counts(self):
        usage = {"five_hour_pct": None, "seven_day_pct": None,
                 "extra": {"monthly_pct": 8}}
        self.assertEqual(swlib.headroom("codex", usage), 92)
        self.assertEqual(swlib.headroom("codex2", usage), 92)
        # Claude never reports a monthly window, so for it this is no reading.
        self.assertIsNone(swlib.headroom("primary", usage))
        self.write_config(codex={"enabled": True, "label": "Codex"})
        self.write_usage("codex", age_seconds=60, **usage)
        self.assertEqual(swlib.brief_lines(now=NOW)[1], "Codex: monthly 8% (60s ago)")
        row = next(line for line in swlib.brief_card(now=NOW) if line.startswith("Codex"))
        self.assertIn("monthly limit 8% used", row)
        self.assertNotIn(swlib.BAR_ABSENT, row)

    def test_a_free_workspaces_reset_is_the_monthly_one_not_a_weekly_one(self):
        # It has no weekly window, so a weekly reset string here would name a
        # window this row is not showing.
        self.write_config(codex={"enabled": True, "label": "Codex"})
        self.write_usage("codex", age_seconds=60, five_hour_pct=None, seven_day_pct=None,
                         seven_day_resets="16:17 on 19 Sep",
                         extra={"monthly_pct": 8, "monthly_resets": "02:00 on 1 Oct"})
        row, notes = card_account("Codex")
        self.assertIn("monthly limit 8% used", row)
        self.assertEqual(notes, "resets 02:00 Oct 1")
        self.assertNotIn("16:17 on 19 Sep", row)


class IdentityDrift(Base):
    """A reader saw a different sign-in from the one the config pinned."""

    def test_unknown_on_either_side_is_never_a_drift(self):
        self.write_config(codex={"enabled": True, "account": "unknown", "plan": "unknown"})
        self.write_usage("codex", account="user@example.com", plan="Business")
        self.assertIsNone(swlib.identity_drift("codex", now=NOW))
        self.write_config(codex={"enabled": True, "account": "user@example.com",
                                 "plan": "Business"})
        self.write_usage("codex", account=None, plan="")
        self.assertIsNone(swlib.identity_drift("codex", now=NOW))

    def test_a_workspace_switch_keeps_the_email_and_is_still_caught(self):
        # The case that happened: same login, Business seat one minute and the
        # Free personal space the next.
        self.write_config(codex={"enabled": True, "account": "user@example.com",
                                 "plan": "Business Premium"})
        self.write_usage("codex", account="user@example.com", plan="Free")
        self.assertEqual(swlib.identity_drift("codex", now=NOW),
                         "signed in as Free, not the configured Business Premium")

    def test_a_dead_reading_is_not_compared(self):
        # An hours-old file written before a re-login would otherwise report a
        # drift on a healthy sign-in, and --check would disagree with the panel.
        self.write_config(codex={"enabled": True, "account": "user@example.com",
                                 "plan": "Business Premium"})
        self.write_usage("codex", age_seconds=7200, account="user@example.com", plan="Free")
        self.assertIsNone(swlib.identity_drift("codex", now=NOW))

    def test_a_changed_account_is_caught_for_any_role(self):
        self.write_config(secondary={"enabled": True, "config_dir": "~/.claude-secondary",
                                     "account": "one@example.com", "plan": "max"})
        self.write_usage("secondary", account="two@example.com", plan="Claude Max")
        self.assertEqual(swlib.identity_drift("secondary", now=NOW),
                         "signed in as two@example.com, not the configured one@example.com")

    def test_plan_wording_is_only_compared_for_codex(self):
        # Setup writes "max" and the reader writes "Claude Max": not a drift.
        self.write_config(secondary={"enabled": True, "config_dir": "~/.claude-secondary",
                                     "account": "user@example.com", "plan": "max"})
        self.write_usage("secondary", account="user@example.com", plan="Claude Max")
        self.assertIsNone(swlib.identity_drift("secondary", now=NOW))


class CodexPinning(Base):
    """Setup pins the account and plan it will later compare a reading against,
    newest source first, so a rerun after a deliberate change clears the drift."""

    def block(self, previous, probed, **usage):
        if usage:
            self.write_usage("codex", **usage)
        return sw_setup.codex_block(previous, "codex", True, "~/.codex", True,
                                    {"account": probed}, False, now=NOW)

    def test_the_live_probe_beats_the_previous_config(self):
        previous = {"codex": {"account": "a@example.com", "plan": "Business"}}
        block = self.block(previous, "b@example.com")
        self.assertEqual(block["account"], "b@example.com")

    def test_a_fresh_reading_beats_the_previous_config(self):
        previous = {"codex": {"account": "a@example.com", "plan": "Business"}}
        block = self.block(previous, "unknown", age_seconds=60,
                           account="b@example.com", plan="Free")
        self.assertEqual((block["account"], block["plan"]), ("b@example.com", "Free"))

    def test_a_dead_reading_does_not_pin_anything(self):
        previous = {"codex": {"account": "a@example.com", "plan": "Business"}}
        block = self.block(previous, "unknown", age_seconds=7200,
                           account="b@example.com", plan="Free")
        self.assertEqual((block["account"], block["plan"]), ("a@example.com", "Business"))

    def test_the_rerun_clears_the_drift(self):
        # The whole point: a drift, then the rerun of --write every message
        # tells the user to do, then no drift.
        self.write_config(codex={"enabled": True, "account": "a@example.com",
                                 "plan": "Business"})
        self.write_usage("codex", age_seconds=60, account="a@example.com", plan="Free")
        self.assertIsNotNone(swlib.identity_drift("codex", now=NOW))
        block = self.block(swlib.load_config(), "unknown")
        cfg = swlib.load_config()
        cfg["codex"].update(block)
        swlib.write_json_atomic(swlib.config_path(), cfg)
        self.assertIsNone(swlib.identity_drift("codex", now=NOW))

    def test_label_survives_and_full_access_is_only_ever_a_boolean(self):
        block = self.block({"codex": {"label": "Work Codex"}}, "unknown")
        self.assertEqual(block["label"], "Work Codex")
        given = sw_setup.codex_block({"codex": {"label": "Work Codex"}}, "codex", True,
                                     "~/.codex", True, {}, False, now=NOW,
                                     label="Codex work")
        self.assertEqual(given["label"], "Codex work", "a label passed on the "
                         "command line beats the one already in the config")
        self.assertIs(block["full_access"], False)
        self.assertEqual(block["account"], "unknown")

    def test_the_card_row_carries_the_drift(self):
        self.write_config(codex={"enabled": True, "label": "Codex",
                                 "account": "user@example.com", "plan": "Business Premium"})
        self.write_usage("codex", age_seconds=60, seven_day_pct=98, five_hour_pct=None,
                         account="user@example.com", plan="Free")
        row, notes = card_account("Codex")
        self.assertIn("98%", row)
        self.assertNotIn("signed in as", row)
        self.assertIn("signed in as Free, not the configured Business Premium", notes)


class Repin(Base):
    """--repin: the narrow fix for a drift --check already named. Unlike
    --write, it must never touch anything but the account/plan fields it is
    asked to fix."""

    def repin(self, *roles):
        args = sw_setup.build_parser().parse_args(["--repin", *roles])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sw_setup.cmd_repin(args)
        return out.getvalue()

    def test_an_upgraded_codex_plan_is_repinned_and_the_drift_clears(self):
        # The case this exists for: a Plus seat upgraded to Pro (Codex reports
        # it as "Pro Lite"), and the config still expects "Plus".
        self.write_config(codex={"enabled": True, "label": "Codex",
                                 "account": "user@example.com", "plan": "Plus"})
        self.write_usage_live("codex", age_seconds=60, account="user@example.com",
                              plan="Pro Lite", seven_day_pct=10)
        self.assertIsNotNone(swlib.identity_drift("codex"))
        out = self.repin("codex")
        self.assertIn("codex plan: Plus -> Pro Lite", out)
        self.assertEqual(swlib.load_config()["codex"]["plan"], "Pro Lite")
        self.assertIsNone(swlib.identity_drift("codex"))

    def test_defaults_to_every_enabled_role_when_none_named(self):
        self.write_config(
            primary={"config_dir": "~/.claude", "label": "primary",
                     "account": "old@example.com"},
            codex={"enabled": True, "label": "Codex",
                  "account": "user@example.com", "plan": "Plus"})
        self.write_usage_live("primary", age_seconds=60, account="new@example.com")
        self.write_usage_live("codex", age_seconds=60, account="user@example.com",
                              plan="Pro Lite")
        self.repin()
        cfg = swlib.load_config()
        self.assertEqual(cfg["primary"]["account"], "new@example.com")
        self.assertEqual(cfg["codex"]["plan"], "Pro Lite")

    def test_a_claude_roles_plan_field_is_never_touched(self):
        # identity_drift only ever compares plan for Codex; repinning it for
        # Claude would just invent a comparison nobody makes.
        self.write_config(secondary={"enabled": True, "config_dir": "~/.claude-secondary",
                                     "account": "user@example.com", "plan": "max"})
        self.write_usage_live("secondary", age_seconds=60, account="user@example.com",
                              plan="Claude Max")
        self.repin("secondary")
        self.assertEqual(swlib.load_config()["secondary"]["plan"], "max")

    def test_a_stale_or_missing_reading_is_skipped_not_guessed_at(self):
        self.write_config(codex={"enabled": True, "account": "a@example.com",
                                 "plan": "Business"})
        self.write_usage_live("codex", age_seconds=99999, account="b@example.com",
                              plan="Free")
        out = self.repin("codex")
        self.assertIn("no current reading, skipping", out)
        self.assertEqual(swlib.load_config()["codex"]["account"], "a@example.com")

    def test_nothing_to_repin_when_everything_already_matches(self):
        self.write_config(codex={"enabled": True, "account": "a@example.com",
                                 "plan": "Business"})
        self.write_usage_live("codex", age_seconds=60, account="a@example.com", plan="Business")
        out = self.repin("codex")
        self.assertIn("Nothing to repin", out)

    def test_an_unknown_role_name_is_refused(self):
        self.write_config()
        with self.assertRaises(SystemExit):
            self.repin("not-a-role")

    def test_only_the_named_roles_account_and_plan_fields_change(self):
        # Everything else --write owns (hooks, launchd, thresholds, other
        # roles' fields) must survive untouched.
        cfg = self.write_config(
            codex={"enabled": True, "account": "a@example.com", "plan": "Business"},
            thresholds={"five_hour_pct": 77, "seven_day_pct": 66})
        self.write_usage_live("codex", age_seconds=60, account="a@example.com", plan="Free")
        self.repin("codex")
        after = swlib.load_config()
        self.assertEqual(after["thresholds"], cfg["thresholds"])
        self.assertEqual(after["codex"]["enabled"], True)
        self.assertEqual(after["codex"]["account"], "a@example.com")
        self.assertEqual(after["codex"]["plan"], "Free")


class FieldNotes(Base):
    """A test round's diary: process only, scrubbed at write time, off by default."""

    def test_off_by_default_and_nothing_is_written(self):
        self.write_config()
        self.assertFalse(swlib.field_note("setup", command="check"))
        self.assertFalse(os.path.exists(swlib.field_notes_path()))

    def test_on_writes_a_scrubbed_row(self):
        self.write_config(field_notes={"enabled": True})
        home = swlib.HOME
        self.assertTrue(swlib.field_note("note", text="signed in as one@example.com from %s/x" % home))
        self.assertTrue(swlib.field_note("setup", command="write",
                                         args=["--codex-dir", home + "/.codex-work"]))
        rows = swlib.load_field_notes()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["event"], "note")
        self.assertEqual(rows[0]["text"], "signed in as <account> from ~/x")
        self.assertEqual(rows[1]["args"], ["--codex-dir", "~/.codex-work"])
        self.assertTrue(rows[0]["ts"].endswith("Z"))

    def test_a_refresh_event_records_each_readers_outcome(self):
        self.write_config(field_notes={"enabled": True}, codex={"enabled": True})
        swlib.write_text_atomic(swlib.status_path("primary"), "OK\n")
        swlib.write_text_atomic(swlib.status_path("codex"), "LOGIN EXPIRED: sign in\n")
        self.assertTrue(swlib.field_note_refresh())
        row = swlib.load_field_notes()[-1]
        self.assertEqual(row["event"], "refresh")
        self.assertEqual(row["outcomes"], {"primary": "ok", "codex": "login"})

    def test_a_broken_notes_file_never_raises(self):
        self.write_config(field_notes={"enabled": True})
        os.makedirs(swlib.field_notes_path(), exist_ok=True)   # a directory, not a file
        self.assertFalse(swlib.field_note("note", text="x"))
        self.assertEqual(swlib.load_field_notes(), [])


class RecordedArgs(Base):
    def test_labels_are_dropped_and_home_dotdirs_kept(self):
        home = swlib.HOME
        seen = sw_setup.recorded_args(["--write", "--codex-label", "Work of Someone",
                                       "--codex-dir", home + "/.codex-work", "--codex", "on",
                                       "--timeout", "900", "--level", "relief"])
        self.assertEqual(seen, ["--write", "--codex-label", "<value>",
                                "--codex-dir", "~/.codex-work", "--codex", "on",
                                "--timeout", "900", "--level", "relief"])

    def test_the_equals_form_and_paths_elsewhere_are_hidden_too(self):
        seen = swlib.safe_args(["--codex-label=Some Client", "--codex-dir",
                                "/Volumes/ClientA/.codex", "--primary", "~/Documents/ClientB",
                                "--picker-models", "codex=gpt-6.1-sol/high"])
        self.assertEqual(seen, ["--codex-label", "<value>", "--codex-dir", "<path>",
                                "--primary", "<path>", "--picker-models", "<value>"])


class FieldNoteAllowlist(Base):
    """The promise is kept by the table in swlib, not by each caller."""

    def setUp(self):
        super().setUp()
        self.write_config(field_notes={"enabled": True}, codex={"enabled": True})

    def last(self):
        return swlib.load_field_notes()[-1]

    def test_an_unknown_event_is_not_written(self):
        self.assertFalse(swlib.field_note("prompt", text="the whole prompt"))
        self.assertEqual(swlib.load_field_notes(), [])

    def test_an_unknown_key_is_dropped_from_a_known_event(self):
        swlib.field_note("setup", command="check", cwd="/Volumes/ClientA/work", prompt="x")
        row = self.last()
        self.assertNotIn("cwd", row)
        self.assertNotIn("prompt", row)
        self.assertEqual(row["command"], "check")

    def test_a_label_change_is_recorded_by_name_not_value(self):
        swlib.field_note("write", level="relief", workers=["codex"],
                         changed=["codex label", "codex label Old Client to New Client",
                                  "five-hour threshold"])
        self.assertEqual(self.last()["changed"], ["codex label", "five-hour threshold"])

    def test_check_lines_become_kinds(self):
        swlib.field_note("check",
                         faults=["codex2's directory ~/Documents/ClientA/.codex does not exist."],
                         warnings=["codex is signed in as Free, not the configured Business.",
                                   "grok is 1.0.25 now, 1.0.13 when set up."])
        row = self.last()
        self.assertEqual(row["faults"], ["directory-missing"])
        self.assertEqual(row["warnings"], ["sign-in-changed", "client-version-moved"])
        self.assertNotIn("ClientA", json.dumps(row))

    def test_routes_keep_worker_and_model_tokens_only(self):
        swlib.field_note("route", source="chat", worker="codex2", model="gpt-6.1-sol",
                         effort="high", mode="work", cwd="/x")
        row = self.last()
        self.assertEqual((row["worker"], row["model"], row["effort"]), ("codex2", "gpt-6.1-sol", "high"))
        self.assertNotIn("cwd", row)
        swlib.field_note("route", source="chat", worker="nonsense", model="a model with spaces")
        self.assertEqual((self.last()["worker"], self.last()["model"]), ("<role>", "<value>"))

    def test_the_file_is_created_private(self):
        swlib.field_note("note", text="x")
        self.assertEqual(oct(os.stat(swlib.field_notes_path()).st_mode & 0o777), "0o600")


class PrimarySession(Base):
    def test_a_symlinked_profile_is_still_the_primary(self):
        real = os.path.join(self.home, "profile")
        link = os.path.join(self.home, "link-to-profile")
        os.makedirs(real)
        os.symlink(real, link)
        self.write_config(primary={"config_dir": real})
        os.environ["CLAUDE_CONFIG_DIR"] = link
        self.assertTrue(swlib.is_primary_session())

    def test_another_profile_is_not_the_primary(self):
        self.write_config(primary={"config_dir": os.path.join(self.home, "profile")})
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.home, "other")
        self.assertFalse(swlib.is_primary_session())

    def test_the_default_profile_is_assumed_when_nothing_is_set(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        self.write_config(primary={"config_dir": "~/.claude"})
        self.assertTrue(swlib.is_primary_session())
        self.write_config(primary={"config_dir": os.path.join(self.home, "profile")})
        self.assertFalse(swlib.is_primary_session())

    def test_no_config_is_not_a_primary_session(self):
        self.assertFalse(swlib.is_primary_session())


class Writing(Base):
    def test_json_is_written_private_and_whole(self):
        path = os.path.join(self.home, "thing.json")
        swlib.write_json_atomic(path, {"a": 1})
        self.assertEqual(read_json(path), {"a": 1})
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_the_original_backup_is_taken_once_and_never_moves(self):
        path = os.path.join(self.home, "settings.json")
        with open(path, "w") as handle:
            handle.write('{"first": true}')
        original, stamped = swlib.backup_once(path)
        self.assertTrue(original.endswith(".second-wind-original"))
        self.assertTrue(os.path.exists(stamped))
        with open(path, "w") as handle:
            handle.write('{"second": true}')
        time.sleep(1.1)
        again, stamped_again = swlib.backup_once(path)
        self.assertIsNone(again)
        self.assertNotEqual(stamped, stamped_again)
        self.assertEqual(read_json(path + ".second-wind-original"),
                         {"first": True})

    def test_backing_up_a_file_that_is_not_there_does_nothing(self):
        self.assertEqual(swlib.backup_once(os.path.join(self.home, "absent")),
                         (None, None))


class Config(Base):
    def test_a_missing_config_reads_as_empty(self):
        self.assertEqual(swlib.load_config(), {})
        self.assertEqual(swlib.enabled_roles(), [])
        self.assertEqual(swlib.brief_lines(), [])

    def test_broken_json_reads_as_empty(self):
        swlib.write_text_atomic(swlib.config_path(), "{not json")
        self.assertEqual(swlib.load_config(), {})

    def test_enabled_roles_always_include_the_primary(self):
        self.write_config(codex={"enabled": True}, grok={"enabled": False})
        self.assertEqual(swlib.enabled_roles(), ["primary", "codex"])

    def test_a_nonsense_interval_falls_back_to_the_default(self):
        self.write_config(refresh={"interval_minutes": "soon"})
        self.assertEqual(swlib.interval_minutes(), 15)
        self.write_config(refresh={"interval_minutes": 0})
        self.assertEqual(swlib.interval_minutes(), 15)

    def test_thresholds_reject_out_of_range_values(self):
        self.write_config(thresholds={"five_hour_pct": 500, "seven_day_pct": 70})
        self.assertEqual(swlib.thresholds(), (90, 70))


class AccountRows(Base):
    """The table cell for a role. statusline.sh writes a reading and no status
    file at all, so a cache with no status must read as ready, not as a fault."""

    def test_a_fresh_cache_with_no_status_file_is_ready(self):
        cfg = self.write_config()
        self.write_usage_live("primary", age_seconds=16, five_hour_pct=2,
                              seven_day_pct=16)
        self.assertFalse(os.path.exists(swlib.status_path("primary")))
        row = sw_setup.account_row("primary", cfg)
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["headroom"], 84)
        self.assertIn("5h 2%", row["limits"])
        self.assertEqual(row["age"], "16s")

    def test_a_stale_cache_with_no_status_file_says_stale(self):
        cfg = self.write_config()
        self.write_usage_live("primary", age_seconds=1800, five_hour_pct=2,
                              seven_day_pct=16)
        row = sw_setup.account_row("primary", cfg)
        self.assertEqual(row["status"], "reading is stale")
        self.assertEqual(row["headroom"], 84)

    def test_a_real_fault_overrides_the_reading(self):
        cfg = self.write_config()
        self.write_usage_live("primary", age_seconds=16, five_hour_pct=2,
                              seven_day_pct=16)
        self.write_status_live("primary", "LOGIN EXPIRED: sign in again")
        row = sw_setup.account_row("primary", cfg)
        self.assertEqual(row["status"], "login expired")
        self.assertIsNone(row["headroom"])

    def test_no_cache_at_all_says_so(self):
        cfg = self.write_config()
        row = sw_setup.account_row("primary", cfg)
        self.assertEqual(row["status"], "no current reading")
        self.assertIsNone(row["headroom"])


class CorruptCache(Base):
    """A cache file can hold anything. Nothing in the library may raise on it,
    because brief_lines runs inside a hook and a hook that raises breaks the
    session."""

    def setUp(self):
        super().setUp()
        self.write_config(primary={"config_dir": "~/.claude", "label": "P"})

    def test_a_cached_at_that_is_not_a_number_does_not_raise(self):
        swlib.write_json_atomic(swlib.usage_path("primary"),
                                {"cached_at": "not-a-number", "five_hour_pct": 2,
                                 "seven_day_pct": 3})
        self.write_status_live("primary", "OK")
        self.assertEqual(swlib.status("primary"), "OK")
        self.assertIsNone(swlib.cache_age("primary"))
        self.assertEqual(swlib.freshness("primary", now=NOW), "none")
        self.assertEqual(swlib.brief_lines(now=NOW),
                         ["P: no current reading, refreshing"])

    def test_a_cache_that_is_not_an_object_does_not_raise(self):
        swlib.write_text_atomic(swlib.usage_path("primary"), "[1, 2, 3]")
        self.write_status_live("primary", "OK")
        self.assertEqual(swlib.load_usage("primary"), {})
        self.assertEqual(swlib.status("primary"), "OK")
        self.assertEqual(swlib.brief_lines(now=NOW),
                         ["P: no current reading, refreshing"])


class Routing(Base):
    """parse_route is the only place that decides what a routing id means, so
    every spelling the hook, the guard and the picker accept is pinned here."""

    def setUp(self):
        super().setUp()
        self.cfg = self.write_config(
            secondary={"enabled": True, "config_dir": "~/.claude-secondary",
                       "label": "spare Claude"},
            codex={"enabled": True, "label": "codex"})

    def test_a_worker_on_its_own(self):
        for target, worker in (("second-wind/personal", "secondary"),
                               ("second-wind/secondary", "secondary"),
                               ("second-wind/codex", "codex"),
                               ("second-wind/grok", "grok"),
                               ("second-wind/cursor", "cursor")):
            found = swlib.parse_route(target, self.cfg)
            self.assertEqual(found["worker"], worker, target)
            self.assertIsNone(found["model"], target)
            self.assertIsNone(found["effort"], target)

    def test_a_model_and_an_effort(self):
        found = swlib.parse_route("second-wind/codex/gpt-6.1-sol/high", self.cfg)
        self.assertEqual((found["worker"], found["model"], found["effort"]),
                         ("codex", "gpt-6.1-sol", "high"))
        found = swlib.parse_route("second-wind/personal/opus", self.cfg)
        self.assertEqual((found["worker"], found["model"], found["effort"]),
                         ("secondary", "opus", None))

    def test_the_bare_aliases_a_desktop_user_types(self):
        for target, worker in (("claude-personal", "secondary"),
                               ("personal", "secondary"),
                               ("second-wind-personal", "secondary"),
                               ("codex", "codex"), ("grok", "grok"),
                               ("cursor", "cursor")):
            self.assertEqual(swlib.parse_route(target, self.cfg)["worker"],
                             worker, target)

    def test_case_and_stray_slashes_do_not_matter(self):
        for target in ("Second-Wind/Personal", " second-wind/personal ",
                       "second-wind/personal/"):
            self.assertEqual(swlib.parse_route(target, self.cfg)["worker"],
                             "secondary", target)

    def test_a_real_model_is_not_ours(self):
        for target in ("opus", "haiku", "claude-opus-5", "claude-sonnet-5[1m]",
                       "second-wind", "second-wind/", "second-wind/nobody",
                       "second-wind/codex/a/b/c", "second-wind/codex//high",
                       "second-wind/codex/-bad", "second-wind/codex/a b",
                       "", "   ", None, 7, ["second-wind/codex"]):
            self.assertIsNone(swlib.parse_route(target, self.cfg), repr(target))

    def test_the_label_is_the_users_own_when_they_set_one(self):
        self.assertEqual(swlib.parse_route("second-wind/personal",
                                           self.cfg)["label"], "spare Claude")
        # setup writes the role name as the label, which reads as nothing in a
        # sentence, so our own wording stands in for it.
        self.assertEqual(swlib.parse_route("second-wind/codex",
                                           self.cfg)["label"], "Codex")
        self.assertEqual(swlib.parse_route("second-wind/grok",
                                           self.cfg)["label"], "Grok Build")

    def test_the_canonical_id_for_each_worker(self):
        self.assertEqual(swlib.route_id("secondary"), "second-wind/personal")
        for worker in ("codex", "grok", "cursor"):
            self.assertEqual(swlib.route_id(worker), "second-wind/" + worker)
            self.assertEqual(
                swlib.parse_route(swlib.route_id(worker), self.cfg)["worker"],
                worker)


class DesktopStore(Base):
    """desktop_session_model reads the desktop app's session files, which is the
    only signal that a routing name was typed into its model menu. It reads and
    never writes, and it has to answer at a prompt, so the scan is bounded and
    the match is cached."""

    SESSION = "0eaa1111-2222-3333-4444-555566667777"

    def setUp(self):
        super().setUp()
        self.store = os.path.join(self.home, "desktop-store")
        self.previous_store = os.environ.get("SW_DESKTOP_STORE")
        os.environ["SW_DESKTOP_STORE"] = self.store

    def tearDown(self):
        if self.previous_store is None:
            os.environ.pop("SW_DESKTOP_STORE", None)
        else:
            os.environ["SW_DESKTOP_STORE"] = self.previous_store
        super().tearDown()

    def entry(self, name, data, raw=None):
        folder = os.path.join(self.store, "%s-outer" % name, "%s-inner" % name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "local_%s.json" % name)
        with open(path, "w") as handle:
            handle.write(raw if raw is not None else json.dumps(data))
        return path

    def test_the_matching_entry_decides(self):
        self.entry("other", {"cliSessionId": "another", "model": "claude-opus-5"})
        self.entry("mine", {"cliSessionId": self.SESSION,
                            "model": "claude-personal"})
        self.assertEqual(swlib.desktop_session_model(self.SESSION),
                         "claude-personal")

    def test_no_entry_no_answer(self):
        self.entry("other", {"cliSessionId": "another", "model": "claude-opus-5"})
        self.assertIsNone(swlib.desktop_session_model(self.SESSION))
        self.assertFalse(os.path.exists(swlib.session_map_path()))

    def test_an_entry_without_a_model_is_not_a_model(self):
        for data in ({"cliSessionId": self.SESSION},
                     {"cliSessionId": self.SESSION, "model": ""},
                     {"cliSessionId": self.SESSION, "model": None},
                     {"cliSessionId": self.SESSION, "model": 7}):
            self.entry("mine", data)
            self.assertIsNone(swlib.desktop_session_model(self.SESSION),
                              repr(data))

    def test_rubbish_in_the_store_is_skipped(self):
        # The app writes these files while it runs, so a half-written or
        # rewritten one is ordinary. One unreadable file must not hide the
        # entry that matters.
        self.entry("half", None, raw='{"cliSessionId": "%s", "mod' % self.SESSION)
        self.entry("list", None, raw='["%s"]' % self.SESSION)
        self.entry("empty", None, raw="")
        self.entry("mine", {"cliSessionId": self.SESSION, "model": "codex"})
        self.assertEqual(swlib.desktop_session_model(self.SESSION), "codex")

    def test_a_session_id_inside_another_field_is_not_a_match(self):
        self.entry("mine", {"cliSessionId": "another", "model": "claude-personal",
                            "bridgeSessionIds": [self.SESSION]})
        self.assertIsNone(swlib.desktop_session_model(self.SESSION))

    def test_no_store_and_no_session_id(self):
        self.assertIsNone(swlib.desktop_session_model(self.SESSION))
        self.assertIsNone(swlib.desktop_session_model(""))
        self.assertIsNone(swlib.desktop_session_model(None))

    def test_off_macos_it_never_looks(self):
        # The store is an app on one platform. Elsewhere the whole path is a
        # waste of a prompt, so it answers without reading anything.
        self.entry("mine", {"cliSessionId": self.SESSION,
                            "model": "claude-personal"})
        os.environ.pop("SW_DESKTOP_STORE")
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.object(swlib, "DESKTOP_STORE", self.store):
            self.assertIsNone(swlib.desktop_session_model(self.SESSION))
            with mock.patch.object(sys, "platform", "darwin"):
                self.assertEqual(swlib.desktop_session_model(self.SESSION),
                                 "claude-personal")

    def test_the_cache_keeps_the_newest_entries_only(self):
        self.entry("mine", {"cliSessionId": self.SESSION, "model": "codex"})
        swlib.write_json_atomic(swlib.session_map_path(), dict(
            ("session-%03d" % index, "/gone/%d.json" % index)
            for index in range(swlib.DESKTOP_MAP_MAX + 10)))
        self.assertEqual(swlib.desktop_session_model(self.SESSION), "codex")
        with open(swlib.session_map_path()) as handle:
            known = json.load(handle)
        self.assertEqual(len(known), swlib.DESKTOP_MAP_MAX)
        self.assertIn(self.SESSION, known)
        self.assertNotIn("session-000", known)

    def test_a_broken_cache_file_is_ignored(self):
        self.entry("mine", {"cliSessionId": self.SESSION, "model": "codex"})
        for raw in ("{not json", "[]", '{"%s": 7}' % self.SESSION, ""):
            with open(swlib.session_map_path(), "w") as handle:
                handle.write(raw)
            self.assertEqual(swlib.desktop_session_model(self.SESSION), "codex",
                             raw)

    def test_the_scan_is_bounded(self):
        # A store that has grown for years cannot make a prompt wait. Only the
        # newest files are read, and a live session's file is one of them
        # because the app rewrites it as the session goes.
        old = self.entry("mine", {"cliSessionId": self.SESSION,
                                  "model": "claude-personal"})
        os.utime(old, (1_600_000_000, 1_600_000_000))
        for index in range(swlib.DESKTOP_SCAN_MAX + 5):
            self.entry("filler-%03d" % index,
                       {"cliSessionId": "filler-%03d" % index,
                        "model": "claude-opus-5"})
        self.assertIsNone(swlib.desktop_session_model(self.SESSION))


class ModeFile(Base):
    """One writer for the routing override, because two hooks write it now."""

    def route(self, **fields):
        found = {"worker": "codex", "model": None, "effort": None,
                 "label": "Codex"}
        found.update(fields)
        return found

    def test_the_five_keys_and_nothing_else(self):
        swlib.write_mode(self.route())
        written = read_json(swlib.mode_path())
        self.assertEqual(set(written), {"worker", "model", "effort", "set_at",
                                        "label"})
        self.assertEqual(written["worker"], "codex")
        self.assertEqual(written["label"], "Codex")
        self.assertIsNone(written["model"])
        self.assertIsNone(written["effort"])

    def test_a_source_is_recorded_when_there_is_one(self):
        swlib.write_mode(self.route(model="gpt-6.1-sol", effort="high"),
                         source="desktop")
        written = read_json(swlib.mode_path())
        self.assertEqual(written["source"], "desktop")
        self.assertEqual((written["model"], written["effort"]),
                         ("gpt-6.1-sol", "high"))

    def test_it_is_written_where_every_reader_looks(self):
        self.assertEqual(swlib.mode_path(),
                         os.path.join(self.home, "mode"))
        self.assertEqual(swlib.routes_dir(),
                         os.path.join(self.home, "routes"))

    def test_a_session_route_is_its_own_file_and_a_global_one_is_not(self):
        swlib.write_mode(self.route(), session_id="abc-123")
        self.assertEqual(
            read_json(os.path.join(self.home, "routes", "abc-123.json"))["worker"],
            "codex")
        self.assertFalse(os.path.exists(swlib.mode_path()),
                         "one session's route must not sit in the global file")
        swlib.write_mode(self.route(), session_id="*")
        self.assertEqual(read_json(swlib.mode_path())["session_id"], "*")

    def test_an_id_that_is_not_a_filename_still_gets_one_file(self):
        # Ids are uuids in practice. A crafted one must not write outside the
        # routes directory or collide with the global file.
        for session in ("../mode", "a/b/c", "..", "  spaces  ", "\u00e9\u00e9"):
            path = swlib.route_path(session)
            self.assertEqual(os.path.dirname(path), swlib.routes_dir(), session)
            swlib.write_mode(self.route(), session_id=session)
            self.assertEqual(read_json(path)["session_id"], session.strip())
        self.assertFalse(os.path.exists(swlib.mode_path()))


class ScopedRoutes(Base):
    """A route belongs to the session that armed it. Before this, a route armed
    in one chat told every other session to send its work away."""

    SESSION = "1111aaaa-2222-3333-4444-555566667777"

    def write_route(self, **fields):
        data = {"worker": "codex", "model": None, "effort": None,
                "set_at": int(time.time()), "label": "Codex"}
        data.update(fields)
        swlib.write_json_atomic(swlib.mode_path(), data)
        return data

    def test_a_bare_word_stays_global(self):
        self.write_config()
        swlib.write_text_atomic(swlib.mode_path(), "codex\n")
        for session in (self.SESSION, "another", None):
            route = swlib.active_route(session)
            self.assertEqual(route["worker"], "codex", session)
            self.assertEqual(route["word"], "codex")

    def test_a_bare_id_still_carries_a_model_and_an_effort(self):
        self.write_config()
        swlib.write_text_atomic(swlib.mode_path(),
                                "second-wind/codex/gpt-6.1-sol/high\n")
        route = swlib.active_route(self.SESSION)
        self.assertEqual((route["model"], route["effort"]), ("gpt-6.1-sol", "high"))

    def test_the_session_that_armed_it_gets_it(self):
        self.write_config()
        self.write_route(session_id=self.SESSION)
        self.assertEqual(swlib.active_route(self.SESSION)["worker"], "codex")

    def test_another_session_is_ignored_and_the_route_is_left_alone(self):
        self.write_config()
        self.write_route(session_id=self.SESSION)
        self.assertIsNone(swlib.active_route("somebody-else"))
        self.assertIsNone(swlib.active_route(None))
        self.assertTrue(os.path.exists(swlib.mode_path()),
                        "another session's route is still their decision")

    def test_a_star_applies_everywhere(self):
        self.write_config()
        self.write_route(session_id="*")
        for session in (self.SESSION, "another", None):
            self.assertEqual(swlib.active_route(session)["worker"], "codex",
                             session)

    def test_a_route_naming_no_session_applies_everywhere(self):
        # What an older version of this tool wrote.
        self.write_config()
        self.write_route()
        self.assertEqual(swlib.active_route("anyone")["worker"], "codex")

    def test_a_route_past_twelve_hours_is_ignored_and_deleted(self):
        self.write_config()
        self.write_route(session_id=self.SESSION,
                         set_at=int(time.time()) - swlib.ROUTE_MAX_AGE - 60)
        self.assertIsNone(swlib.active_route(self.SESSION))
        self.assertFalse(os.path.exists(swlib.mode_path()))

    def test_a_route_with_no_timestamp_is_aged_by_the_file(self):
        self.write_config()
        self.write_route(session_id=self.SESSION, set_at=None)
        self.assertEqual(swlib.active_route(self.SESSION)["worker"], "codex")
        old = time.time() - swlib.ROUTE_MAX_AGE - 60
        os.utime(swlib.mode_path(), (old, old))
        self.assertIsNone(swlib.active_route(self.SESSION))

    def test_the_mode_is_carried_and_a_worker_nobody_named_is_not(self):
        self.write_config()
        self.write_route(session_id="*", mode="review")
        self.assertEqual(swlib.active_route(self.SESSION)["mode"], "review")
        self.write_route(session_id="*", worker="banana")
        self.assertIsNone(swlib.active_route(self.SESSION))

    def test_a_written_route_records_the_session_and_nothing_more(self):
        self.write_config()
        swlib.write_mode({"worker": "codex", "model": None, "effort": None,
                          "label": "Codex"}, source="chat",
                         session_id=self.SESSION)
        written = read_json(swlib.route_path(self.SESSION))
        self.assertEqual(written["session_id"], self.SESSION)
        self.assertEqual(written["source"], "chat")
        self.assertNotIn("mode", written)

    def arm(self, session, worker="codex", label="Codex", **fields):
        """A route armed the way route.py and the hooks arm one."""
        route = {"worker": worker, "model": None, "effort": None,
                 "label": label}
        route.update(fields)
        return swlib.write_mode(route, source="chat", session_id=session)

    def test_two_sessions_armed_at_once_each_see_only_their_own(self):
        # The fault this store exists for: both routes went to one file, so
        # arming the second overwrote the first and both chats routed together.
        self.write_config(secondary={"enabled": True, "label": "spare Claude"})
        other = "8888bbbb-2222-3333-4444-555566667777"
        self.arm(self.SESSION)
        self.arm(other, worker="secondary", label="spare Claude")
        self.assertEqual(swlib.active_route(self.SESSION)["worker"], "codex")
        self.assertEqual(swlib.active_route(other)["worker"], "secondary")
        self.assertIsNone(swlib.active_route("a-third-session"))
        for session in (self.SESSION, other):
            self.assertTrue(os.path.exists(swlib.route_path(session)), session)

    def test_the_sessions_own_route_beats_a_global_one(self):
        self.write_config(secondary={"enabled": True, "label": "spare Claude"})
        self.write_route(session_id="*", worker="secondary",
                         label="spare Claude")
        self.arm(self.SESSION)
        self.assertEqual(swlib.active_route(self.SESSION)["worker"], "codex")
        self.assertEqual(swlib.active_route("anyone")["worker"], "secondary")

    def test_the_route_names_the_file_it_came_from(self):
        self.write_config()
        self.arm(self.SESSION)
        self.assertEqual(swlib.active_route(self.SESSION)["path"],
                         swlib.route_path(self.SESSION))
        swlib.drop_route(self.SESSION)
        self.write_route(session_id="*")
        self.assertEqual(swlib.active_route(self.SESSION)["path"],
                         swlib.mode_path())

    def test_an_expired_session_file_is_deleted_where_it_is_found(self):
        self.write_config()
        self.arm(self.SESSION)
        stale = time.time() - swlib.ROUTE_MAX_AGE - 60
        data = read_json(swlib.route_path(self.SESSION))
        data["set_at"] = int(stale)
        swlib.write_json_atomic(swlib.route_path(self.SESSION), data)
        self.assertIsNone(swlib.active_route(self.SESSION))
        self.assertFalse(os.path.exists(swlib.route_path(self.SESSION)))

    def test_clearing_takes_this_session_and_the_global_route(self):
        self.write_config()
        self.arm(self.SESSION)
        self.write_route(session_id="*")
        removed = swlib.clear_route(self.SESSION)
        self.assertEqual(sorted(removed),
                         sorted([swlib.route_path(self.SESSION),
                                 swlib.mode_path()]))
        self.assertIsNone(swlib.active_route(self.SESSION))

    def test_clearing_leaves_another_sessions_route_alone(self):
        self.write_config()
        other = "8888bbbb-2222-3333-4444-555566667777"
        self.arm(other)
        self.assertEqual(swlib.clear_route(self.SESSION), [])
        self.assertEqual(swlib.active_route(other)["worker"], "codex")

    def test_clearing_takes_a_bare_word_because_it_routes_everyone(self):
        self.write_config()
        swlib.write_text_atomic(swlib.mode_path(), "codex\n")
        self.assertEqual(swlib.clear_route(self.SESSION), [swlib.mode_path()])

    def test_armed_routes_lists_every_one_with_its_session_and_age(self):
        self.write_config(secondary={"enabled": True, "label": "spare Claude"})
        other = "8888bbbb-2222-3333-4444-555566667777"
        self.arm(self.SESSION, model="gpt-6.1-sol", effort="high")
        self.arm(other, worker="secondary", label="spare Claude")
        self.write_route(session_id="*", set_at=int(time.time()) - 300)
        rows = swlib.armed_routes()
        self.assertEqual(len(rows), 3)
        by_scope = {row["scope"]: row for row in rows}
        self.assertEqual(set(by_scope), {self.SESSION, other, "*"})
        self.assertEqual(by_scope[self.SESSION]["label"], "Codex")
        self.assertEqual((by_scope[self.SESSION]["model"],
                          by_scope[self.SESSION]["effort"]),
                         ("gpt-6.1-sol", "high"))
        self.assertEqual(by_scope[other]["label"], "spare Claude")
        self.assertGreaterEqual(by_scope["*"]["age"], 300)
        self.assertEqual(rows[-1]["scope"], "*", "newest first")
        self.assertTrue(all(row["readable"] for row in rows))
        self.assertFalse(any(row["expired"] for row in rows))

    def test_armed_routes_names_an_expired_or_unreadable_file(self):
        # A listing, not a reader: --show has to be able to say what is there.
        self.write_config()
        self.arm(self.SESSION)
        data = read_json(swlib.route_path(self.SESSION))
        data["set_at"] = int(time.time()) - swlib.ROUTE_MAX_AGE - 60
        swlib.write_json_atomic(swlib.route_path(self.SESSION), data)
        swlib.write_text_atomic(swlib.mode_path(), "{not json")
        rows = swlib.armed_routes()
        self.assertEqual(len(rows), 2)
        self.assertTrue(os.path.exists(swlib.route_path(self.SESSION)),
                        "a listing must not delete what it lists")
        by_scope = {row["scope"]: row for row in rows}
        self.assertTrue(by_scope[self.SESSION]["expired"])
        self.assertFalse(by_scope["*"]["readable"])


class Destinations(Base):
    """What the /second-wind picker is built from."""

    def house(self):
        self.write_config(secondary={"enabled": True, "label": "spare Claude"},
                          codex={"enabled": True, "label": "codex"},
                          grok={"enabled": False}, cursor={"enabled": False})
        self.write_usage_live("secondary", age_seconds=60, five_hour_pct=10,
                              seven_day_pct=20)
        self.write_usage_live("codex", age_seconds=120, five_hour_pct=70,
                              seven_day_pct=30)

    def test_one_row_per_enabled_worker_sorted_by_headroom(self):
        self.house()
        rows = swlib.destinations()
        self.assertEqual([row["worker"] for row in rows], ["secondary", "codex"])
        self.assertEqual(rows[0]["label"], "spare Claude")
        self.assertEqual(rows[0]["headroom"], 80)
        self.assertEqual(rows[1]["headroom"], 30)
        self.assertIn("5h 10%, 7d 20%", rows[0]["usage"])
        self.assertIn(" ago)", rows[0]["usage"])

    def test_the_primary_is_not_a_destination(self):
        self.house()
        self.assertNotIn("primary", [row["worker"] for row in swlib.destinations()])

    def test_a_dead_reading_has_no_headroom_and_says_so(self):
        self.house()
        self.write_usage_live("codex", age_seconds=7200, five_hour_pct=70)
        row = [r for r in swlib.destinations() if r["worker"] == "codex"][0]
        self.assertIsNone(row["headroom"])
        self.assertEqual(row["usage"], "no current reading, refreshing")

    def test_the_default_presets(self):
        self.house()
        rows = {row["worker"]: row["models"] for row in swlib.destinations()}
        self.assertEqual([entry["text"] for entry in rows["secondary"]],
                         ["claude-fable-5-1 high", "claude-opus-5-5 high",
                          "claude-sonnet-5-5 medium",
                          "claude-haiku-4-5-20251001 low"])
        self.assertEqual(rows["secondary"][1],
                         {"model": "claude-opus-5-5", "effort": "high",
                          "text": "claude-opus-5-5 high",
                          "note": "Opus 5.5, high effort: hard reasoning, "
                                  "spends heavily"})
        self.assertEqual([entry["text"] for entry in rows["codex"]],
                         ["gpt-6.1-sol high", "gpt-6.1-sol medium",
                          "gpt-6-astra high"])
        # Every preset says what it costs, so a row is never a bare model name.
        for entries in rows.values():
            for entry in entries:
                self.assertTrue(entry["note"].strip(), entry)

    def test_the_claude_presets_name_exact_models(self):
        # An alias resolves inside whichever client runs the worker, and an
        # older client keeps its old target: one install still sent `opus` to
        # Opus 5 after Opus 5.5 shipped, under a row that would have said 5.5.
        for entry in swlib.model_menu("secondary", cfg={}):
            self.assertTrue(entry["model"].startswith("claude-"), entry)

    def test_no_default_menu_is_longer_than_one_question_can_show(self):
        # The presets are offered as one AskUserQuestion, which takes four
        # options. A fifth preset is never shown, and nothing says so.
        for worker in swlib.PICKER_MODELS:
            self.assertLessEqual(len(swlib.model_menu(worker, cfg={})), 4, worker)

    def test_grok_offers_effort_without_a_model_and_cursor_neither(self):
        # Grok takes --reasoning-effort and no model of ours; Cursor takes
        # neither, so its one row passes nothing.
        self.write_config(grok={"enabled": True})
        self.assertEqual([(entry["model"], entry["effort"], entry["text"])
                          for entry in swlib.model_menu("grok")],
                         [(None, "high", "high effort"),
                          (None, "medium", "medium effort")])
        self.assertEqual(swlib.model_menu("cursor"),
                         [{"model": None, "effort": None, "text": "default",
                           "note": "The account's own model and effort; "
                                   "neither is passed"}])

    def test_a_codex_effort_word_the_defaults_do_not_use_is_still_accepted(self):
        self.write_config(codex={"enabled": True},
                          picker={"models": {"codex": ["gpt-6.1-sol/xhigh",
                                                       "gpt-6.1-sol/ultra"]}})
        self.assertEqual([entry["text"] for entry in swlib.model_menu("codex")],
                         ["gpt-6.1-sol xhigh", "gpt-6.1-sol ultra"])
        self.assertEqual(swlib.model_menu("codex")[0]["note"],
                         "gpt-6.1-sol, xhigh effort")

    def test_the_config_replaces_a_menu_and_drops_what_it_cannot_parse(self):
        self.write_config(codex={"enabled": True},
                          picker={"models": {"codex": ["gpt-6/low", "a/b/c",
                                                       "", "default"]}})
        self.assertEqual([entry["text"] for entry in swlib.model_menu("codex")],
                         ["gpt-6 low", "default"])
        # A worker the block does not name keeps the built-in presets.
        self.assertEqual([entry["text"] for entry in swlib.model_menu("secondary")],
                         ["claude-fable-5-1 high", "claude-opus-5-5 high",
                          "claude-sonnet-5-5 medium",
                          "claude-haiku-4-5-20251001 low"])

    def test_an_empty_menu_in_the_config_falls_back(self):
        self.write_config(codex={"enabled": True},
                          picker={"models": {"codex": []}})
        self.assertEqual([entry["text"] for entry in swlib.model_menu("codex")],
                         ["gpt-6.1-sol high", "gpt-6.1-sol medium",
                          "gpt-6-astra high"])


class DesktopSessionHere(Base):
    """`route.py --here` needs this session's id, and the desktop store is the
    only place it is written down."""

    def setUp(self):
        super().setUp()
        self.store = os.path.join(self.home, "desktop-store")
        self.previous_store = os.environ.get("SW_DESKTOP_STORE")
        os.environ["SW_DESKTOP_STORE"] = self.store
        self.where = os.path.join(self.home, "project")
        os.makedirs(self.where)

    def tearDown(self):
        if self.previous_store is None:
            os.environ.pop("SW_DESKTOP_STORE", None)
        else:
            os.environ["SW_DESKTOP_STORE"] = self.previous_store
        super().tearDown()

    def entry(self, name, data):
        folder = os.path.join(self.store, "%s-outer" % name, "%s-inner" % name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "local_%s.json" % name)
        swlib.write_json_atomic(path, data)
        return path

    def test_the_latest_session_in_this_directory_wins(self):
        self.entry("old", {"cliSessionId": "old-one", "cwd": self.where,
                           "lastActivityAt": 1000})
        self.entry("new", {"cliSessionId": "new-one", "cwd": self.where,
                           "lastActivityAt": 2000})
        self.entry("elsewhere", {"cliSessionId": "other-folder",
                                 "cwd": self.home, "lastActivityAt": 9000})
        found = swlib.desktop_session_here(self.where)
        self.assertEqual(found["cliSessionId"], "new-one")

    def test_no_session_in_this_directory_has_no_answer(self):
        self.entry("elsewhere", {"cliSessionId": "other-folder",
                                 "cwd": self.home, "lastActivityAt": 9000})
        self.assertIsNone(swlib.desktop_session_here(self.where))

    def test_records_without_a_session_or_a_directory_are_skipped(self):
        self.entry("nameless", {"cwd": self.where, "lastActivityAt": 5000})
        self.entry("homeless", {"cliSessionId": "no-folder",
                                "lastActivityAt": 5000})
        self.entry("broken", {"cliSessionId": "", "cwd": self.where})
        self.assertIsNone(swlib.desktop_session_here(self.where))

    def test_a_record_with_no_activity_stamp_is_aged_by_its_file(self):
        first = self.entry("first", {"cliSessionId": "first", "cwd": self.where})
        second = self.entry("second", {"cliSessionId": "second",
                                       "cwd": self.where})
        os.utime(first, (1_600_000_000, 1_600_000_000))
        os.utime(second, (1_600_000_100, 1_600_000_100))
        self.assertEqual(swlib.desktop_session_here(self.where)["cliSessionId"],
                         "second")

    def test_no_store_no_answer(self):
        os.environ["SW_DESKTOP_STORE"] = os.path.join(self.home, "gone")
        self.assertIsNone(swlib.desktop_session_here(self.where))

    def test_off_macos_it_never_looks(self):
        self.entry("mine", {"cliSessionId": "mine", "cwd": self.where})
        os.environ.pop("SW_DESKTOP_STORE")
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.object(swlib, "DESKTOP_STORE", self.store):
            self.assertIsNone(swlib.desktop_session_here(self.where))
            with mock.patch.object(sys, "platform", "darwin"):
                self.assertEqual(
                    swlib.desktop_session_here(self.where)["cliSessionId"],
                    "mine")


class RouteCli(Base):
    """scripts/route.py, which is how the chat picker arms a route."""

    SESSION = "9999aaaa-2222-3333-4444-555566667777"

    def setUp(self):
        super().setUp()
        self.store = os.path.join(self.home, "desktop-store")
        self.where = os.path.join(self.home, "project")
        os.makedirs(self.where)

    def run_route(self, *args, store=None, cwd=None):
        environment = dict(os.environ)
        environment["SW_HOME"] = self.home
        environment["SW_DESKTOP_STORE"] = store or os.path.join(self.home, "gone")
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "route.py")] + list(args),
            capture_output=True, text=True, timeout=30, env=environment,
            cwd=cwd or self.where)

    def session_entry(self, session=None):
        folder = os.path.join(self.store, "outer", "inner")
        os.makedirs(folder, exist_ok=True)
        swlib.write_json_atomic(os.path.join(folder, "local_mine.json"),
                                {"cliSessionId": session or self.SESSION,
                                 "cwd": self.where, "lastActivityAt": 2000})
        return self.store

    def route(self, session=None):
        """The armed route: this session's own file, or the global one."""
        return read_json(swlib.route_path(session) if session
                         else swlib.mode_path())

    def assert_nothing_armed(self, message=None):
        self.assertFalse(os.path.exists(swlib.mode_path()), message)
        self.assertEqual(swlib.route_paths(), [], message)

    def test_it_arms_a_route_for_every_session(self):
        self.write_config(codex={"enabled": True, "label": "codex"})
        done = self.run_route("--set", "codex", "--model", "gpt-6.1-sol",
                              "--effort", "high", "--all")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Routing to Codex, model gpt-6.1-sol, effort high, every "
                      "session.", done.stdout)
        self.assertIn("/second-wind off stops it.", done.stdout)
        written = self.route()
        self.assertEqual((written["worker"], written["model"], written["effort"],
                          written["session_id"], written["source"]),
                         ("codex", "gpt-6.1-sol", "high", "*", "chat"))

    def test_here_finds_the_session_in_this_directory(self):
        self.write_config(codex={"enabled": True})
        done = self.run_route("--set", "codex", "--here",
                              store=self.session_entry())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("this session only", done.stdout)
        self.assertEqual(self.route(self.SESSION)["session_id"], self.SESSION)
        self.assertFalse(os.path.exists(swlib.mode_path()),
                         "one session's route must not land in the global file")

    def test_here_falls_back_to_every_session_and_says_so(self):
        self.write_config(codex={"enabled": True})
        done = self.run_route("--set", "codex", "--here")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("this session could not be identified", done.stdout)
        self.assertEqual(self.route()["session_id"], "*")

    def test_a_named_session_and_a_mode(self):
        self.write_config(secondary={"enabled": True, "label": "spare Claude"})
        done = self.run_route("--set", "personal", "--mode", "review",
                              "--session", "given-id")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Routing to spare Claude, review mode", done.stdout)
        written = self.route("given-id")
        self.assertEqual((written["worker"], written["mode"],
                          written["session_id"]),
                         ("secondary", "review", "given-id"))

    def test_an_effort_without_a_model_is_kept_as_an_effort(self):
        # Grok's presets pass --reasoning-effort and no model. Treating the
        # effort word as the model would have sent "--model high".
        self.write_config(grok={"enabled": True})
        done = self.run_route("--set", "grok", "--effort", "high", "--all")
        self.assertEqual(done.returncode, 0, done.stderr)
        written = self.route()
        self.assertIsNone(written["model"])
        self.assertEqual(written["effort"], "high")

    def test_a_worker_that_is_not_connected_is_refused(self):
        self.write_config(codex={"enabled": False})
        done = self.run_route("--set", "codex", "--all")
        self.assertEqual(done.returncode, 1)
        self.assertIn("is not connected", done.stderr)
        self.assert_nothing_armed()

    def test_rubbish_is_refused(self):
        self.write_config(codex={"enabled": True})
        for args in (("--set", "banana", "--all"),
                     ("--set", "codex", "--model", "a b c", "--all"),
                     ("--set", "codex", "--all", "--here")):
            done = self.run_route(*args)
            self.assertNotEqual(done.returncode, 0, args)
            self.assert_nothing_armed(args)

    def test_show_and_clear(self):
        self.write_config(codex={"enabled": True})
        self.run_route("--set", "codex", "--session", "given-id")
        shown = self.run_route("--show", "--session", "given-id").stdout
        self.assertIn("Armed routes: 1", shown)
        self.assertIn("Worker    codex", shown)
        self.assertIn("Session   given-id", shown)
        self.assertIn("Armed by  chat", shown)
        self.assertIn("Age       ", shown)
        self.assertIn("routes/given-id.json", shown)
        self.assertIn("Applies here: yes", shown)
        elsewhere = self.run_route("--show", "--session", "another").stdout
        self.assertIn("Applies here: no, it was armed in another session",
                      elsewhere)
        cleared = self.run_route("--clear", "--session", "given-id")
        self.assertIn("Routing off for this session", cleared.stdout)
        self.assert_nothing_armed()
        self.assertIn("Nothing to clear",
                      self.run_route("--clear", "--session", "given-id").stdout)
        self.assertIn("No route armed", self.run_route("--show").stdout)

    def test_show_lists_every_armed_route(self):
        self.write_config(codex={"enabled": True},
                          secondary={"enabled": True, "label": "spare Claude"})
        self.run_route("--set", "codex", "--session", "chat-one")
        self.run_route("--set", "personal", "--session", "chat-two")
        self.run_route("--set", "personal", "--all")
        shown = self.run_route("--show", "--session", "chat-one").stdout
        self.assertIn("Armed routes: 3", shown)
        for expected in ("Session   chat-one", "Session   chat-two",
                         "Session   every session", "Label     Codex",
                         "Label     spare Claude"):
            self.assertIn(expected, shown)
        self.assertIn("Applies here: yes", shown)
        self.assertIn("Applies here: no, it was armed in another session",
                      shown)
        self.assertIn("Applies here: no, this session's own route takes it",
                      shown)

    def test_two_sessions_are_armed_at_once_and_keep_their_own_route(self):
        self.write_config(codex={"enabled": True},
                          secondary={"enabled": True, "label": "spare Claude"})
        self.run_route("--set", "codex", "--session", "chat-one")
        self.run_route("--set", "personal", "--session", "chat-two")
        self.assertEqual(self.route("chat-one")["worker"], "codex")
        self.assertEqual(self.route("chat-two")["worker"], "secondary")
        self.run_route("--clear", "--session", "chat-one")
        self.assertFalse(os.path.exists(swlib.route_path("chat-one")))
        self.assertEqual(self.route("chat-two")["worker"], "secondary")

    def test_clear_without_a_session_asks_which_rather_than_guessing(self):
        # No desktop store, so nothing can name this session. Removing
        # somebody else's route on a guess is the fault being fixed.
        self.write_config(codex={"enabled": True})
        self.run_route("--set", "codex", "--session", "chat-one")
        done = self.run_route("--clear")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("This session could not be identified", done.stdout)
        self.assertIn("chat-one | Codex | armed", done.stdout)
        self.assertIn("--clear --session ID", done.stdout)
        self.assertTrue(os.path.exists(swlib.route_path("chat-one")))

    def test_clear_all_takes_the_global_route_only(self):
        self.write_config(codex={"enabled": True})
        self.run_route("--set", "codex", "--session", "chat-one")
        self.run_route("--set", "codex", "--all")
        done = self.run_route("--clear", "--all")
        self.assertIn("Routing off for every session", done.stdout)
        self.assertFalse(os.path.exists(swlib.mode_path()))
        self.assertTrue(os.path.exists(swlib.route_path("chat-one")))
        self.assertIn("No route applies to every session",
                      self.run_route("--clear", "--all").stdout)

    def test_clear_here_takes_the_global_route_that_was_routing_here(self):
        self.write_config(codex={"enabled": True})
        self.run_route("--set", "codex", "--all")
        done = self.run_route("--clear", "--session", "chat-one")
        self.assertIn("The route that applied to every session is gone too.",
                      done.stdout)
        self.assert_nothing_armed()

    def test_show_reads_a_bare_word_file(self):
        self.write_config(codex={"enabled": True})
        swlib.write_text_atomic(swlib.mode_path(), "codex\n")
        self.assertIn("applies to every session",
                      self.run_route("--show").stdout)

    def test_destinations_are_printed_for_the_picker(self):
        self.write_config(secondary={"enabled": True, "label": "spare Claude"},
                          codex={"enabled": True, "label": "codex"})
        self.write_usage_live("secondary", age_seconds=60, five_hour_pct=10,
                              seven_day_pct=20)
        out = self.run_route("--destinations").stdout
        self.assertIn("secondary | spare Claude | 80% headroom | 5h 10%, 7d 20%",
                      out)
        self.assertIn("    claude-opus-5-5 high | Opus 5.5, high effort: hard "
                      "reasoning, spends heavily", out)
        self.assertIn("codex | Codex | headroom unknown | no current reading",
                      out)
        self.assertIn("    gpt-6.1-sol medium | GPT-6.1-Sol, medium: everyday "
                      "tasks", out)

    def test_no_destinations_says_so(self):
        self.write_config()
        self.assertIn("No destinations are connected",
                      self.run_route("--destinations").stdout)

    def test_an_unconfigured_machine_is_told_to_run_setup(self):
        done = self.run_route("--set", "codex", "--all")
        self.assertEqual(done.returncode, 1)
        self.assertIn("not set up yet", done.stderr)


class InstalledSettings(Base):
    """What setup actually writes into a profile's settings.json."""

    def profile(self, contents=None):
        folder = os.path.join(self.home, "profile")
        os.makedirs(folder, exist_ok=True)
        swlib.write_json_atomic(os.path.join(folder, "settings.json"),
                                contents if contents is not None else {})
        return folder

    def settings(self, folder):
        return read_json(os.path.join(folder, "settings.json"))

    def test_the_narrowing_matchers_are_written(self):
        folder = self.profile()
        sw_setup.merge_settings(folder, events=("SessionStart", "StopFailure",
                                                "Notification", "PostModelSwitch",
                                                "UserPromptSubmit"),
                                statusline=True)
        hooks = self.settings(folder)["hooks"]
        self.assertEqual([group["matcher"] for group in hooks["StopFailure"]],
                         ["rate_limit"])
        self.assertEqual(
            [group["matcher"] for group in hooks["Notification"]],
            ["quota_auto_resume_fired|quota_auto_resume_stale|"
             "quota_auto_resume_disabled"])
        for event in ("SessionStart", "PostModelSwitch", "UserPromptSubmit"):
            self.assertNotIn("matcher", hooks[event][0], event)
        for event, entry in HOOK_MATCHERS.items():
            self.assertEqual(sw_setup.HOOK_FILES[event][3], entry)

    def test_every_hook_is_installed_by_absolute_path(self):
        folder = self.profile()
        sw_setup.merge_settings(folder, events=tuple(sw_setup.HOOK_FILES))
        hooks = self.settings(folder)["hooks"]
        for event in sw_setup.HOOK_FILES:
            command = hooks[event][0]["hooks"][0]["command"]
            self.assertTrue(os.path.isabs(command), command)
            self.assertEqual(os.path.basename(command),
                             sw_setup.HOOK_FILES[event][0])

    def test_the_routing_hook_is_installed_at_every_level(self):
        for name, level in sw_setup.LEVELS.items():
            folder = self.profile()
            sw_setup.merge_settings(folder, events=level["events"],
                                    statusline=True)
            groups = self.settings(folder)["hooks"]["PreModelSwitch"]
            self.assertEqual(len(groups), 1, name)
            self.assertNotIn("matcher", groups[0], name)
            inner = groups[0]["hooks"][0]
            self.assertEqual(os.path.basename(inner["command"]),
                             "model-route.py", name)
            self.assertTrue(os.access(inner["command"], os.X_OK), name)

    def test_setup_never_creates_the_model_picker_key(self):
        folder = self.profile()
        sw_setup.merge_settings(folder, events=("SessionStart",), statusline=True)
        self.assertNotIn("modelPicker", self.settings(folder))

    def test_uninstall_removes_only_a_marked_model_picker(self):
        folder = self.profile({"modelPicker": {"labels": {"opus": "mine"}}})
        sw_setup.merge_settings(folder, model_picker=False)
        self.assertEqual(self.settings(folder)["modelPicker"],
                         {"labels": {"opus": "mine"}})
        folder = self.profile({"modelPicker": {"_second_wind": True,
                                               "labels": {"opus": "5h 2%"}}})
        sw_setup.merge_settings(folder, model_picker=False)
        self.assertNotIn("modelPicker", self.settings(folder))

    def test_the_usage_panel_keeps_other_plugin_folders(self):
        folder = self.profile({"env": {"CLAUDE_CODE_PLUGIN_DIRS": "~/mods/other",
                                       "KEEP": "1"}})
        sw_setup.merge_settings(folder, usage_panel=True)
        entries = self.settings(folder)["env"]["CLAUDE_CODE_PLUGIN_DIRS"].split(os.pathsep)
        self.assertEqual(entries[0], "~/mods/other")
        self.assertEqual(os.path.basename(entries[1]), "usage-panel")
        sw_setup.merge_settings(folder, usage_panel=True)
        self.assertEqual(len(self.settings(folder)["env"]["CLAUDE_CODE_PLUGIN_DIRS"]
                             .split(os.pathsep)), 2)
        sw_setup.merge_settings(folder, usage_panel=False)
        self.assertEqual(self.settings(folder)["env"],
                         {"CLAUDE_CODE_PLUGIN_DIRS": "~/mods/other", "KEEP": "1"})

    def test_the_usage_panel_leaves_no_empty_env_behind(self):
        folder = self.profile({"theme": "dark"})
        sw_setup.merge_settings(folder, usage_panel=True)
        self.assertTrue(sw_setup.usage_panel_installed(self.settings(folder)))
        sw_setup.merge_settings(folder, usage_panel=False)
        self.assertEqual(self.settings(folder), {"theme": "dark"})

    def test_the_usage_panel_ships_with_the_skill(self):
        self.assertTrue(os.path.isfile(os.path.join(
            sw_setup.usage_panel_dir(), ".claude-plugin", "plugin.json")))


class TrustStore(Base):
    def test_a_claude_profile_reports_its_trust_honestly(self):
        folder = os.path.join(self.home, "profile")
        os.makedirs(folder)
        workdir = os.path.join(self.home, "workdir")
        metadata = os.path.join(folder, ".claude.json")
        self.assertFalse(sw_setup.claude_trusted(folder, workdir))
        swlib.write_json_atomic(metadata, {"projects": {}})
        self.assertFalse(sw_setup.claude_trusted(folder, workdir))
        self.assertEqual(sw_setup.trust_claude(folder, workdir), "trusted")
        self.assertTrue(sw_setup.claude_trusted(folder, workdir))
        # a running session writing its own copy back drops our entry
        swlib.write_json_atomic(metadata, {"projects": {"/elsewhere": {}}})
        self.assertFalse(sw_setup.claude_trusted(folder, workdir))

    def test_codex_reports_its_trust_honestly(self):
        previous_home = sw_setup.HOME
        sw_setup.HOME = self.home
        try:
            workdir = os.path.join(self.home, "workdir")
            self.assertFalse(sw_setup.codex_trusted(workdir))
            os.makedirs(os.path.join(self.home, ".codex"))
            swlib.write_text_atomic(
                os.path.join(self.home, ".codex", "config.toml"), 'model = "x"\n')
            self.assertFalse(sw_setup.codex_trusted(workdir))
            sw_setup.trust_codex(workdir)
            self.assertTrue(sw_setup.codex_trusted(workdir))
        finally:
            sw_setup.HOME = previous_home


class SetupDecisions(Base):
    def test_the_level_mapping_matches_the_contract(self):
        self.assertEqual(sorted(sw_setup.LEVELS), ["relief", "reviewer", "worker"])
        self.assertEqual(sw_setup.LEVELS["reviewer"]["events"],
                         ("SessionStart", "PreModelSwitch"))
        self.assertFalse(sw_setup.LEVELS["reviewer"]["failover"])
        self.assertEqual(sw_setup.LEVELS["reviewer"]["mode"], "review")
        for level in ("worker", "relief"):
            self.assertEqual(sw_setup.LEVELS[level]["mode"], "work")
            for event in ("SessionStart", "StopFailure", "Notification",
                          "PostModelSwitch"):
                self.assertIn(event, sw_setup.LEVELS[level]["events"])
        # Routing from the picker is offered at every level, reviewer included.
        for level in sw_setup.LEVELS:
            self.assertIn("PreModelSwitch", sw_setup.LEVELS[level]["events"])
        self.assertEqual(sw_setup.HOOK_FILES["PreModelSwitch"][0],
                         "model-route.py")
        self.assertEqual(sw_setup.HOOK_FILES["PreModelSwitch"][3], "")
        self.assertNotIn("UserPromptSubmit", sw_setup.LEVELS["worker"]["events"])
        self.assertIn("UserPromptSubmit", sw_setup.LEVELS["relief"]["events"])
        self.assertTrue(sw_setup.LEVELS["relief"]["failover"])

    def test_our_own_entries_are_recognised_and_nobody_else_is(self):
        ours = [
            "/opt/legacy/.claude/bin/usage-guard.sh",
            "~/.claude/bin/session-brief.sh",
            "/opt/second-wind/scripts/statusline.sh",
            "/x/skills/second-wind/scripts/hooks/prompt-guard.py",
            "python3 /x/skills/second-wind/scripts/hooks/session-start.py",
            "/somewhere/hooks/model-switch.py",
        ]
        theirs = [
            "/opt/other/my-hook.sh",
            "/x/hooks/format-on-save.py",
            "echo usage-guard",
            "",
            None,
            # Somebody else's script that happens to sit under a path with our
            # name in it, or to be named after us. A substring rule claimed both.
            "~/notes/second-wind-notes.sh",
            "/x/second-wind/scripts/my-own-thing.py",
            "python3 ~/second-wind/tools/export.py",
        ]
        for command in ours:
            self.assertTrue(sw_setup.is_ours(command), command)
        for command in theirs:
            self.assertFalse(sw_setup.is_ours(command), command)

    def test_stripping_keeps_a_hook_that_is_not_ours(self):
        hooks = {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "/x/usage-guard.sh"}]},
                {"hooks": [{"type": "command", "command": "/opt/other/my-hook.sh"}]},
            ],
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "/x/session-brief.sh"}]},
            ],
        }
        sw_setup.strip_ours(hooks)
        self.assertNotIn("SessionStart", hooks)
        self.assertEqual(len(hooks["UserPromptSubmit"]), 1)
        self.assertEqual(hooks["UserPromptSubmit"][0]["hooks"][0]["command"],
                         "/opt/other/my-hook.sh")

    def test_an_unrelated_hook_named_after_us_survives_uninstall(self):
        # The uninstall path is merge_settings with no events and no status
        # line. A hook that merely lives near us, or borrows the name, is the
        # user's and has to come out the other side untouched.
        folder = os.path.join(self.home, "profile-keep")
        os.makedirs(folder, exist_ok=True)
        mine = sw_setup.hook_command("UserPromptSubmit")
        theirs = os.path.join(self.home, "notes", "second-wind-notes.sh")
        swlib.write_json_atomic(
            os.path.join(folder, "settings.json"),
            {"hooks": {"UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": mine}]},
                {"hooks": [{"type": "command", "command": theirs}]},
            ]}})
        sw_setup.merge_settings(folder)
        left = read_json(os.path.join(folder, "settings.json"))["hooks"]
        commands = [entry["command"] for group in left["UserPromptSubmit"]
                    for entry in group["hooks"]]
        self.assertEqual(commands, [theirs])

    def test_codex_trust_is_appended_once(self):
        previous_home = sw_setup.HOME
        sw_setup.HOME = self.home
        try:
            os.makedirs(os.path.join(self.home, ".codex"))
            path = os.path.join(self.home, ".codex", "config.toml")
            with open(path, "w") as handle:
                handle.write('model = "gpt-5"\n')
            folder = os.path.join(self.home, "workdir")
            self.assertEqual(sw_setup.trust_codex(folder), "trusted")
            self.assertEqual(sw_setup.trust_codex(folder), "already trusted")
            text = read_text(path)
            self.assertEqual(text.count('[projects."%s"]' % folder), 1)
            self.assertIn('model = "gpt-5"', text)
            self.assertIn('trust_level = "trusted"', text)
        finally:
            sw_setup.HOME = previous_home

    def test_a_temporary_sw_home_is_recognised(self):
        os.environ["SW_HOME"] = "/tmp/sw-home-example"
        self.assertTrue(sw_setup.in_temp_home())
        os.environ["SW_HOME"] = os.path.join(swlib.HOME, ".second-wind")
        self.assertFalse(sw_setup.in_temp_home())


class WriteRerun(Base):
    """A second --write must not undo the first one.

    cmd_write is driven through the real parser, so the argparse defaults are
    part of what is under test. Discovery and the version probe are stubbed:
    neither belongs in a unit test, and both would reach for real clients.
    """

    def setUp(self):
        super().setUp()
        self.primary_dir = os.path.join(self.home, "profile-primary")
        self.secondary_dir = os.path.join(self.home, "profile-secondary")
        for folder in (self.primary_dir, self.secondary_dir):
            os.makedirs(folder, exist_ok=True)
        self.real_discover = sw_setup.discover
        self.real_versions = swlib.client_versions
        sw_setup.discover = lambda: {
            "claude_bin": "/usr/local/bin/claude",
            "claude_profiles": [
                {"config_dir": self.primary_dir, "logged_in": True,
                 "account": "user@example.com", "plan": "max"},
                {"config_dir": self.secondary_dir, "logged_in": True,
                 "account": "user@example.com", "plan": "max"},
            ],
            "codex": {"installed": False, "logged_in": False},
            "grok": {"installed": False, "logged_in": False},
            "cursor": {"installed": False, "logged_in": False},
        }
        swlib.client_versions = lambda: {"claude": "2.1.251"}

    def tearDown(self):
        sw_setup.discover = self.real_discover
        swlib.client_versions = self.real_versions
        super().tearDown()

    def write(self, *extra):
        argv = ["--write", "--primary", self.primary_dir,
                "--secondary", self.secondary_dir, "--level", "relief",
                "--no-launchd", "--force"] + list(extra)
        args = sw_setup.build_parser().parse_args(argv)
        out = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errors):
            sw_setup.cmd_write(args)
        return out.getvalue()

    def config(self):
        return read_json(swlib.config_path())

    def settings(self):
        return read_json(os.path.join(self.primary_dir, "settings.json"))

    def test_a_rerun_keeps_the_values_it_was_not_given(self):
        self.write("--timeout", "2400", "--five-hour", "70", "--seven-day", "60",
                   "--refresh-minutes", "45", "--model-picker", "on")
        self.write()
        cfg = self.config()
        self.assertEqual(cfg["timeout_seconds"], 2400)
        self.assertEqual(cfg["thresholds"]["five_hour_pct"], 70)
        self.assertEqual(cfg["thresholds"]["seven_day_pct"], 60)
        self.assertEqual(cfg["refresh"]["interval_minutes"], 45)
        self.assertTrue(cfg["refresh"]["model_picker"])

    def test_picker_routes_are_carried_forward_and_validated(self):
        self.write("--picker-routes",
                   "second-wind/codex/gpt-6.1-sol/high, second-wind/personal")
        self.assertEqual(self.config()["picker"]["routes"],
                         ["second-wind/codex/gpt-6.1-sol/high", "second-wind/personal"])
        self.write()
        self.assertEqual(self.config()["picker"]["routes"],
                         ["second-wind/codex/gpt-6.1-sol/high", "second-wind/personal"])
        text = self.write("--picker-routes", "second-wind/grok")
        self.assertEqual(self.config()["picker"]["routes"], ["second-wind/grok"])
        self.assertIn("picker routes", text)
        with self.assertRaises(SystemExit):
            self.write("--picker-routes", "opus")

    def test_a_first_write_takes_no_picker_routes_or_models(self):
        self.write()
        self.assertEqual(self.config()["picker"], {"routes": [], "models": {}})

    def test_picker_models_are_carried_forward_and_validated(self):
        self.write("--picker-models",
                   "codex=gpt-6.1-sol/high, codex=gpt-6.1-sol/medium, personal=opus/high")
        self.assertEqual(self.config()["picker"]["models"],
                         {"codex": ["gpt-6.1-sol/high", "gpt-6.1-sol/medium"],
                          "secondary": ["opus/high"]})
        self.write()
        self.assertEqual(self.config()["picker"]["models"],
                         {"codex": ["gpt-6.1-sol/high", "gpt-6.1-sol/medium"],
                          "secondary": ["opus/high"]})
        text = self.write("--picker-models", "grok=default")
        self.assertEqual(self.config()["picker"]["models"], {"grok": ["default"]})
        self.assertIn("picker models", text)
        for bad in ("opus/high", "banana=opus/high", "codex=a/b/c"):
            with self.assertRaises(SystemExit, msg=bad):
                self.write("--picker-models", bad)

    def test_a_first_write_still_takes_the_documented_defaults(self):
        self.write()
        cfg = self.config()
        self.assertEqual(cfg["timeout_seconds"], 600)
        self.assertEqual(cfg["thresholds"], {"five_hour_pct": 90,
                                             "seven_day_pct": 80})
        self.assertEqual(cfg["refresh"]["interval_minutes"], 15)
        self.assertFalse(cfg["refresh"]["model_picker"])

    def test_a_changed_value_is_reported_and_an_explicit_one_still_wins(self):
        self.write("--timeout", "2400")
        text = self.write("--timeout", "900")
        self.assertEqual(self.config()["timeout_seconds"], 900)
        self.assertIn("timeout 2400 to 900", text)

    def test_the_account_labels_survive_a_rerun(self):
        text = self.write("--primary-label", "Work Claude",
                          "--secondary-label", "Personal Claude")
        self.assertIn('primary "Work Claude", secondary "Personal Claude"', text)
        self.write()
        cfg = self.config()
        self.assertEqual(cfg["primary"]["label"], "Work Claude")
        self.assertEqual(cfg["secondary"]["label"], "Personal Claude")
        self.assertEqual(swlib.role_label("primary", cfg), "Work Claude")
        self.assertEqual(swlib.route_label("secondary", cfg), "Personal Claude")

    def test_a_first_write_labels_the_accounts_by_role(self):
        self.write()
        cfg = self.config()
        self.assertEqual(cfg["primary"]["label"], "primary")
        self.assertEqual(cfg["secondary"]["label"], "secondary")

    def test_a_changed_label_is_reported_and_an_explicit_one_still_wins(self):
        self.write("--primary-label", "Work Claude")
        text = self.write("--primary-label", "Day job")
        self.assertEqual(self.config()["primary"]["label"], "Day job")
        self.assertIn("primary label Work Claude to Day job", text)

    def test_a_blank_label_falls_back_to_the_role_name(self):
        self.write("--primary-label", "   ")
        self.assertEqual(self.config()["primary"]["label"], "primary")

    def test_model_picker_off_removes_a_marked_key(self):
        swlib.write_json_atomic(
            os.path.join(self.primary_dir, "settings.json"),
            {"modelPicker": {"_second_wind": True, "labels": {"opus": "5h 2%"}},
             "theme": "dark"})
        self.write("--model-picker", "off")
        settings = self.settings()
        self.assertNotIn("modelPicker", settings)
        self.assertEqual(settings["theme"], "dark")
        self.assertFalse(self.config()["refresh"]["model_picker"])

    def test_model_picker_off_leaves_someone_elses_key_alone(self):
        mine = {"labels": {"opus": "my own row"}}
        swlib.write_json_atomic(
            os.path.join(self.primary_dir, "settings.json"), {"modelPicker": mine})
        self.write("--model-picker", "off")
        self.assertEqual(self.settings()["modelPicker"], mine)

    def test_the_usage_panel_is_on_by_default_and_carried(self):
        self.write()
        self.assertTrue(self.config()["refresh"]["usage_panel"])
        self.assertTrue(sw_setup.usage_panel_installed(self.settings()))
        self.write("--usage-panel", "off")
        self.assertFalse(sw_setup.usage_panel_installed(self.settings()))
        self.write()
        self.assertFalse(self.config()["refresh"]["usage_panel"])

    def test_the_usage_panel_switch_alone_touches_nothing_else(self):
        self.write("--usage-panel", "off")
        before = self.settings()
        with contextlib.redirect_stdout(io.StringIO()):
            sw_setup.cmd_usage_panel(True)
        after = self.settings()
        self.assertTrue(sw_setup.usage_panel_installed(after))
        self.assertTrue(self.config()["refresh"]["usage_panel"])
        after.pop("env")
        before.pop("env", None)
        self.assertEqual(after, before)


class RuntimeMirror(Base):
    """The scheduled refresh runs from a mirror inside SW_HOME, because a
    LaunchAgent cannot read a skill installed under ~/Documents."""

    def setUp(self):
        super().setUp()
        self.skill = os.path.join(self.home, "skill")
        self.scripts = os.path.join(self.skill, "scripts")
        os.makedirs(os.path.join(self.scripts, "hooks"))
        for name in swlib.RUNTIME_FILES:
            self.write_source(name, "# %s\n" % name)
        os.chmod(os.path.join(self.scripts, "usage-refresh.sh"), 0o755)
        self.write_source("discover.py", "# not one of the mirrored files\n")
        self.write_source(os.path.join("hooks", "session-start.py"),
                          "# hooks run inside the app\n")

    def write_source(self, name, text):
        path = os.path.join(self.scripts, name)
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def mirrored(self):
        return sorted(os.listdir(swlib.runtime_dir()))

    def test_the_listed_files_are_copied_and_nothing_else(self):
        result = swlib.sync_runtime(self.skill)
        self.assertEqual(sorted(result["copied"]), sorted(swlib.RUNTIME_FILES))
        self.assertEqual(self.mirrored(), sorted(swlib.RUNTIME_FILES))
        self.assertNotIn("discover.py", self.mirrored())
        self.assertNotIn("hooks", self.mirrored())

    def test_the_scripts_folder_can_be_named_directly(self):
        swlib.sync_runtime(self.scripts)
        self.assertEqual(self.mirrored(), sorted(swlib.RUNTIME_FILES))

    def test_the_executable_bit_is_preserved_either_way(self):
        swlib.sync_runtime(self.skill)
        script = os.path.join(swlib.runtime_dir(), "usage-refresh.sh")
        library = os.path.join(swlib.runtime_dir(), "swlib.py")
        self.assertTrue(os.stat(script).st_mode & 0o111)
        self.assertFalse(os.stat(library).st_mode & 0o111)

    def test_the_mirror_is_private(self):
        swlib.sync_runtime(self.skill)
        self.assertEqual(os.stat(swlib.runtime_dir()).st_mode & 0o777, 0o700)

    def test_a_file_already_current_is_not_copied_again(self):
        swlib.sync_runtime(self.skill)
        result = swlib.sync_runtime(self.skill)
        self.assertEqual(result["copied"], [])
        self.assertEqual(sorted(result["skipped"]), sorted(swlib.RUNTIME_FILES))

    def test_a_newer_source_is_copied_again(self):
        swlib.sync_runtime(self.skill)
        source = self.write_source("claude-usage.py", "# edited\n")
        later = time.time() + 10
        os.utime(source, (later, later))
        result = swlib.sync_runtime(self.skill)
        self.assertEqual(result["copied"], ["claude-usage.py"])
        self.assertEqual(read_text(os.path.join(swlib.runtime_dir(),
                                                "claude-usage.py")), "# edited\n")

    def test_a_source_of_a_different_size_is_copied_again(self):
        swlib.sync_runtime(self.skill)
        mirrored = os.path.join(swlib.runtime_dir(), "grok-usage.py")
        stamp = os.stat(mirrored).st_mtime
        source = self.write_source("grok-usage.py", "# longer than it was\n")
        os.utime(source, (stamp, stamp))
        self.assertEqual(swlib.sync_runtime(self.skill)["copied"],
                         ["grok-usage.py"])

    def test_nothing_the_list_does_not_name_is_deleted(self):
        swlib.sync_runtime(self.skill)
        stray = os.path.join(swlib.runtime_dir(), "somebody-elses-file.py")
        swlib.write_text_atomic(stray, "keep me\n")
        swlib.sync_runtime(self.skill)
        self.assertTrue(os.path.exists(stray))

    def test_the_source_is_never_written(self):
        before = {name: os.stat(os.path.join(self.scripts, name)).st_mtime_ns
                  for name in swlib.RUNTIME_FILES}
        swlib.sync_runtime(self.skill)
        swlib.sync_runtime(self.skill)
        after = {name: os.stat(os.path.join(self.scripts, name)).st_mtime_ns
                 for name in swlib.RUNTIME_FILES}
        self.assertEqual(before, after)
        self.assertNotIn("runtime", os.listdir(self.scripts))

    def test_a_missing_source_file_is_reported_and_the_rest_still_copy(self):
        os.unlink(os.path.join(self.scripts, "cursor-usage.py"))
        result = swlib.sync_runtime(self.skill)
        self.assertEqual(result["missing"], ["cursor-usage.py"])
        self.assertNotIn("cursor-usage.py", self.mirrored())
        self.assertIn("usage-refresh.sh", self.mirrored())

    def test_a_source_folder_that_is_not_there_reports_everything_missing(self):
        result = swlib.sync_runtime(os.path.join(self.home, "gone"))
        self.assertEqual(sorted(result["missing"]), sorted(swlib.RUNTIME_FILES))
        self.assertEqual(self.mirrored(), [])

    def test_the_command_line_entry_point_syncs(self):
        done = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "swlib.py"),
             "--sync-runtime", self.skill],
            capture_output=True, text=True,
            env=dict(os.environ, SW_HOME=self.home), timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.mirrored(), sorted(swlib.RUNTIME_FILES))

    def test_the_launchd_agent_runs_the_mirror_not_the_skill_folder(self):
        plist = sw_setup.launchd_plist(900)
        self.assertEqual(plist["ProgramArguments"],
                         ["/bin/sh",
                          os.path.join(swlib.runtime_dir(), "usage-refresh.sh"),
                          "--if-claude-running"])
        self.assertNotIn(SCRIPTS, " ".join(plist["ProgramArguments"]))

    def test_uninstall_removes_the_mirror(self):
        self.write_config(primary={"config_dir": os.path.join(self.home, "gone"),
                                   "label": "primary"})
        swlib.sync_runtime(self.skill)
        with contextlib.redirect_stdout(io.StringIO()):
            sw_setup.cmd_uninstall()
        self.assertFalse(os.path.isdir(swlib.runtime_dir()))


class MirroredRefreshRuns(Base):
    """The proof of the fix: the mirrored refresh script runs with the skill
    folder gone, which is what a launchd agent effectively sees."""

    def test_it_runs_without_reading_the_skill_folder(self):
        skill = os.path.join(self.home, "skill")
        shutil.copytree(SCRIPTS, os.path.join(skill, "scripts"))
        swlib.sync_runtime(skill)
        shutil.rmtree(skill)
        workdir = os.path.join(self.home, "workdir")
        os.makedirs(workdir)
        # Readings off for every role, so this exercises the plan, the lock and
        # the workdir without starting a real client.
        self.write_config(refresh={"interval_minutes": 15, "workdir": workdir,
                                   "primary": False, "model_picker": False})
        done = subprocess.run(
            ["/bin/sh", os.path.join(swlib.runtime_dir(), "usage-refresh.sh")],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, SW_HOME=self.home))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".refresh.lock")))


class TimerSkipsADarkWake(Base):
    """The timer does nothing while the Mac is asleep.

    launchd runs an overdue interval job in a dark wake: the Mac is asleep,
    wakes for seconds of maintenance with no display and often no network,
    and sleeps again. From 9 to 18 October 2026 about two thirds of the
    refreshes started that way failed for Grok, Cursor and the second Claude
    together, Cursor saying it could not even look up its server's name, and
    together they made up most of the refresh failures logged. A dark wake is a
    wake without graphics, which `pmset -g systemstate` names.

    Each run here points the readers at a working directory that does not
    exist, so a run that goes ahead says so in every status file and starts
    no client, and one that is skipped leaves no status at all."""

    AWAKE = "Current System Capabilities are: CPU Graphics Audio Network \n" \
            "Current Power State: 4\n"
    DARK = "Current System Capabilities are: CPU Network \n" \
           "Current Power State: 4\n"

    def run_refresh(self, systemstate, *args):
        bin_dir = os.path.join(self.home, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        with open(os.path.join(bin_dir, "pgrep"), "w") as handle:
            handle.write("#!/bin/sh\nexit 0\n")
        with open(os.path.join(bin_dir, "pmset"), "w") as handle:
            if systemstate is None:
                handle.write("#!/bin/sh\nexit 1\n")
            else:
                handle.write("#!/bin/sh\n[ \"$*\" = '-g systemstate' ] || exit 1\n"
                             "cat <<'EOF'\n%sEOF\n" % systemstate)
        for name in ("pgrep", "pmset"):
            os.chmod(os.path.join(bin_dir, name), 0o755)
        self.write_config(refresh={"interval_minutes": 15,
                                   "workdir": os.path.join(self.home, "gone")})
        return subprocess.run(
            ["/bin/sh", os.path.join(SCRIPTS, "usage-refresh.sh")] + list(args),
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, SW_HOME=self.home,
                     PATH=bin_dir + os.pathsep + os.environ.get("PATH", "")))

    def went_ahead(self):
        return os.path.exists(os.path.join(self.home,
                                           "refresh-status-primary.txt"))

    def test_the_timer_does_nothing_in_a_dark_wake(self):
        done = self.run_refresh(self.DARK, "--if-claude-running")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(self.went_ahead())

    def test_the_timer_reads_while_the_mac_is_awake(self):
        self.run_refresh(self.AWAKE, "--if-claude-running")
        self.assertTrue(self.went_ahead())

    def test_a_power_state_it_cannot_read_does_not_stop_the_timer(self):
        self.run_refresh(None, "--if-claude-running")
        self.assertTrue(self.went_ahead())

    def test_a_refresh_asked_for_by_hand_is_never_skipped(self):
        self.run_refresh(self.DARK, "--force")
        self.assertTrue(self.went_ahead())


# Captured from `launchctl print gui/<uid>/com.second-wind.refresh` on a machine
# where the agent was loaded, with the home directory generalised. Written out
# here rather than run for real, so the comparison is tested without launchd.
LAUNCHCTL_PRINT = """gui/501/com.second-wind.refresh = {
\tactive count = 0
\tpath = /Users/example/Library/LaunchAgents/com.second-wind.refresh.plist
\ttype = LaunchAgent
\tstate = not running

\tprogram = /bin/sh
\targuments = {
\t\t/bin/sh
\t\t%s
\t\t--if-claude-running
\t}

\tstderr path = /Users/example/.second-wind/log/launchd.err
\tdefault environment = {
\t\tPATH => /usr/bin:/bin:/usr/sbin:/sbin
\t}

\trun interval = 900 seconds
\tlast exit code = (never exited)

\tproperties = inferred program
}
"""

MIRROR = "/Users/example/.second-wind/runtime/usage-refresh.sh"


def launchctl_print(script=MIRROR):
    return LAUNCHCTL_PRINT % script


class LaunchdCommand(unittest.TestCase):
    """--check could say the agent was loaded but not what it was running, so a
    job still pointed at a path that has since moved read as healthy."""

    def test_the_argument_vector_is_read(self):
        self.assertEqual(sw_setup.launchd_arguments(launchctl_print()),
                         ["/bin/sh", MIRROR, "--if-claude-running"])

    def test_the_mirror_is_recognised(self):
        self.assertEqual(sw_setup.launchd_command_state(launchctl_print(),
                                                        MIRROR),
                         ("loaded, runs the mirror", True))

    def test_a_path_that_has_moved_is_named_with_the_fix(self):
        stale = launchctl_print(
            "/Users/example/Documents/skills/second-wind/scripts/usage-refresh.sh")
        detail, fresh = sw_setup.launchd_command_state(stale, MIRROR)
        self.assertEqual(detail, "loaded, runs a stale path: rerun --write")
        self.assertIs(fresh, False)

    def test_a_job_with_a_program_and_no_vector_is_read_as_one_argument(self):
        text = "gui/501/x = {\n\tprogram = /bin/sh\n\tstate = not running\n}\n"
        self.assertEqual(sw_setup.launchd_arguments(text), ["/bin/sh"])
        self.assertEqual(
            sw_setup.launchd_command_state(text, MIRROR)[0],
            "loaded, runs a stale path: rerun --write")

    def test_output_it_cannot_read_fails_closed(self):
        for text in ("", "   ", None,
                     "Could not find service \"com.second-wind.refresh\"",
                     "gui/501/x = {\n\targuments = {\n\t\t/bin/sh\n",
                     "gui/501/x = {\n\targuments = {\n\t}\n}\n",
                     "gui/501/x = {\n\tprogram =\n}\n"):
            self.assertIsNone(sw_setup.launchd_arguments(text), text)
            self.assertEqual(sw_setup.launchd_command_state(text, MIRROR),
                             ("loaded, arguments not readable", None), text)

    def test_no_expected_path_is_not_reported_as_a_match(self):
        self.assertEqual(
            sw_setup.launchd_command_state(launchctl_print(), "")[0],
            "loaded, runs a stale path: rerun --write")


class CodexVersionGate(Base):
    """OpenAI offers a new Codex model only to a client new enough to ask for
    it, and refuses an older one in words that blame the account. These are the
    places this machine can show that the codex on PATH is the older one."""

    def setUp(self):
        super().setUp()
        # The real ~/.codex and the real ChatGPT app are both out of bounds.
        self.patches = [mock.patch.object(swlib, "HOME", self.home),
                        mock.patch.object(swlib, "CHATGPT_APP_CODEX",
                                          os.path.join(self.home, "no-app"))]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        super().tearDown()

    def cache(self, folder, version):
        home = os.path.join(self.home, folder)
        os.makedirs(home, exist_ok=True)
        with open(os.path.join(home, "models_cache.json"), "w") as handle:
            json.dump({"client_version": version, "models": []}, handle)
        return home

    def app(self, version):
        """A stand-in for the CLI inside the ChatGPT app, answering --version
        the way the real one does."""
        path = os.path.join(self.home, "app-codex")
        with open(path, "w") as handle:
            handle.write("#!/bin/sh\necho 'codex-cli %s'\n" % version)
        os.chmod(path, 0o755)
        swlib.CHATGPT_APP_CODEX = path

    def roles(self, **folders):
        self.write_config(**{role: {"enabled": True,
                                    "config_dir": os.path.join(self.home, folder)}
                             for role, folder in folders.items()})

    def test_versions_compare_as_numbers_not_as_text(self):
        self.assertGreater(swlib.version_key("0.159.10"),
                           swlib.version_key("0.159.9"))
        self.assertGreater(swlib.version_key("codex-cli 0.160.0"),
                           swlib.version_key("0.159.2"))
        self.assertEqual(swlib.version_key("0.160"), swlib.version_key("0.160.0"))

    def test_a_pre_release_sorts_before_its_release(self):
        self.assertLess(swlib.version_key("0.160.0-alpha.2"),
                        swlib.version_key("0.160.0"))
        self.assertGreater(swlib.version_key("0.160.0-alpha.2"),
                           swlib.version_key("0.159.2"))
        self.assertLess(swlib.version_key("0.160.0-alpha.2"),
                        swlib.version_key("0.160.0-alpha.10"))

    def test_text_without_a_version_has_no_key(self):
        for text in ("", None, "unknown", "codex-cli"):
            self.assertIsNone(swlib.version_key(text), text)

    def test_a_home_fetched_by_a_newer_client_is_named(self):
        self.cache("codex-a", "0.159.2")
        self.cache("codex-b", "0.153.0")
        self.roles(codex="codex-a", codex2="codex-b")
        self.assertEqual(swlib.newer_codex_clients("0.153.0"),
                         [("0.159.2", "~/codex-a/models_cache.json")])

    def test_the_default_home_counts_as_well(self):
        # The desktop apps keep ~/.codex, so a newer client seen there is as
        # good a sign as one in a role's own directory.
        self.cache(".codex", "0.161.0")
        self.roles(codex="codex-a")
        self.assertEqual(swlib.newer_codex_clients("0.159.2"),
                         [("0.161.0", "~/.codex/models_cache.json")])

    def test_the_cli_inside_the_chatgpt_app_is_asked_its_version(self):
        self.roles(codex="codex-a")
        self.app("0.162.0")
        self.assertEqual(swlib.newer_codex_clients("0.159.2"),
                         [("0.162.0", "the ChatGPT app")])

    def test_the_newest_comes_first(self):
        self.cache("codex-a", "0.160.0")
        self.roles(codex="codex-a")
        self.app("0.162.0")
        self.assertEqual([version for version, _ in
                          swlib.newer_codex_clients("0.153.0")],
                         ["0.162.0", "0.160.0"])

    def test_the_same_or_older_everywhere_is_nothing_to_report(self):
        self.cache("codex-a", "0.159.2")
        self.cache(".codex", "0.153.0")
        self.roles(codex="codex-a")
        self.app("0.159.2")
        self.assertEqual(swlib.newer_codex_clients("0.159.2"), [])

    def test_a_version_with_words_round_it_is_reported_bare(self):
        # The update line is built from this, and "codex-cli 0.160.0" would
        # reach npm as two broken arguments.
        self.cache("codex-a", "codex-cli 0.160.0")
        self.roles(codex="codex-a")
        self.assertEqual(swlib.newer_codex_clients("0.159.2"),
                         [("0.160.0", "~/codex-a/models_cache.json")])

    def test_a_cache_that_cannot_be_read_is_skipped(self):
        home = os.path.join(self.home, "codex-a")
        os.makedirs(home)
        with open(os.path.join(home, "models_cache.json"), "w") as handle:
            handle.write("{not json")
        self.roles(codex="codex-a")
        self.assertEqual(swlib.newer_codex_clients("0.153.0"), [])

    def test_an_unknown_installed_version_is_compared_with_nothing(self):
        self.cache(".codex", "0.161.0")
        self.roles(codex="codex-a")
        self.assertEqual(swlib.newer_codex_clients(""), [])

    def test_the_update_goes_through_the_npm_beside_codex(self):
        # npm -g installs into the prefix of whichever npm runs it, so the line
        # names the folder PATH found codex in, not whatever npm comes first.
        codex = npm_install(os.path.join(self.home, "node"), "@openai/codex", "codex")
        self.assertEqual(
            swlib.codex_update_command("0.160.0", codex),
            'PATH="$HOME/node/bin:$PATH" npm install -g @openai/codex@0.160.0')

    def test_an_npm_beside_a_homebrew_codex_does_not_make_it_npms(self):
        # Homebrew keeps its links and its own npm in one folder, so an npm
        # there proves nothing; the link has to lead into npm's node_modules.
        codex = brew_install(os.path.join(self.home, "homebrew"), "codex", "codex")
        self.assertEqual(swlib.codex_update_command("0.160.0", codex),
                         "brew upgrade codex")

    def test_a_folder_name_the_shell_would_expand_is_kept_literal(self):
        # Inside double quotes sh still expands $, ` and \, so a folder named
        # $node would send the update to some other directory.
        codex = npm_install(os.path.join(self.home, "$node"), "@openai/codex", "codex")
        folder = os.path.dirname(codex)
        line = swlib.codex_update_command("0.160.0", codex)
        self.assertEqual(line, 'PATH="$HOME/\\$node/bin:$PATH" npm install -g '
                               '@openai/codex@0.160.0')
        # And sh agrees: the PATH it builds starts with the real folder.
        shown = subprocess.run(
            ["/bin/sh", "-c", line.split(" npm ")[0] + '; printf %s "${PATH%%:*}"'],
            capture_output=True, text=True, env={"HOME": self.home, "PATH": "/x"})
        self.assertEqual(shown.stdout, folder)

    def test_with_no_npm_beside_codex_the_update_is_the_plain_npm_line(self):
        folder = os.path.join(self.home, "bin")
        os.makedirs(folder)
        open(os.path.join(folder, "codex"), "w").close()
        self.assertEqual(
            swlib.codex_update_command("0.160.0", os.path.join(folder, "codex")),
            "npm install -g @openai/codex@0.160.0")


class ClaudeVersionGate(Base):
    """Anthropic refuses a model newer than the claude on PATH, and an older
    client's aliases keep their old targets. The desktop app keeps its own
    builds of Claude Code current, so a newer one there is the sign."""

    def setUp(self):
        super().setUp()
        # The real desktop app's builds are out of bounds.
        self.patch = mock.patch.object(swlib, "HOME", self.home)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        super().tearDown()

    def build(self, version, verified=True):
        folder = os.path.join(self.home, swlib.CLAUDE_DESKTOP_BUILDS, version)
        os.makedirs(os.path.join(folder, "claude.app"))
        if verified:
            open(os.path.join(folder, ".verified"), "w").close()

    def test_a_newer_build_in_the_desktop_app_is_named_newest_first(self):
        self.build("2.1.281")
        self.build("2.1.284")
        self.assertEqual(swlib.newer_claude_clients("2.1.273"),
                         [("2.1.284", "the Claude desktop app"),
                          ("2.1.281", "the Claude desktop app")])

    def test_a_download_the_app_has_not_finished_checking_is_ignored(self):
        self.build("2.1.290", verified=False)
        self.assertEqual(swlib.newer_claude_clients("2.1.285"), [])

    def test_the_same_or_older_is_nothing_to_report(self):
        self.build("2.1.284")
        self.assertEqual(swlib.newer_claude_clients("2.1.285"), [])

    def test_no_desktop_app_is_nothing_to_report(self):
        self.assertEqual(swlib.newer_claude_clients("2.1.273"), [])

    def test_an_unknown_installed_version_is_compared_with_nothing(self):
        self.build("2.1.284")
        self.assertEqual(swlib.newer_claude_clients(""), [])

    def test_two_folders_for_one_version_name_the_app_once(self):
        self.build("2.1.284")
        self.build("2.1.284 copy")
        self.assertEqual(swlib.newer_claude_clients("2.1.273"),
                         [("2.1.284", "the Claude desktop app")])

    def test_the_update_goes_through_the_npm_beside_claude(self):
        claude = npm_install(os.path.join(self.home, "node"),
                             "@anthropic-ai/claude-code", "claude")
        self.assertEqual(
            swlib.claude_update_command("2.1.284", claude),
            'PATH="$HOME/node/bin:$PATH" npm install -g '
            '@anthropic-ai/claude-code@2.1.284')

    def test_a_homebrew_claude_is_upgraded_by_homebrew(self):
        claude = brew_install(os.path.join(self.home, "homebrew"), "claude-code",
                              "claude")
        self.assertEqual(swlib.claude_update_command("2.1.284", claude),
                         "brew upgrade claude-code")

    def test_a_native_install_is_asked_for_the_version_itself(self):
        # `claude update` only moves to the newest build on its channel, which
        # may not be the version the warning names; `claude install` takes one.
        folder = os.path.join(self.home, "bin")
        os.makedirs(folder)
        open(os.path.join(folder, "claude"), "w").close()
        self.assertEqual(
            swlib.claude_update_command("2.1.284", os.path.join(folder, "claude")),
            "claude install 2.1.284")


class Check(Base):
    """--check has to answer the questions the tool cannot answer for itself, so
    the two rows added here are tested for what they actually say."""

    def setUp(self):
        super().setUp()
        self.profile = os.path.join(self.home, "profile-primary")
        os.makedirs(self.profile)
        self.skill = os.path.join(self.home, "skill")
        self.scripts = os.path.join(self.skill, "scripts")
        os.makedirs(self.scripts)
        for name in swlib.RUNTIME_FILES:
            with open(os.path.join(self.scripts, name), "w") as handle:
                handle.write("# %s\n" % name)
        self.real_versions = swlib.client_versions
        self.real_loaded = sw_setup.launchd_loaded
        self.real_print = sw_setup.launchd_print
        swlib.client_versions = lambda: {"claude": "2.1.259"}
        sw_setup.launchd_loaded = lambda: True
        # No launchctl in a test. The loaded job runs the mirror unless a test
        # says otherwise.
        self.printed = lambda: launchctl_print(
            os.path.join(swlib.runtime_dir(), "usage-refresh.sh"))
        sw_setup.launchd_print = lambda: self.printed()

    def tearDown(self):
        swlib.client_versions = self.real_versions
        sw_setup.launchd_loaded = self.real_loaded
        sw_setup.launchd_print = self.real_print
        super().tearDown()

    def configure(self, **overrides):
        settings = {"skill_dir": self.skill,
                    "primary": {"config_dir": self.profile, "label": "primary"},
                    "tested_versions": {"claude": "2.1.251"}}
        settings.update(overrides)
        return self.write_config(**settings)

    def check(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            sw_setup.cmd_check()
        return out.getvalue()

    def test_the_mirror_row_says_stale_then_in_sync(self):
        self.configure()
        first = self.check()
        self.assertIn("runtime mirror", first)
        self.assertIn("copied in just now", first)
        second = self.check()
        self.assertIn("present and in sync (%d files)" % len(swlib.RUNTIME_FILES),
                      second)

    def test_a_file_the_mirror_cannot_get_is_named(self):
        self.configure()
        self.check()
        os.unlink(os.path.join(swlib.runtime_dir(), "model-picker.py"))
        os.unlink(os.path.join(self.scripts, "model-picker.py"))
        text = self.check()
        self.assertIn("STALE: not mirrored: model-picker.py", text)
        self.assertIn("not in the runtime mirror: model-picker.py", text)

    def test_a_missing_refresh_script_is_a_fault_when_the_agent_is_installed(self):
        self.configure(refresh={"interval_minutes": 15, "launchd": True})
        os.unlink(os.path.join(self.scripts, "usage-refresh.sh"))
        text = self.check()
        self.assertIn("the launchd agent has nothing to run", text)
        self.assertIn("NOT ARMED", text)

    @unittest.skipUnless(sys.platform == "darwin", "launchd is macOS only")
    def test_the_loaded_job_is_reported_as_running_the_mirror(self):
        self.configure(refresh={"interval_minutes": 15, "launchd": True})
        text = self.check()
        self.assertIn("loaded, runs the mirror", text)

    @unittest.skipUnless(sys.platform == "darwin", "launchd is macOS only")
    def test_a_loaded_job_running_a_path_that_moved_is_a_fault(self):
        self.configure(level="relief",
                       refresh={"interval_minutes": 15, "launchd": True})
        self.printed = lambda: launchctl_print(
            os.path.join(self.scripts, "usage-refresh.sh"))
        text = self.check()
        self.assertIn("loaded, runs a stale path: rerun --write", text)
        self.assertIn("runs a path that is not the mirror", text)
        self.assertIn("NOT ARMED", text)

    @unittest.skipUnless(sys.platform == "darwin", "launchd is macOS only")
    def test_output_that_cannot_be_parsed_says_so_and_raises_nothing(self):
        self.configure(refresh={"interval_minutes": 15, "launchd": True})
        self.printed = lambda: "Could not find service"
        text = self.check()
        self.assertIn("loaded, arguments not readable", text)
        self.assertNotIn("stale path", text)

    def test_a_newer_client_than_the_tested_one_is_stated_plainly(self):
        self.configure()
        text = self.check()
        self.assertIn("2.1.259 installed, tested against 2.1.251", text)

    def test_the_same_client_version_says_so(self):
        self.configure(tested_versions={"claude": "2.1.259"})
        self.assertIn("2.1.259 installed, tested against the same", self.check())

    def codex_cache(self, version, folder="codex-a"):
        home = os.path.join(self.home, folder)
        os.makedirs(home)
        with open(os.path.join(home, "models_cache.json"), "w") as handle:
            json.dump({"client_version": version, "models": []}, handle)
        return home

    def check_codex(self, version):
        """--check with a codex of this version first on PATH and the npm that
        installed it beside it, and with the real ~/.codex and ChatGPT app
        swapped for nothing."""
        folder = os.path.dirname(npm_install(os.path.join(self.home, "node"),
                                             "@openai/codex", "codex"))
        swlib.client_versions = lambda: {"claude": "2.1.259", "codex": version}
        path = folder + os.pathsep + os.environ.get("PATH", "")
        with mock.patch.dict(os.environ, {"PATH": path}), \
                mock.patch.object(swlib, "HOME", self.home), \
                mock.patch.object(swlib, "CHATGPT_APP_CODEX",
                                  os.path.join(self.home, "no-app")):
            return self.check()

    def test_a_codex_older_than_one_seen_here_is_a_warning_with_the_fix(self):
        self.configure(codex={"enabled": True,
                              "config_dir": self.codex_cache("0.159.2")})
        text = self.check_codex("0.153.0")
        self.assertIn("BEHIND: 0.153.0 on PATH, 0.159.2 in "
                      "~/codex-a/models_cache.json", text)
        self.assertIn("not supported when using Codex with a ChatGPT account", text)
        self.assertIn('PATH="$HOME/node/bin:$PATH" npm install -g '
                      '@openai/codex@0.159.2', text)

    def test_a_codex_as_new_as_any_seen_here_says_so(self):
        self.configure(codex={"enabled": True,
                              "config_dir": self.codex_cache("0.159.2")})
        text = self.check_codex("0.159.2")
        self.assertIn("0.159.2 on PATH, no newer client seen", text)
        self.assertNotIn("npm install -g @openai/codex", text)

    def check_claude(self, version):
        """--check with a claude of this version first on PATH and the npm that
        installed it beside it, and the real desktop app's builds swapped for a
        folder of our own."""
        folder = os.path.dirname(npm_install(os.path.join(self.home, "node"),
                                             "@anthropic-ai/claude-code", "claude"))
        swlib.client_versions = lambda: {"claude": version}
        path = folder + os.pathsep + os.environ.get("PATH", "")
        with mock.patch.dict(os.environ, {"PATH": path}), \
                mock.patch.object(swlib, "HOME", self.home):
            return self.check()

    def desktop_build(self, version):
        folder = os.path.join(self.home, swlib.CLAUDE_DESKTOP_BUILDS, version)
        os.makedirs(folder)
        open(os.path.join(folder, ".verified"), "w").close()

    def personal_worker(self):
        profile = os.path.join(self.home, "profile-secondary")
        os.makedirs(profile)
        return {"enabled": True, "config_dir": profile, "label": "secondary"}

    def test_a_claude_older_than_the_desktop_apps_is_a_warning_with_the_fix(self):
        self.desktop_build("2.1.284")
        self.configure(secondary=self.personal_worker())
        text = self.check_claude("2.1.273")
        self.assertIn("BEHIND: 2.1.273 on PATH, 2.1.284 in the Claude desktop app",
                      text)
        self.assertIn("version it needs", text)
        self.assertIn('PATH="$HOME/node/bin:$PATH" npm install -g '
                      '@anthropic-ai/claude-code@2.1.284', text)

    def test_a_claude_as_new_as_the_desktop_apps_says_so(self):
        self.desktop_build("2.1.284")
        self.configure(secondary=self.personal_worker())
        text = self.check_claude("2.1.285")
        self.assertIn("2.1.285 on PATH, no newer client seen", text)
        self.assertNotIn("npm install -g @anthropic-ai/claude-code", text)

    def test_no_claude_worker_means_no_claude_gate_row(self):
        self.desktop_build("2.1.284")
        self.configure()
        text = self.check_claude("2.1.273")
        self.assertNotIn("claude version gate", text)

    def test_no_codex_worker_means_no_gate_row(self):
        # A newer client sits in the default home, so the row would be BEHIND
        # if the gate ran without a Codex worker to run it for.
        self.codex_cache("0.159.2", folder=".codex")
        self.configure()
        text = self.check_codex("0.153.0")
        self.assertNotIn("codex version gate", text)
        self.assertNotIn("npm install -g @openai/codex", text)


class VerifyOutput(unittest.TestCase):
    """--verify exists so a reading can be compared against its own panel.

    What it must get right is which lines of a panel say what a window holds,
    for four clients that draw them four ways, and it must carry a model's
    window through rather than flattening it into the plan's figures.
    """

    def extract(self, name):
        with open(os.path.join(FIXTURES, name + ".txt")) as handle:
            return sw_setup._panel_extract(handle.read())[0]

    def test_a_named_bar_keeps_the_name_above_it(self):
        # Claude draws a heading and then a bar with no words of its own.
        lines = self.extract("claude-2.1.251")
        self.assertIn("Current week (all models)", lines)
        self.assertTrue(any("35% used" in line for line in lines))

    def test_a_line_that_names_its_own_window_borrows_no_label(self):
        # Codex names the window on the line that carries the figure, so the
        # line above it is not part of the window and is not shown as one.
        lines = self.extract("codex-0.152.1")
        self.assertTrue(any("Weekly limit" in line and "87% left" in line
                            for line in lines))
        self.assertFalse(any("Session:" in line for line in lines))

    def test_a_heading_that_carries_no_figure_is_kept(self):
        # The Codex heading that caused the fault this command exists to catch.
        # Without it the panel shows two Weekly lines and no way to tell them
        # apart.
        panel = ("  Weekly limit: [###] 74% left (resets 09:48 on 22 Sep)\n"
                 "  GPT-5.3-Codex-Spark limit:\n"
                 "  5h limit: [###] 100% left (resets 17:28)\n")
        self.assertIn("GPT-5.3-Codex-Spark limit:",
                      sw_setup._panel_extract(panel)[0])

    def test_a_bar_between_a_name_and_its_figure_does_not_become_the_name(self):
        # Grok puts the bar on its own line, so the line above the figure has
        # no words in it and is not what names the window.
        panel = ("Weekly limit (X Premium+)\n"
                 "\u2588\u2588\u2591\u2591\u2591\u2591\n"
                 "6%\n")
        self.assertIn("Weekly limit (X Premium+)",
                      sw_setup._panel_extract(panel)[0])

    def test_a_bar_drawn_the_width_of_the_terminal_is_shown_short(self):
        # Cursor draws a bar hundreds of characters wide. Printed as it comes,
        # it wraps the comparison this command exists to make.
        for line in self.extract("cursor-2026.08.31-4057e58"):
            self.assertLess(len(line), 60, line)

    def test_the_reading_line_states_every_window_it_holds(self):
        said = sw_setup._read_line({
            "plan": "Pro Lite", "five_hour_pct": None, "seven_day_pct": 26,
            "five_hour_resets": None, "seven_day_resets": "09:48 on 22 Sep",
            "extra": {"model_weeks": {"GPT-5.3-Codex-Spark": {"pct": 0}}}})
        self.assertIn("plan Pro Lite", said)
        self.assertIn("5-hour none", said)
        self.assertIn("weekly 26% used, resets 09:48 on 22 Sep", said)
        # The window that caused the fault this command exists to catch: it is
        # named, and it is not the weekly figure.
        self.assertIn("GPT-5.3-Codex-Spark week 0% used", said)


if __name__ == "__main__":
    unittest.main()
