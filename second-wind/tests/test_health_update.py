"""The health report, the update command and the installer's newer behaviour."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "second-wind" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import swlib  # noqa: E402
import health  # noqa: E402


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.previous = os.environ.get("SW_HOME")
        os.environ["SW_HOME"] = str(self.home)
        self.cfg = {"version": 4,
                    "primary": {"label": "Work Claude", "config_dir": "~/.claude",
                                "account": "work@example.com"},
                    "codex": {"enabled": True, "label": "Codex", "account": "user@example.com"},
                    "cursor": {"enabled": True, "label": "Cursor", "account": "user@example.com"}}
        (self.home / "config.json").write_text(json.dumps(self.cfg))
        self.now = time.time()

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("SW_HOME", None)
        else:
            os.environ["SW_HOME"] = self.previous
        self.temp.cleanup()

    def reading(self, role, age=10, **fields):
        data = {"cached_at": int(self.now) - age, "account": self.cfg[role]["account"]}
        data.update(fields)
        Path(swlib.usage_path(role)).write_text(json.dumps(data))

    def status(self, role, text):
        Path(swlib.status_path(role)).write_text(text + "\n")

    def failures(self, role, count, days_ago=5):
        log = self.home / "log" / "refresh-failures.log"
        log.parent.mkdir(exist_ok=True)
        with log.open("a") as handle:
            for index in range(count):
                stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                      time.gmtime(self.now - days_ago * 86400 + index * 900))
                handle.write("%s %s FAILED: something\n" % (stamp, role))

    def found(self, **kwargs):
        return health.findings(self.cfg, self.now, deep=False, **kwargs)

    def by_who(self, who):
        return [item for item in self.found() if item["who"] == who]

    def test_everything_read_and_fine_is_healthy(self):
        for role in ("primary", "codex", "cursor"):
            self.reading(role, seven_day_pct=10, five_hour_pct=5)
            self.status(role, "OK")
        self.assertEqual(self.found(), [])
        lines, _ = health.report(self.cfg, self.now, deep=False)
        self.assertIn("healthy", lines[0])
        self.assertIn("FINE: Work Claude, Codex, Cursor", lines)

    def test_a_signed_out_account_names_the_command_and_how_long(self):
        self.reading("cursor", age=11 * 86400, included_pct=3)
        self.failures("cursor", 40)
        self.status("cursor", "LOGIN EXPIRED: run cursor-agent in a terminal and sign in again.")
        item = self.by_who("Cursor")[0]
        self.assertEqual((item["level"], item["code"]), ("fix", "login"))
        self.assertIn("40 failed readings", item["what"])
        self.assertIn("cursor-agent", item["fix"])
        self.assertIn("user@example.com", item["fix"])

    def test_an_older_reader_that_only_saw_the_sign_in_screen_is_still_a_login(self):
        self.status("cursor", "FAILED: usage panel did not appear. Last of the screen: "
                              "Cursor Agent v2026.10.19 Press any key to log in...")
        self.assertEqual(self.by_who("Cursor")[0]["code"], "login")

    def test_codex_login_names_its_own_home(self):
        self.cfg["codex"]["config_dir"] = "~/.codex-two"
        self.status("codex", "LOGIN EXPIRED: sign in again.")
        self.assertIn("CODEX_HOME=~/.codex-two codex login", self.by_who("Codex")[0]["fix"])

    def test_a_crashed_reader_is_our_fault_and_says_so(self):
        self.status("codex", "FAILED: ModuleNotFoundError: No module named 'card_json'")
        item = self.by_who("Codex")[0]
        self.assertEqual((item["level"], item["code"]), ("fix", "reader-crashed"))
        self.assertIn("update.py", item["fix"])

    def test_any_python_error_is_a_crash_and_a_vendor_sentence_is_not(self):
        for message in ("FAILED: KeyError: 'five_hour'", "FAILED: AttributeError: 'NoneType' has no x",
                        "FAILED: Traceback (most recent call last):"):
            self.assertEqual(health.classify_failure(message)[1], "reader-crashed", message)
        for message in ("FAILED: usage panel did not appear. Last of the screen: Error: no",
                        "FAILED: codex is not on PATH."):
            self.assertNotEqual(health.classify_failure(message)[1], "reader-crashed", message)

    def test_the_primary_is_signed_in_through_its_reader_profile(self):
        self.cfg["reader"] = {"enabled": True, "config_dir": "~/.claude-usage"}
        self.status("primary", "LOGIN EXPIRED: run claude in a terminal for ~/.claude-usage and sign in again.")
        fix = self.by_who("Work Claude")[0]["fix"]
        self.assertIn("CLAUDE_CONFIG_DIR=~/.claude-usage claude auth login", fix)

    def test_an_antigravity_sign_in_command_keeps_its_case(self):
        self.cfg["accounts"] = {"agy2": {"kind": "antigravity", "label": "Gemini two",
                                         "home": "~/.second-wind/antigravity/two"}}
        Path(swlib.antigravity_usage_path("agy2")).write_text(json.dumps({
            "read_at": self.now, "error": "not signed in; sign in once in a terminal with "
            "HOME=/x/two SSH_CONNECTION='127.0.0.1 0 127.0.0.1 0' agy (unattended runs never open a browser)"}))
        item = self.by_who("Gemini two")[0]
        self.assertEqual(item["level"], "fix")
        self.assertIn("HOME=/x/two SSH_CONNECTION='127.0.0.1 0 127.0.0.1 0' agy", item["fix"])
        self.assertTrue(item["fix"].startswith("Sign in once"))

    def test_no_fix_asks_the_user_to_define_a_variable(self):
        self.status("codex", "PARSER MISMATCH: labels moved")
        self.status("cursor", "FAILED: KeyError: 'x'")
        self.reading("primary", age=5 * 3600)
        runtime = Path(swlib.runtime_dir())
        runtime.mkdir()
        fixes = " ".join(item["fix"] for item in self.found())
        self.assertNotIn("$SW", fixes)
        self.assertIn("update.py", fixes)
        self.assertIn(str(SCRIPTS).replace(os.path.expanduser("~"), "$HOME"), fixes)

    def test_a_rate_limit_is_a_note_with_nothing_to_do(self):
        self.status("codex", "FAILED: Usage endpoint is rate limited until 11:30am.")
        item = self.by_who("Codex")[0]
        self.assertEqual((item["level"], item["code"]), ("note", "rate-limited"))

    def test_a_spent_account_is_a_note_with_its_nearest_reset(self):
        self.reading("codex", five_hour_pct=100, seven_day_pct=70,
                     five_hour_resets="13:13", seven_day_resets="00:39 on 12 Nov")
        self.status("codex", "OK")
        item = self.by_who("Codex")[0]
        self.assertEqual(item["code"], "spent")
        self.assertIn("13:13", item["what"])

    def test_a_changed_sign_in_is_a_fix(self):
        self.reading("codex", seven_day_pct=10, account="someone-else@example.com")
        self.status("codex", "OK")
        self.assertEqual(self.by_who("Codex")[0]["code"], "sign-in-changed")

    def test_an_incomplete_runtime_mirror_is_reported(self):
        runtime = Path(swlib.runtime_dir())
        runtime.mkdir()
        (runtime / "swlib.py").write_text("")
        codes = [item["code"] for item in self.found()]
        self.assertIn("mirror-incomplete", codes)

    def test_brief_names_what_needs_the_user_every_time_and_two_at_most(self):
        self.status("cursor", "LOGIN EXPIRED: sign in again.")
        first = health.brief(self.cfg, self.now)
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0].startswith("Needs you: Cursor"))
        self.assertEqual(health.brief(self.cfg, self.now + 60), first)
        self.status("codex", "LOGIN EXPIRED: sign in again.")
        self.status("primary", "LOGIN EXPIRED: sign in again.")
        self.assertEqual(len(health.brief(self.cfg, self.now)), 2)

    def test_brief_never_raises_and_notes_are_not_in_it(self):
        self.status("codex", "FAILED: Usage endpoint is rate limited.")
        self.assertEqual(health.brief(self.cfg, self.now), [])
        self.assertEqual(health.brief({"version": 4, "primary": "nonsense"}, self.now), [])

    def test_tidy_removes_only_surplus_copies_of_our_own(self):
        profile = self.home / "profile"
        profile.mkdir()
        self.cfg["primary"]["config_dir"] = str(profile)
        (self.home / "config.json").write_text(json.dumps(self.cfg))
        settings = profile / "settings.json"
        settings.write_text("{}")
        (profile / "settings.json.second-wind-original").write_text("first")
        for day in range(10, 22):
            (profile / ("settings.json.second-wind-backup-202601%d-000000" % day)).write_text("x")
        (profile / "settings.json.bak").write_text("the user's own")
        stray = self.home / "usage-primary.json.tmp.123"
        stray.write_text("")
        os.utime(stray, (self.now - 2 * 86400,) * 2)
        self.assertEqual(health.tidy(self.cfg), 12 - swlib.BACKUPS_KEPT + 1)
        left = sorted(path.name for path in profile.iterdir())
        self.assertEqual(len([name for name in left if "backup-2026" in name]), swlib.BACKUPS_KEPT)
        self.assertIn("settings.json.second-wind-original", left)
        self.assertIn("settings.json.bak", left)
        self.assertFalse(stray.exists())

    def test_command_line_exits_one_when_something_needs_the_user(self):
        self.status("cursor", "LOGIN EXPIRED: sign in again.")
        done = subprocess.run([sys.executable, str(SCRIPTS / "health.py"), "--quick", "--json"],
                              capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(done.returncode, 1, done.stderr)
        data = json.loads(done.stdout)
        self.assertFalse(data["healthy"])
        via_runner = subprocess.run([sys.executable, str(SCRIPTS / "second_wind.py"), "health", "--quick"],
                                    capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(via_runner.returncode, 1, via_runner.stderr)
        self.assertIn("NEEDS YOU", via_runner.stdout)


@unittest.skipUnless(shutil.which("git"), "git is needed to test updating")
class UpdateTests(unittest.TestCase):
    """A real remote, a real checkout and a real copied install, all in a temp folder."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="user@example.com",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="user@example.com")
        for name in ("SW_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
            self.env.pop(name, None)
        self.remote = self.root / "remote.git"
        self.author = self.root / "author"
        self.checkout = self.root / "checkout"
        self.author.mkdir()
        for name in ("install.py", "install.sh"):
            shutil.copy2(ROOT / name, self.author / name)
        skill = self.author / "second-wind"
        skill.mkdir()
        for name in ("SKILL.md", "VERSION"):
            shutil.copy2(ROOT / "second-wind" / name, skill / name)
        shutil.copytree(SCRIPTS, skill / "scripts", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.git(self.author, "init", "-q", "-b", "main")
        self.commit("first")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True, env=self.env)
        self.git(self.author, "remote", "add", "origin", str(self.remote))
        self.git(self.author, "push", "-q", "-u", "origin", "main")
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.checkout)], check=True, env=self.env)
        self.skill = self.home / ".claude" / "skills" / "second-wind"

    def tearDown(self):
        self.temp.cleanup()

    def git(self, folder, *args):
        return subprocess.run(["git", "-C", str(folder), *args], check=True, env=self.env,
                              capture_output=True, text=True).stdout

    def commit(self, subject):
        self.git(self.author, "add", "-A")
        self.git(self.author, "commit", "-q", "-m", subject)

    def publish(self, version, subject):
        (self.author / "second-wind" / "VERSION").write_text(version + "\n")
        self.commit(subject)
        self.git(self.author, "push", "-q", "origin", "main")

    def install(self, *flags):
        return subprocess.run([sys.executable, str(self.checkout / "install.py"), "--claude", *flags],
                              capture_output=True, text=True, env=self.env)

    def update(self, *flags):
        return subprocess.run([sys.executable, str(self.skill / "scripts" / "update.py"), *flags],
                              capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL)

    def test_a_copied_install_is_checked_then_updated(self):
        self.assertEqual(self.install().returncode, 0)
        self.assertFalse(self.skill.is_symlink())
        result = self.update("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("up to date", result.stdout)
        self.publish("9999.01.01", "Read the new panel")
        result = self.update("--check")
        self.assertIn("1 update available", result.stdout)
        self.assertIn("Read the new panel", result.stdout)
        check = json.loads((self.home / ".second-wind" / "update-check.json").read_text())
        self.assertEqual(check["behind"], 1)
        # nothing installed by a check, and no terminal means no silent update
        self.assertNotEqual((self.skill / "VERSION").read_text().strip(), "9999.01.01")
        self.assertEqual(self.update().returncode, 2)
        result = self.update("--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.skill / "VERSION").read_text().strip(), "9999.01.01")
        self.assertTrue((self.home / ".second-wind" / "backups" / "previous-install-claude" / "SKILL.md").is_file())
        self.assertIn("up to date", self.update("--check").stdout)

    def test_a_linked_install_moves_with_its_checkout(self):
        self.assertEqual(self.install("--link").returncode, 0)
        self.publish("9999.02.02", "Second change")
        result = self.update("--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("linked to the checkout", result.stdout)
        self.assertEqual((self.skill / "VERSION").read_text().strip(), "9999.02.02")

    def test_a_checkout_with_local_changes_is_left_alone(self):
        self.install("--link")
        self.publish("9999.03.03", "Third change")
        (self.checkout / "install.sh").write_text("# edited by hand\n")
        result = self.update("--yes")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not committed", result.stderr)
        self.assertEqual((self.checkout / "install.sh").read_text(), "# edited by hand\n")

    def test_a_branch_with_no_upstream_says_so(self):
        self.install("--link")
        self.git(self.checkout, "checkout", "-q", "-b", "experiment")
        result = self.update("--check")
        self.assertEqual(result.returncode, 2)
        self.assertIn("follows no remote branch", result.stderr)

    def test_a_copy_left_behind_its_checkout_is_found_and_reinstalled(self):
        self.assertEqual(self.install().returncode, 0)
        self.publish("9999.04.04", "Fourth change")
        self.git(self.checkout, "pull", "-q")  # by hand: the checkout is now current, the copy is not
        result = self.update("--check")
        self.assertIn("older than its checkout", result.stdout)
        self.assertNotIn("up to date", result.stdout)
        result = self.update("--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.skill / "VERSION").read_text().strip(), "9999.04.04")
        self.assertIn("up to date", self.update("--check").stdout)

    def test_a_copy_whose_checkout_is_gone_fetches_a_fresh_one(self):
        self.assertEqual(self.install().returncode, 0)
        shutil.rmtree(self.checkout)
        self.publish("9999.05.05", "Fifth change")
        # the remains of a clone that was cut short
        junk = self.home / ".second-wind" / "source"
        junk.mkdir(parents=True)
        (junk / "half").write_text("")
        self.assertEqual(self.update("--check").returncode, 2)
        result = self.update("--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.skill / "VERSION").read_text().strip(), "9999.05.05")
        self.assertTrue((junk / "install.py").is_file())

    def test_a_check_that_cannot_reach_the_repository_keeps_what_it_knew(self):
        self.install("--link")
        self.publish("9999.06.06", "Sixth change")
        self.update("--check")
        check = self.home / ".second-wind" / "update-check.json"
        self.assertEqual(json.loads(check.read_text())["behind"], 1)
        self.remote.rename(self.root / "moved.git")
        self.assertEqual(self.update("--check", "--quiet").returncode, 0)
        after = json.loads(check.read_text())
        self.assertEqual(after["behind"], 1)
        self.assertTrue(after["error"])

    def test_uninstall_never_deletes_a_checkout_in_the_skills_folder(self):
        cloned = self.home / ".claude" / "skills" / "second-wind"
        cloned.parent.mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", str(self.remote), str(cloned)], check=True, env=self.env)
        (cloned / "my-work.txt").write_text("not committed")
        done = subprocess.run([sys.executable, str(cloned / "install.py"), "--uninstall", "--yes"],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Left alone", done.stdout)
        self.assertEqual((cloned / "my-work.txt").read_text(), "not committed")
        # and neither a folder of somebody else's that only has the name
        other = self.home / ".codex" / "skills" / "second-wind"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text("---\nname: something-else\n---\n")
        subprocess.run([sys.executable, str(self.checkout / "install.py"), "--uninstall", "--yes"],
                       capture_output=True, text=True, env=self.env)
        self.assertTrue(other.exists())

    def test_installer_dry_run_and_uninstall(self):
        dry = self.install("--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("Would install", dry.stdout)
        self.assertFalse(self.skill.exists())
        self.assertEqual(self.install().returncode, 0)
        record = json.loads((self.home / ".second-wind" / "install.json").read_text())
        self.assertEqual(record["installs"]["claude"]["path"], str(self.skill))
        self.assertEqual(record["source"], str(self.checkout))
        refused = subprocess.run([sys.executable, str(self.checkout / "install.py"), "--uninstall"],
                                 capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL)
        self.assertEqual(refused.returncode, 2)
        self.assertTrue(self.skill.exists())
        gone = subprocess.run([sys.executable, str(self.checkout / "install.py"), "--uninstall", "--yes"],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(gone.returncode, 0, gone.stderr)
        self.assertFalse(self.skill.exists())
        self.assertTrue((self.checkout / "second-wind" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
