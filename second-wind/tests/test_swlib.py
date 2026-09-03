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

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts")
sys.path.insert(0, SCRIPTS)
import swlib  # noqa: E402
import setup as sw_setup  # noqa: E402

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
        found = swlib.parse_route("second-wind/codex/gpt-5.6/high", self.cfg)
        self.assertEqual((found["worker"], found["model"], found["effort"]),
                         ("codex", "gpt-5.6", "high"))
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
                   "second-wind/codex/gpt-5.6/high, second-wind/personal")
        self.assertEqual(self.config()["picker"]["routes"],
                         ["second-wind/codex/gpt-5.6/high", "second-wind/personal"])
        self.write()
        self.assertEqual(self.config()["picker"]["routes"],
                         ["second-wind/codex/gpt-5.6/high", "second-wind/personal"])
        text = self.write("--picker-routes", "second-wind/grok")
        self.assertEqual(self.config()["picker"]["routes"], ["second-wind/grok"])
        self.assertIn("picker routes", text)
        with self.assertRaises(SystemExit):
            self.write("--picker-routes", "opus")

    def test_a_first_write_takes_no_picker_routes(self):
        self.write()
        self.assertEqual(self.config()["picker"], {"routes": []})

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
        swlib.client_versions = lambda: {"claude": "2.1.259"}
        sw_setup.launchd_loaded = lambda: True

    def tearDown(self):
        swlib.client_versions = self.real_versions
        sw_setup.launchd_loaded = self.real_loaded
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

    def test_a_newer_client_than_the_tested_one_is_stated_plainly(self):
        self.configure()
        text = self.check()
        self.assertIn("2.1.259 installed, tested against 2.1.251", text)

    def test_the_same_client_version_says_so(self):
        self.configure(tested_versions={"claude": "2.1.259"})
        self.assertIn("2.1.259 installed, tested against the same", self.check())


if __name__ == "__main__":
    unittest.main()
