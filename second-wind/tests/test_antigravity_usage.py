import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import swlib  # noqa: E402

spec = importlib.util.spec_from_file_location("antigravity_usage", SCRIPTS / "antigravity-usage.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def envelope(groups, status="SUCCESS"):
    return json.dumps({"status": status, "response": "table", "error": "" if status == "SUCCESS" else "boom",
                       "command": {"name": "usage", "data": {"groups": groups}}})


PRO = [
    {"name": "Gemini Models", "buckets": [
        {"id": "gemini-weekly", "window": "weekly", "remaining_fraction": 0.75, "reset_time": "2030-01-08T10:00:00Z"},
        {"id": "gemini-5h", "window": "5h", "remaining_fraction": 0.5, "reset_time": "2030-01-01T15:00:00Z"}]},
    {"name": "Claude and GPT models", "buckets": [
        {"window": "weekly", "remaining_fraction": 1, "reset_time": "2030-01-08T10:00:00Z"}]},
    {"name": "Gemini 4 Argon", "buckets": [
        {"window": "weekly", "remaining_fraction": 0.9, "reset_time": "2030-01-08T10:00:00Z"}]},
]

FAKE = """#!{python}
import os, sys
from pathlib import Path
leader = "SESSION=own" if os.getsid(0) == os.getpid() else "SESSION=inherited"
import stat
stdin = ("STDIN=pipe" if stat.S_ISFIFO(os.fstat(0).st_mode) else "STDIN=other") + ("-empty" if sys.stdin.read() == "" else "-data")
Path(os.environ["AGY_MARK"]).write_text(os.environ["HOME"] + " " + " ".join(sys.argv[1:]) + " SSH=" + os.environ.get("SSH_CONNECTION", "none") + " " + leader + " " + stdin)
assert "GEMINI_API_KEY" not in os.environ
reply = Path(os.environ["AGY_REPLY"]).read_text()
if reply.startswith("STDERR:"):
    sys.stderr.write(reply[len("STDERR:"):])
    sys.exit(1)
sys.stdout.write(reply)
"""


class Parse(unittest.TestCase):
    def test_used_is_one_minus_remaining_per_window(self):
        pools = reader.parse(envelope(PRO))
        self.assertEqual(pools[0]["name"], "Gemini Models")
        self.assertEqual({b["window"]: b["used_pct"] for b in pools[0]["buckets"]}, {"weekly": 25.0, "5h": 50.0})
        self.assertEqual(pools[2]["buckets"][0]["used_pct"], 10.0)

    def test_error_status_and_missing_pools_are_failures(self):
        with self.assertRaisesRegex(ValueError, "boom"):
            reader.parse(envelope(PRO, status="ERROR"))
        with self.assertRaisesRegex(ValueError, "no usage pools"):
            reader.parse(envelope([]))
        with self.assertRaisesRegex(ValueError, "no JSON"):
            reader.parse("Please sign in")


class Reader(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "second-home"
        self.home.mkdir()
        fake = self.tmp / "agy"
        fake.write_text(FAKE.format(python=sys.executable))
        fake.chmod(0o755)
        self.reply = self.tmp / "reply.json"
        self.reply.write_text(envelope(PRO))
        self.mark = self.tmp / "mark"
        config = {"version": 4, "accounts": {
            "ag": {"kind": "antigravity", "label": "Antigravity", "binary": str(fake)},
            "ag-work": {"kind": "antigravity", "label": "Antigravity-work", "binary": str(fake), "home": str(self.home)}}}
        (self.tmp / "config.json").write_text(json.dumps(config))
        self.env = mock.patch.dict(os.environ, {"SW_HOME": str(self.tmp), "AGY_MARK": str(self.mark),
                                                "AGY_REPLY": str(self.reply), "GEMINI_API_KEY": "x"})
        self.env.start()
        self.addCleanup(self.env.stop)
        # The real probe would reach Google from a unit test; the Mac is online unless a test says otherwise.
        probe = mock.patch.object(reader, "online", return_value=True)
        self.probe = probe.start()
        self.addCleanup(probe.stop)

    def run_reader(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = reader.main(list(argv))
        return code, out.getvalue()

    def test_reading_is_saved_privately_and_uses_the_account_home(self):
        code, out = self.run_reader("--account", "ag-work")
        self.assertEqual(code, 0, out)
        self.assertIn("OK ag-work Gemini Models: 5h 50% used, week 25% used", out)
        self.assertTrue(self.mark.read_text().startswith(str(self.home) + " -p /usage --output-format json --log-file "))
        self.assertNotIn("SSH=none", self.mark.read_text())
        self.assertIn("agy-usage-ag-work.log", self.mark.read_text())
        self.run_reader("--account", "ag")
        self.assertIn("SSH=none", self.mark.read_text())
        path = Path(swlib.antigravity_usage_path("ag-work"))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text())["groups"][0]["name"], "Gemini Models")

    def test_failure_is_saved_and_reported(self):
        self.reply.write_text("Error: Please sign in to view available models.")
        code, out = self.run_reader("--account", "ag")
        self.assertEqual(code, 1)
        self.assertIn("FAILED: ag", out)
        self.assertIn("not signed in", swlib.load_antigravity_usage("ag")["error"])

    def test_agy_gets_a_closed_pipe_as_stdin_so_it_never_opens_a_browser(self):
        # agy 1.5.3 starts its browser sign-in whenever silent auth fails and
        # stdin is not a pipe; /dev/null from the launchd timer put a Google
        # sign-in page on screen every fifteen minutes while the Mac was offline.
        self.run_reader("--account", "ag")
        self.assertIn("SESSION=own", self.mark.read_text())
        self.assertIn("STDIN=pipe-empty", self.mark.read_text())
        self.reply.write_text("STDERR:Error: authentication required. Run 'agy' to log in, then retry.\n")
        code, out = self.run_reader("--account", "ag-work")
        self.assertEqual(code, 1)
        error = swlib.load_antigravity_usage("ag-work")["error"]
        self.assertIn("not signed in", error)
        self.assertIn("HOME=%s" % self.home, error)
        self.assertIn("never open a browser", error)

    def test_offline_keeps_the_last_reading_and_does_not_run_agy(self):
        self.run_reader("--account", "ag")
        before = swlib.load_antigravity_usage("ag")
        self.mark.unlink()
        self.probe.return_value = False
        code, out = self.run_reader("--account", "ag", "--if-older", "0")
        self.assertEqual(code, 0)
        self.assertIn("SKIPPED ag: offline, kept the last reading", out)
        self.assertFalse(self.mark.exists())
        self.assertEqual(swlib.load_antigravity_usage("ag"), before)
        self.probe.return_value = True
        code, out = self.run_reader("--account", "ag", "--if-older", "0")
        self.assertEqual(code, 0)
        self.assertTrue(self.mark.exists())

    def test_young_reading_is_kept_without_running_agy(self):
        self.run_reader("--account", "ag")
        self.mark.unlink()
        code, out = self.run_reader("--account", "ag", "--if-older", "10")
        self.assertEqual(code, 0)
        self.assertIn("cached", out)
        self.assertFalse(self.mark.exists())

    def test_panel_rows_show_gemini_pool_and_note_the_others(self):
        self.run_reader()
        lines = swlib.brief_card(now=time.time())
        text = "\n".join(lines)
        row = next(line for line in lines if line.startswith("Antigravity "))
        self.assertIn(swlib.usage_bar(50.0) + "  50%", row)
        self.assertIn(swlib.usage_bar(25.0) + "  25%", row)
        self.assertIn("Gemini 4 Argon week 10%", text)
        self.assertNotIn("Claude and GPT models", text)  # untouched pools are left off
        self.assertIn("resets ", text)

    def test_panel_marks_missing_window_and_failed_reading(self):
        self.reply.write_text(envelope([PRO[0] | {"buckets": PRO[0]["buckets"][:1]}]))
        self.run_reader("--account", "ag")
        self.reply.write_text("not json")
        self.run_reader("--account", "ag-work")
        lines = swlib.brief_card(now=time.time())
        row = next(line for line in lines if line.startswith("Antigravity "))
        self.assertIn(swlib.usage_bar(None) + "  n/a", row)
        self.assertTrue(any(line.startswith("Antigravity-work") and "usage reading failed" in line for line in lines))


class Wiring(unittest.TestCase):
    def test_reader_is_mirrored_for_the_scheduled_refresh(self):
        self.assertIn("antigravity-usage.py", swlib.RUNTIME_FILES)
        self.assertIn("antigravity-usage.py", (SCRIPTS / "usage-refresh.sh").read_text())

    def test_timer_keeps_an_antigravity_reading_for_an_hour(self):
        # Every run starts agy's server and its background updater, so the timer
        # does not re-read every fifteen minutes; the runner refreshes after a job.
        self.assertIn("older=60", (SCRIPTS / "usage-refresh.sh").read_text())


if __name__ == "__main__":
    unittest.main()
