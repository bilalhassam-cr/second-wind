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
        result = self.install('--claude')
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
        self.assertTrue((self.root/'.codex/skills/second-wind/scripts/codex_wind.py').is_file())
        self.assertEqual(config.read_text(),'keep me')

    def test_codex_only_and_link(self):
        result = self.install('--codex','--link')
        self.assertEqual(result.returncode,0,result.stderr)
        target = self.root/'.codex/skills/second-wind'
        self.assertTrue(target.is_symlink())
        self.assertEqual(target.resolve(),ROOT/'second-wind')
        self.assertFalse((self.root/'.claude').exists())
        self.assertEqual(self.install('--codex','--link').returncode,0)

    def test_refuses_to_replace_source(self):
        result = self.install('--codex', ROOT)
        self.assertEqual(result.returncode,2)
        self.assertTrue((ROOT/'second-wind/SKILL.md').is_file())

    def test_custom_homes_and_paths_with_spaces(self):
        self.env['CODEX_HOME'] = str(self.root/'custom codex')
        result = self.install('--both','--claude-dir',self.root/'Claude skills')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.root/'custom codex/skills/second-wind/SKILL.md').is_file())
        self.assertTrue((self.root/'Claude skills/second-wind/SKILL.md').is_file())

    def test_one_detected_harness_is_selected_without_input(self):
        self.env['PATH']='/usr/bin:/bin'
        (self.root/'.codex').mkdir()
        result=self.install()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.root/'.codex/skills/second-wind/SKILL.md').exists())
        self.assertFalse((self.root/'.claude').exists())

    def test_ambiguous_detection_requires_a_flag_without_terminal(self):
        self.env['PATH']='/usr/bin:/bin'
        (self.root/'.codex').mkdir()
        (self.root/'.claude').mkdir()
        result=self.install()
        self.assertEqual(result.returncode,2)
        self.assertIn('non-interactive',result.stderr)
        self.assertFalse((self.root/'.claude/skills').exists())

    def test_both_hosts_load_identical_skill_and_archive_legacy(self):
        old=self.root/'.codex/skills/codex-wind'
        old.mkdir(parents=True)
        (old/'SKILL.md').write_text('legacy')
        result=self.install('--both')
        self.assertEqual(result.returncode,0,result.stderr)
        a=self.root/'.claude/skills/second-wind/SKILL.md'
        b=self.root/'.codex/skills/second-wind/SKILL.md'
        self.assertEqual(a.read_bytes(),b.read_bytes())
        self.assertIn('name: second-wind',b.read_text())
        self.assertFalse(old.exists())
        backups=list((self.root/'.second-wind/backups').glob('codex-wind-install-*'))
        self.assertEqual((backups[0]/'SKILL.md').read_text(),'legacy')

    def test_interactive_install_offers_both_hosts(self):
        import importlib.util
        from unittest import mock
        spec=importlib.util.spec_from_file_location('wind_installer',ROOT/'install.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        args=['install.py','--claude-dir',str(self.root/'claude'),'--codex-dir',str(self.root/'codex')]
        with mock.patch('sys.argv',args), mock.patch('sys.stdin.isatty',return_value=True), mock.patch('builtins.input',return_value='3'):
            module.main()
        self.assertTrue((self.root/'claude/second-wind/SKILL.md').exists())
        self.assertTrue((self.root/'codex/second-wind/SKILL.md').exists())
