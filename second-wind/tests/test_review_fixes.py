"""One test per fault found in the 10 November 2026 review, so none returns."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import swlib  # noqa: E402
import second_wind as sw  # noqa: E402
import setup as sw_setup  # noqa: E402


def load(name):
    """A script whose file name has a hyphen, as a module."""
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Home(unittest.TestCase):
    """SW_HOME pointed at a temporary folder for the length of a test."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sw_home = self.root / "sw"
        self.sw_home.mkdir()
        self.previous = os.environ.get("SW_HOME")
        os.environ["SW_HOME"] = str(self.sw_home)

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("SW_HOME", None)
        else:
            os.environ["SW_HOME"] = self.previous
        self.temp.cleanup()

    def config(self, data):
        (self.sw_home / "config.json").write_text(json.dumps(data))
        return data

    def reading(self, role, age=10, **fields):
        data = {"cached_at": int(time.time()) - age}
        data.update(fields)
        Path(swlib.usage_path(role)).write_text(json.dumps(data))


class RuntimeMirror(Home):
    def test_every_sibling_a_mirrored_script_imports_is_mirrored(self):
        siblings = {path.stem for path in SCRIPTS.glob("*.py")}
        for name in swlib.RUNTIME_FILES:
            if not name.endswith(".py"):
                continue
            tree = ast.parse((SCRIPTS / name).read_text())
            wanted = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    wanted.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    wanted.add(node.module.split(".")[0])
            for module in sorted(wanted & siblings):
                self.assertIn(module + ".py", swlib.RUNTIME_FILES,
                              "%s imports %s, which the runtime mirror lacks" % (name, module))

    def test_each_reader_starts_from_the_mirror_alone(self):
        swlib.sync_runtime(str(SCRIPTS.parent))
        runtime = Path(swlib.runtime_dir())
        for name in ("claude-usage.py", "codex-status.py", "grok-usage.py",
                     "cursor-usage.py", "antigravity-usage.py"):
            done = subprocess.run([sys.executable, str(runtime / name), "--help"],
                                  cwd=str(runtime), capture_output=True, text=True,
                                  env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            self.assertEqual(done.returncode, 0, "%s: %s" % (name, done.stderr[-300:]))

    def test_a_crashed_reader_reports_the_last_line_of_its_error(self):
        text = (SCRIPTS / "usage-refresh.sh").read_text()
        self.assertIn("tail -n 1", text)
        self.assertNotIn("detail=$(sed -n '1p' \"$err\"", text)


class Backups(Home):
    def test_only_the_newest_timestamped_copies_are_kept(self):
        target = self.root / "settings.json"
        target.write_text("{}")
        original = Path(str(target) + ".second-wind-original")
        original.write_text("first")
        for day in range(1, 10):
            Path("%s.second-wind-backup-2026010%d-000000" % (target, day)).write_text("x")
        stranger = Path(str(target) + ".second-wind-backup-notes")
        stranger.write_text("not ours")
        swlib.backup_once(str(target))
        copies = sorted(self.root.glob("settings.json.second-wind-backup-2*"))
        self.assertEqual(len(copies), swlib.BACKUPS_KEPT)
        self.assertTrue(original.exists())
        self.assertTrue(stranger.exists())
        self.assertEqual(original.read_text(), "first")

    def test_a_symlinked_file_is_written_through(self):
        real = self.root / "dotfiles" / "settings.json"
        real.parent.mkdir()
        real.write_text("{}")
        link = self.root / "settings.json"
        link.symlink_to(real)
        swlib.write_json_atomic(str(link), {"a": 1})
        self.assertTrue(link.is_symlink())
        self.assertEqual(json.loads(real.read_text()), {"a": 1})


class Panel(Home):
    def test_an_old_antigravity_reading_is_not_drawn(self):
        cfg = self.config({"version": 4, "accounts": {"agy": {"kind": "antigravity", "label": "Gemini"}}})
        groups = [{"name": "Gemini Models", "buckets": [{"window": "weekly", "used_pct": 40.0}]}]
        path = Path(swlib.antigravity_usage_path("agy"))
        path.write_text(json.dumps({"read_at": time.time() - 3 * 86400, "groups": groups}))
        card = "\n".join(swlib.brief_card(cfg))
        self.assertIn("no current reading", card)
        self.assertNotIn("40%", card)
        path.write_text(json.dumps({"groups": groups}))
        self.assertNotIn("40%", "\n".join(swlib.brief_card(cfg)))
        path.write_text(json.dumps({"read_at": time.time() - 4000, "groups": groups}))
        self.assertIn("40%", "\n".join(swlib.brief_card(cfg)))

    def test_named_accounts_alone_show_no_primary_row(self):
        cfg = self.config({"version": 4, "accounts": {"agy": {"kind": "antigravity", "label": "Gemini"}}})
        self.assertEqual(swlib.enabled_roles(cfg), [])
        self.assertNotIn("primary", "\n".join(swlib.brief_card(cfg)))
        self.assertEqual(swlib.enabled_roles({"version": 4, "primary": {"label": "Work"}}), ["primary"])

    def test_presets_name_current_models(self):
        presets = " ".join(swlib.PICKER_MODELS["secondary"])
        self.assertIn("claude-fable-5-2", presets)
        self.assertIn("claude-haiku-5-5", presets)
        for entry in swlib.PICKER_MODELS["secondary"]:
            self.assertIn(entry, swlib.MODEL_NOTES)


class Spacing(Home):
    def test_a_role_read_in_the_last_five_minutes_is_not_read_again(self):
        self.config({"version": 4, "primary": {"label": "W"}, "secondary": {"enabled": True}})
        self.assertFalse(swlib.recently_read("secondary"))
        self.reading("secondary", five_hour_pct=10)
        self.assertTrue(swlib.recently_read("secondary"))
        self.assertFalse(swlib.recently_read("secondary", now=time.time() + 301))
        # a failure counts as a read: retried at once it only fails again
        Path(swlib.status_path("codex")).write_text("FAILED: nothing\n")
        self.assertTrue(swlib.recently_read("codex"))

    def test_spent_needs_a_reading_still_worth_showing(self):
        cfg = self.config({"version": 4, "primary": {"label": "W"}, "codex": {"enabled": True}})
        self.assertFalse(swlib.spent("codex", cfg=cfg))
        self.reading("codex", five_hour_pct=100, seven_day_pct=70)
        self.assertTrue(swlib.spent("codex", cfg=cfg))
        self.reading("codex", age=2 * 3600, five_hour_pct=100)
        self.assertFalse(swlib.spent("codex", cfg=cfg))
        self.reading("codex", five_hour_pct=99, seven_day_pct=70)
        self.assertFalse(swlib.spent("codex", cfg=cfg))
        # 99.6% shows as 100 and is not used up
        self.reading("codex", five_hour_pct=99.6, seven_day_pct=70)
        self.assertFalse(swlib.spent("codex", cfg=cfg))

    def test_a_window_whose_reset_has_passed_is_not_spent(self):
        cfg = self.config({"version": 4, "primary": {"label": "W"}, "codex": {"enabled": True}})
        now = time.time()
        self.reading("codex", age=600, five_hour_pct=100, seven_day_pct=40,
                     five_hour_resets_at=int(now) - 60)
        self.assertFalse(swlib.spent("codex", cfg=cfg))
        self.reading("codex", age=600, five_hour_pct=100, seven_day_pct=40,
                     five_hour_resets_at=int(now) + 600)
        self.assertTrue(swlib.spent("codex", cfg=cfg))
        # a clock time with no date, as Codex prints it, read against when it was printed
        soon = time.strftime("%H:%M", time.localtime(now + 1800))
        self.reading("codex", age=600, five_hour_pct=100, five_hour_resets=soon)
        self.assertTrue(swlib.spent("codex", cfg=cfg))
        self.assertFalse(swlib.spent("codex", cfg=cfg, now=now + 2400))

    def test_the_reset_named_is_the_one_that_brings_allowance_back(self):
        usage = {"five_hour_pct": 100, "seven_day_pct": 70, "five_hour_resets": "13:13",
                 "seven_day_resets": "00:39 on 12 Nov"}
        self.assertEqual(swlib.spent_reset_note("codex", usage), "resets 13:13")
        usage["seven_day_pct"] = 100
        self.assertIn("Nov 12", swlib.spent_reset_note("codex", usage))


class RefreshSpacing(Home):
    def run_refresh(self, *flags):
        env = dict(os.environ, SW_HOME=str(self.sw_home), PATH="/usr/bin:/bin")
        return subprocess.run(["/bin/sh", str(SCRIPTS / "usage-refresh.sh"), "--only", "codex", *flags],
                              env=env, capture_output=True, text=True, timeout=120)

    def test_a_failing_role_is_not_retried_within_five_minutes_unless_forced(self):
        workdir = self.root / "workdir"
        workdir.mkdir()
        self.config({"version": 4, "primary": {"label": "W"}, "codex": {"enabled": True},
                     "refresh": {"workdir": str(workdir)}})
        status = Path(swlib.status_path("codex"))
        status.write_text("FAILED: the vendor said wait\n")
        self.assertEqual(self.run_refresh().returncode, 0)
        self.assertEqual(status.read_text(), "FAILED: the vendor said wait\n")
        self.run_refresh("--force")
        self.assertIn("codex is not on PATH", status.read_text())
        old = time.time() - 400
        os.utime(status, (old, old))
        status.write_text("FAILED: the vendor said wait\n")
        os.utime(status, (old, old))
        self.run_refresh()
        self.assertIn("codex is not on PATH", status.read_text())


class Pruning(Home):
    def test_only_exchanges_are_pruned(self):
        notes = self.root / "notes"
        notes.mkdir(mode=0o700)
        mine = notes / "my-own-notes.md"
        mine.write_text("mine")
        old_exchange = notes / "20260101-000000-1-codex-work.md"
        old_exchange.write_text("old")
        legacy = notes / "legacy-codex-wind-20260101-000000-x.md"
        legacy.write_text("old")
        for path in (mine, old_exchange, legacy):
            os.utime(path, (time.time() - 40 * 86400,) * 2)
        conf = {"log": {"prune_days": 30, "max_exchange_kb": 200}, "log_dir": str(notes)}
        path = sw.exchange_path(conf, "x", self.root)
        sw.record_exchange(path, name="x", profile={"label": "x", "kind": "claude"},
                           status={"email": "user@example.com"}, workspace=self.root,
                           mode="review", command=["c"], prompt="p", output="o",
                           returncode=0, elapsed=1.0, config=conf)
        self.assertTrue(mine.exists())
        self.assertFalse(old_exchange.exists())
        self.assertFalse(legacy.exists())
        self.assertTrue(sw.EXCHANGE_NAME.match(path.name))


class ReaderGuard(unittest.TestCase):
    def test_a_newer_session_reading_stands_only_when_its_figures_are_not_lower(self):
        claude = load("claude-usage")
        record = {"five_hour_pct": 90, "seven_day_pct": 40}
        ahead = {"source": "status line", "cached_at": 200, "five_hour_pct": 92, "seven_day_pct": 40}
        self.assertTrue(claude.session_reading_is_ahead(ahead, record, 100))
        idle = dict(ahead, five_hour_pct=50)
        self.assertFalse(claude.session_reading_is_ahead(idle, record, 100))
        self.assertFalse(claude.session_reading_is_ahead(dict(ahead, cached_at=50), record, 100))
        self.assertFalse(claude.session_reading_is_ahead(dict(ahead, source="panel"), record, 100))
        self.assertFalse(claude.session_reading_is_ahead(
            {"source": "status line", "cached_at": 200}, record, 100))


class ConfigBackups(Home):
    def test_only_backups_this_tool_named_are_pruned(self):
        (self.sw_home / "config.json").write_text(json.dumps({"version": 4}))
        mine = self.sw_home / "config.backup-000-original.json"
        mine.write_text("{}")
        for index in range(8):
            (self.sw_home / ("config.backup-17900000000000000%02d.json" % index)).write_text("{}")
        sw.write_config({"version": 4, "accounts": {}})
        self.assertTrue(mine.exists())
        ours = [path for path in self.sw_home.glob("config.backup-1*.json")]
        self.assertEqual(len(ours), 5)


class Ownership(Home):
    def test_a_status_line_found_through_path_is_somebody_elses(self):
        self.assertFalse(sw_setup.statusline_is_ours({"command": "statusline.sh"}))
        self.assertFalse(sw_setup.is_ours("hooks/notification.py"))
        self.assertTrue(sw_setup.statusline_is_ours({"command": sw_setup.statusline_command()}))

    def test_a_quoted_command_is_still_the_same_command(self):
        spaced = "/Users/example/My Skills/second-wind/scripts/statusline.sh"
        import shlex
        self.assertEqual(sw_setup._tokens(shlex.quote(spaced)), [spaced])
        self.assertTrue(sw_setup.same_command(shlex.quote(spaced), shlex.quote(spaced)))
        self.assertTrue(os.path.exists(sw_setup.hook_path("SessionStart")))

    def test_a_hook_with_our_name_and_somebody_elses_content_is_theirs(self):
        hooks = self.root / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        theirs = hooks / "notification.py"
        theirs.write_text("#!/usr/bin/env python3\nprint('ding')\n")
        self.assertFalse(sw_setup.is_ours(str(theirs)))
        self.assertFalse(sw_setup.is_ours("python3 %s" % theirs))
        bar = self.root / ".claude" / "statusline.sh"
        bar.write_text("#!/bin/sh\necho hello\n")
        self.assertFalse(sw_setup.is_ours(str(bar)))

    def test_our_own_file_anywhere_and_a_dangling_entry_are_ours(self):
        old = self.root / "old" / "hooks"
        old.mkdir(parents=True)
        ours = old / "session-start.py"
        ours.write_text("import swlib\n")
        self.assertTrue(sw_setup.is_ours(str(ours)))
        self.assertTrue(sw_setup.is_ours(str(old / "notification.py")))
        self.assertTrue(sw_setup.is_ours(sw_setup.hook_command("SessionStart")))


class Readers(unittest.TestCase):
    def test_cursor_sign_in_screen_is_a_login_fault(self):
        cursor = load("cursor-usage")
        screen = "Cursor Agent v2026.10.19-6bbf2c4\n\n  Press any key to log in..."
        self.assertTrue(re.search(cursor.LOGIN, screen, re.I))
        self.assertFalse(re.search(cursor.LOGIN, "Plan, search, build anything", re.I))


class RunnerAllowance(unittest.TestCase):
    """The runner knows when an account is spent, before and after sending."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home, self.bin = self.root / "home", self.root / "bin"
        self.workspace, self.profile = self.root / "workspace", self.root / "personal"
        for path in (self.home, self.bin, self.workspace, self.profile):
            path.mkdir()
        (self.home / ".claude").mkdir()
        self.sw_home = self.root / "sw"
        self.sw_home.mkdir()
        (self.sw_home / "config.json").write_text(json.dumps({
            "version": 4,
            "primary": {"label": "Work", "config_dir": str(self.home / ".claude"),
                        "account": "work@example.com"},
            "secondary": {"enabled": True, "label": "Personal", "config_dir": str(self.profile),
                          "account": "personal@example.com"},
            "log": {"dir": str(self.sw_home / "log"), "max_exchange_kb": 200, "prune_days": 30},
        }))
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("Review this.")
        self.env = dict(os.environ, HOME=str(self.home), SW_HOME=str(self.sw_home),
                        PATH="%s:%s" % (self.bin, os.environ.get("PATH", "")), SW_NO_REFRESH="1")
        self.env.pop("CLAUDE_CONFIG_DIR", None)

    def tearDown(self):
        self.temp.cleanup()

    def client(self, body):
        fake = self.bin / "claude"
        fake.write_text("""#!/bin/sh
if [ "$1" = auth ]; then
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then email=personal@example.com; else email=work@example.com; fi
  printf '{"loggedIn":true,"authMethod":"claude.ai","email":"%%s","subscriptionType":"test"}\\n' "$email"
  exit 0
fi
cat >/dev/null
%s
""" % body)
        fake.chmod(0o755)

    def run_cli(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "second_wind.py"), "run", "secondary",
             "--prompt-file", str(self.prompt), "--workspace", str(self.workspace),
             "--review", *extra],
            env=self.env, text=True, capture_output=True, check=False)

    def spend(self):
        (self.sw_home / "usage-secondary.json").write_text(json.dumps(
            {"cached_at": int(time.time()), "five_hour_pct": 100, "seven_day_pct": 60,
             "account": "personal@example.com"}))

    def test_a_vendor_usage_limit_failure_has_its_own_exit_code(self):
        self.client("echo \"ERROR: You've hit your usage limit. Try again at 1:13 PM.\" >&2; exit 1")
        result = self.run_cli()
        self.assertEqual(result.returncode, sw.EXIT_OUT_OF_ALLOWANCE, result.stderr)
        self.assertIn("out of allowance", result.stderr)
        rows = [json.loads(line) for line in next((self.sw_home / "log").glob("*.jsonl")).read_text().splitlines()]
        self.assertEqual(rows[-1]["exit"], sw.EXIT_OUT_OF_ALLOWANCE)

    def test_an_ordinary_failure_keeps_its_exit_code(self):
        self.client("echo 'something else broke' >&2; exit 1")
        self.assertEqual(self.run_cli().returncode, 1)

    def test_a_reply_about_limits_is_not_a_failure(self):
        self.client("echo 'The user hit your usage limit bug in section 3.'")
        self.assertEqual(self.run_cli().returncode, 0)

    def test_a_spent_account_is_refused_before_anything_is_sent(self):
        self.client("echo SENT; exit 0")
        self.spend()
        result = self.run_cli()
        self.assertEqual(result.returncode, sw.EXIT_OUT_OF_ALLOWANCE, result.stderr)
        self.assertIn("nothing sent", result.stderr)
        self.assertNotIn("SENT", result.stdout)

    def test_ignore_usage_sends_anyway(self):
        self.client("echo SENT; exit 0")
        self.spend()
        result = self.run_cli("--ignore-usage")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SENT", result.stdout)


if __name__ == "__main__":
    unittest.main()
