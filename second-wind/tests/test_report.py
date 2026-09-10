#!/usr/bin/env python3
"""Tests for the report and for the exchange cap it reports on.

Two things here are load bearing. The share file goes to someone else, so the
redaction has to hold for the shapes a real log throws at it. And the cap
decides what survives on disk, so its arithmetic is checked rather than eyeballed.
"""
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
import truncate  # noqa: E402
import report  # noqa: E402


class ShareFilePrivacy(unittest.TestCase):
    """The shared report describes how the accounts were reached, not the work:
    no directory names, no reply text, unless --with-details asks for them."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="sw-report-")
        self.previous = os.environ.get("SW_HOME")
        os.environ["SW_HOME"] = self.home
        self.logdir = os.path.join(self.home, "log")
        os.makedirs(self.logdir)
        import swlib
        swlib.write_json_atomic(swlib.config_path(), {
            "version": 4, "level": "relief",
            "primary": {"config_dir": "~/.claude"},
            "codex": {"enabled": True},
            "log": {"dir": self.logdir},
            "field_notes": {"enabled": True},
        })
        self.exchange = os.path.join(self.logdir, "20260101-000000-1-codex-SecretProject.md")
        with open(self.exchange, "w") as handle:
            handle.write("# Delegated to codex\n\n## Prompt sent\n\nthe prompt\n\n"
                         "## Reply\n\nTHE WORKER WROTE THIS ABOUT THE CLIENT\n")
        self.rows = [{"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "worker": "codex", "mode": "review", "exit": 1, "duration_s": 9,
                      "cwd": "/somewhere/SecretProject", "exchange": self.exchange,
                      "prompt_bytes": 100, "reply_bytes": 40}]
        swlib.field_note("note", text="had to use the standard login flow")
        swlib.field_note("setup", command="check", args=["--check"], result=0)
        self.path = os.path.join(self.home, "report.md")

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("SW_HOME", None)
        else:
            os.environ["SW_HOME"] = self.previous
        shutil.rmtree(self.home, ignore_errors=True)

    def read(self):
        with open(self.path) as handle:
            return handle.read()

    def test_a_label_and_a_reader_error_path_never_leave_by_default(self):
        import swlib
        swlib.field_note("write", level="relief", workers=["codex"],
                         changed=["codex label Old Client to New Client", "codex label"])
        swlib.write_text_atomic(swlib.status_path("codex"),
                                "FAILED: budget exhausted. Last of the screen: /Volumes/ClientA/notes\n")
        report.share_file(self.rows, 7, self.logdir, path=self.path)
        text = self.read()
        self.assertNotIn("Old Client", text)
        self.assertNotIn("ClientA", text)
        self.assertIn("- codex: failed", text)
        self.assertNotIn("Log directory", text)
        report.share_file(self.rows, 7, self.logdir, path=self.path, details=True)
        self.assertIn("Last of the screen", self.read())

    def test_by_default_no_project_name_and_no_reply_text(self):
        report.share_file(self.rows, 7, self.logdir, path=self.path)
        text = self.read()
        self.assertNotIn("SecretProject", text)
        self.assertNotIn("THE WORKER WROTE", text)
        self.assertIn("What this file contains, and what it does not", text)
        self.assertIn("## Field notes", text)
        self.assertIn("NOTE: had to use the standard login flow", text)
        self.assertIn("setup --check", text)
        self.assertIn("1 failed", text)

    def test_with_details_puts_them_back(self):
        report.share_file(self.rows, 7, self.logdir, path=self.path, details=True)
        text = self.read()
        self.assertIn("SecretProject", text)
        self.assertIn("THE WORKER WROTE", text)


class Redaction(unittest.TestCase):
    def test_a_timezone_in_a_reset_time_becomes_local_time(self):
        self.assertEqual(report.redact("week 25% / Sep 14 at 2:59am (Africa/Johannesburg)"),
                         "week 25% / Sep 14 at 2:59am (local time)")
        self.assertEqual(report.redact("resets (America/Argentina/Buenos_Aires)"),
                         "resets (local time)")
        self.assertEqual(report.redact("(not a zone) and (UTC)"), "(not a zone) and (UTC)")

    def test_email_becomes_account(self):
        self.assertEqual(report.redact("signed in as someone@example.com now"),
                         "signed in as <account> now")

    def test_every_email_goes_not_only_the_first(self):
        text = "a.person+tag@mail.example.com and other_1@sub.example.com"
        self.assertEqual(report.redact(text), "<account> and <account>")

    def test_home_directory_becomes_tilde(self):
        home = os.path.expanduser("~")
        text = "logged: %s/.second-wind/log/2026-09.jsonl" % home
        self.assertEqual(report.redact(text),
                         "logged: ~/.second-wind/log/2026-09.jsonl")

    def test_both_in_one_line(self):
        home = os.path.expanduser("~")
        out = report.redact("%s/.claude holds me@example.com" % home)
        self.assertEqual(out, "~/.claude holds <account>")
        self.assertNotIn(home, out)

    def test_email_inside_a_path_still_goes(self):
        out = report.redact("/tmp/cache/me@example.com/session.json")
        self.assertNotIn("@example.com", out)
        self.assertIn("<account>", out)

    def test_no_email_left_after_redacting_a_realistic_block(self):
        home = os.path.expanduser("~")
        block = "\n".join([
            "- primary: OK (2m ago)",
            "account first.last@example.com plan max",
            "exchange %s/.second-wind/log/x.md" % home,
            "LOGIN EXPIRED: sign in as ops@example.com",
        ])
        out = report.redact(block)
        self.assertNotIn("@", out.replace("<account>", ""))
        self.assertEqual(out.count("<account>"), 2)

    def test_empty_and_none_are_safe(self):
        self.assertEqual(report.redact(""), "")
        self.assertEqual(report.redact(None), "")

    def test_the_accounts_table_keeps_its_column_widths(self):
        row = "secondary  someone@example.com  max        ready"
        out = report.keep_columns(row)
        self.assertNotIn("@", out)
        self.assertEqual(len(out), len(row))
        self.assertTrue(out.startswith("secondary  <account>"))

    def test_an_address_shorter_than_the_placeholder_widens_the_line(self):
        # Nothing can be trimmed to make room, so the column shifts: the
        # placeholder is never cut down to fit. Assembled from pieces because
        # any address written out whole in this repo has to sit under
        # example.com, and no address that short can.
        short = "a@" + "t" + ".co"
        row = "primary  " + short + "  max"
        out = report.keep_columns(row)
        self.assertIn("<account>", out)
        self.assertEqual(len(out), len(row) + len("<account>") - len(short))

    def test_an_address_in_a_sentence_is_not_padded(self):
        out = report.keep_columns("Work should go to: secondary "
                                  "(someone@example.com)")
        self.assertTrue(out.endswith("(<account>)"))

    def test_ordinary_text_is_untouched(self):
        self.assertEqual(report.redact("exit 0, 12s, review mode"),
                         "exit 0, 12s, review mode")


class LimitArithmetic(unittest.TestCase):
    def test_kilobytes_become_bytes(self):
        self.assertEqual(truncate.limit_bytes(200), 200 * 1024)
        self.assertEqual(truncate.limit_bytes("50"), 50 * 1024)

    def test_nonsense_falls_back_to_the_default(self):
        for value in ("", None, "abc", 0, -5):
            self.assertEqual(truncate.limit_bytes(value),
                             truncate.DEFAULT_MAX_KB * 1024)


class Timestamps(unittest.TestCase):
    """The log stamps UTC. Parsing it as local time put every summer row an
    hour out, which is enough to move one across a day cutoff."""

    def setUp(self):
        self.previous = os.environ.get("TZ")

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.previous
        if hasattr(time, "tzset"):
            time.tzset()

    def test_a_stamp_round_trips_on_both_sides_of_a_dst_boundary(self):
        if not hasattr(time, "tzset"):
            self.skipTest("this platform cannot switch TZ in process")
        for zone in ("Europe/London", "America/New_York", "UTC"):
            os.environ["TZ"] = zone
            time.tzset()
            for stamp in ("2026-07-15T12:00:00Z", "2026-01-15T12:00:00Z"):
                parsed = report.parse_ts(stamp)
                self.assertEqual(
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(parsed)),
                    stamp, "%s in %s" % (stamp, zone))

    def test_summer_and_winter_stamps_are_exactly_half_a_year_apart(self):
        # The old arithmetic used the standard-time offset for both, so the
        # summer stamp came out an hour adrift of the winter one.
        summer = report.parse_ts("2026-07-01T00:00:00Z")
        winter = report.parse_ts("2026-01-01T00:00:00Z")
        self.assertEqual(summer - winter, 181 * 86400)

    def test_nonsense_is_zero_rather_than_an_exception(self):
        for value in ("", "not a date", None, "2026-07-15 12:00:00"):
            self.assertEqual(report.parse_ts(value), 0)


class Truncation(unittest.TestCase):
    def test_a_short_reply_is_left_exactly_as_it_came(self):
        data = b"one line of reply\n"
        body, cut = truncate.truncate(data, 1024)
        self.assertEqual(body, data)
        self.assertFalse(cut)

    def test_a_reply_exactly_at_the_limit_is_not_cut(self):
        data = b"x" * 1024
        body, cut = truncate.truncate(data, 1024)
        self.assertFalse(cut)
        self.assertEqual(len(body), 1024)

    def test_a_long_reply_is_cut_and_stays_inside_the_limit(self):
        data = b"y" * 50_000
        body, cut = truncate.truncate(data, 4096)
        self.assertTrue(cut)
        self.assertLessEqual(len(body), 4096)

    def test_both_ends_survive(self):
        data = b"HEAD" + b"m" * 50_000 + b"TAIL"
        body, cut = truncate.truncate(data, 4096)
        self.assertTrue(cut)
        self.assertTrue(body.startswith(b"HEAD"))
        self.assertTrue(body.endswith(b"TAIL"))

    def test_the_split_is_three_quarters_head_one_quarter_tail(self):
        data = bytes(range(256)) * 400          # 102400 bytes, no marker text in it
        limit = 8192
        body, _ = truncate.truncate(data, limit)
        marker_at = body.find(b"[second-wind:")
        self.assertGreater(marker_at, 0)
        head = body[:marker_at]
        tail = body[body.find(b"omitted here]") + len(b"omitted here]"):]
        kept = len(head) + len(tail.strip())
        # 75/25 of the budget, allowing for the marker and its blank lines
        self.assertAlmostEqual(len(head) / float(kept), 0.75, delta=0.02)

    def test_the_marker_names_the_number_of_bytes_dropped(self):
        data = b"z" * 30_000
        body, _ = truncate.truncate(data, 2048)
        text = body.decode("utf-8")
        start = text.index("[second-wind: ") + len("[second-wind: ")
        stated = int(text[start:text.index(" bytes omitted here]")])
        kept = len(body) - len((truncate.MARKER % stated).encode("utf-8"))
        # what the marker claims was dropped plus what is still there is the
        # size the worker actually sent
        self.assertEqual(stated + kept, len(data))
        self.assertGreater(stated, 0)

    def test_the_marker_text_is_the_agreed_wording(self):
        self.assertEqual(truncate.MARKER_TEXT % 42,
                         "[second-wind: 42 bytes omitted here]")

    def test_a_cut_never_leaves_half_a_character(self):
        # every character is three bytes, so a naive slice lands mid-character
        data = ("中" * 20_000).encode("utf-8")
        body, cut = truncate.truncate(data, 4096)
        self.assertTrue(cut)
        body.decode("utf-8")          # raises if a cut split a character

    def test_a_limit_smaller_than_the_marker_still_says_what_happened(self):
        body, cut = truncate.truncate(b"q" * 5000, 8)
        self.assertTrue(cut)
        self.assertIn(b"bytes omitted here]", body)


class TruncateCommandLine(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sw-truncate-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.infile = os.path.join(self.dir, "full")
        self.outfile = os.path.join(self.dir, "body")

    def run_it(self, data, max_kb):
        with open(self.infile, "wb") as handle:
            handle.write(data)
        done = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "truncate.py"),
             self.infile, self.outfile, str(max_kb)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_it_reports_the_full_size_not_the_kept_size(self):
        data = b"w" * 300_000
        out = self.run_it(data, 1)
        self.assertEqual(out["reply_bytes"], len(data))
        self.assertTrue(out["truncated"])
        self.assertLessEqual(out["kept_bytes"], 1024)
        self.assertEqual(os.path.getsize(self.outfile), out["kept_bytes"])

    def test_a_small_reply_passes_through_whole(self):
        data = b"the whole reply\n"
        out = self.run_it(data, 200)
        self.assertFalse(out["truncated"])
        self.assertEqual(out["reply_bytes"], len(data))
        with open(self.outfile, "rb") as handle:
            self.assertEqual(handle.read(), data)

    def test_the_body_file_is_not_world_readable(self):
        self.run_it(b"private\n", 200)
        self.assertEqual(os.stat(self.outfile).st_mode & 0o077, 0)

    def test_a_missing_input_file_fails_loudly(self):
        done = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "truncate.py"),
             os.path.join(self.dir, "nope"), self.outfile, "200"],
            capture_output=True, text=True, timeout=60)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("cannot read", done.stderr)


class LogDirectory(unittest.TestCase):
    """The runner and the report have to agree on where the log is, including
    for a config written before `log.dir` existed."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sw-report-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.old = os.environ.get("SW_HOME")
        os.environ["SW_HOME"] = self.dir
        self.addCleanup(self.restore)

    def restore(self):
        if self.old is None:
            os.environ.pop("SW_HOME", None)
        else:
            os.environ["SW_HOME"] = self.old

    def write_config(self, cfg):
        with open(os.path.join(self.dir, "config.json"), "w") as handle:
            json.dump(cfg, handle)

    def test_new_key_wins(self):
        self.write_config({"log": {"dir": "~/somewhere/new"},
                           "log_dir": "~/somewhere/old"})
        self.assertEqual(report.log_dir(),
                         os.path.expanduser("~/somewhere/new"))

    def test_old_key_still_read(self):
        self.write_config({"log_dir": "~/somewhere/old"})
        self.assertEqual(report.log_dir(),
                         os.path.expanduser("~/somewhere/old"))

    def test_neither_key_falls_back_under_sw_home(self):
        self.write_config({})
        self.assertEqual(report.log_dir(), os.path.join(self.dir, "log"))


class Summary(unittest.TestCase):
    def test_nothing_delegated_reads_as_a_pass(self):
        lines = report.summary_lines([], 7)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("PASS"))

    def test_a_failure_makes_it_a_warning_and_names_the_exchange(self):
        rows = [{"ts": "2026-09-02T10:00:00Z", "worker": "codex", "mode": "work",
                 "exit": 1, "duration_s": 5, "cwd": "/tmp/sw-test",
                 "exchange": "/tmp/sw-test/gone.md"}]
        text = "\n".join(report.summary_lines(rows, 7))
        self.assertTrue(text.startswith("WARN"))
        self.assertIn("gone.md", text)

    def test_a_capped_exchange_is_called_out(self):
        rows = [{"ts": "2026-09-02T10:00:00Z", "worker": "codex", "mode": "work",
                 "exit": 0, "duration_s": 5, "cwd": "/tmp/sw-test",
                 "exchange": __file__, "truncated": True, "reply_bytes": 900_000}]
        text = "\n".join(report.summary_lines(rows, 7))
        self.assertIn("capped on disk", text)


if __name__ == "__main__":
    unittest.main()
