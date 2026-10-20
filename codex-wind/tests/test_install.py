import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = dict(os.environ, HOME=str(self.root))
        self.env.pop('CODEX_HOME', None)

    def tearDown(self):
        self.temp.cleanup()

    def install(self, *args):
        return subprocess.run([str(ROOT/'install.sh'), *map(str,args)],
                              capture_output=True,text=True,env=self.env)

    def test_default_and_legacy_destination(self):
        result = self.install()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.root/'.claude/skills/second-wind/SKILL.md').is_file())
        self.assertFalse((self.root/'.codex').exists())
        result = self.install(self.root/'legacy')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.root/'legacy/second-wind/SKILL.md').is_file())

    def test_both_copy_reinstall_and_no_config_changes(self):
        config = self.root/'.codex-wind/config.json'
        config.parent.mkdir()
        config.write_text('keep me')
        for _ in range(2):
            result = self.install('--both')
            self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.root/'.claude/skills/second-wind/SKILL.md').is_file())
        self.assertTrue((self.root/'.codex/skills/codex-wind/scripts/codex_wind.py').is_file())
        self.assertEqual(config.read_text(),'keep me')

    def test_codex_only_and_link(self):
        result = self.install('--codex','--link')
        self.assertEqual(result.returncode,0,result.stderr)
        target = self.root/'.codex/skills/codex-wind'
        self.assertTrue(target.is_symlink())
        self.assertEqual(target.resolve(),ROOT/'codex-wind')
        self.assertFalse((self.root/'.claude').exists())
        self.assertEqual(self.install('--codex','--link').returncode,0)

    def test_refuses_to_replace_source(self):
        result = self.install('--codex', ROOT)
        self.assertEqual(result.returncode,2)
        self.assertTrue((ROOT/'codex-wind/SKILL.md').is_file())

    def test_custom_homes_and_paths_with_spaces(self):
        self.env['CODEX_HOME'] = str(self.root/'custom codex')
        result = self.install('--both','--claude-dir',self.root/'Claude skills')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.root/'custom codex/skills/codex-wind/SKILL.md').is_file())
        self.assertTrue((self.root/'Claude skills/second-wind/SKILL.md').is_file())
