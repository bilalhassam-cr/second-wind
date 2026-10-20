from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "codex_wind.py"


class CodexWindTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.workspace = self.root / "workspace"
        self.profile = self.root / "personal-profile"
        for path in (self.home, self.bin, self.workspace, self.profile):
            path.mkdir()
        (self.home / ".claude").mkdir()
        config_home = self.root / "codex-wind-home"
        config_home.mkdir()
        (config_home / "config.json").write_text(json.dumps({
            "version": 1,
            "accounts": {
                "claude-work": {
                    "label": "Work Claude",
                    "config_dir": str(self.home / ".claude"),
                    "expected_email": "work@example.com",
                },
                "claude-personal": {
                    "label": "Personal Claude",
                    "config_dir": str(self.profile),
                    "expected_email": "personal@example.com",
                },
            },
            "timeout_seconds": 30,
            "log_dir": str(config_home / "exchanges"),
        }))
        fake = self.bin / "claude"
        fake.write_text("""#!/bin/sh
if [ "$1" = auth ]; then
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then email=personal@example.com; else email=work@example.com; fi
  printf '{"loggedIn":true,"authMethod":"claude.ai","email":"%s","subscriptionType":"test"}\\n' "$email"
  exit 0
fi
printf 'CONFIG=%s\\n' "${CLAUDE_CONFIG_DIR:-default}"
printf 'KEY=%s\\n' "${ANTHROPIC_API_KEY:-cleared}"
printf 'ARGS=%s\\n' "$*"
cat
""")
        fake.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update({
            "HOME": str(self.home),
            "CODEX_WIND_HOME": str(config_home),
            "PATH": f"{self.bin}:{self.env.get('PATH', '')}",
            "ANTHROPIC_API_KEY": "must-not-leak",
            "CLAUDE_CONFIG_DIR": "/wrong/inherited/profile",
        })

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_status_verifies_both_named_profiles(self) -> None:
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("READY  claude-work", result.stdout)
        self.assertIn("READY  claude-personal", result.stdout)

    def test_work_uses_personal_profile_and_clears_api_key(self) -> None:
        prompt = self.root / "prompt.txt"
        prompt.write_text("Do the bounded work.")
        result = self.run_cli(
            "run", "claude-personal", "--prompt-file", str(prompt),
            "--workspace", str(self.workspace), "--work",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"CONFIG={self.profile.resolve()}", result.stdout)
        self.assertIn("KEY=cleared", result.stdout)
        self.assertIn("--permission-mode bypassPermissions", result.stdout)
        logs = list((Path(self.env["CODEX_WIND_HOME"]) / "exchanges").glob("*.md"))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)

    def test_default_profile_unsets_inherited_config_dir(self) -> None:
        prompt = self.root / "prompt.txt"
        prompt.write_text("Review this.")
        result = self.run_cli(
            "run", "claude-work", "--prompt-file", str(prompt),
            "--workspace", str(self.workspace), "--review",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=default", result.stdout)
        self.assertIn("--disallowed-tools", result.stdout)
        self.assertIn("--strict-mcp-config", result.stdout)

    def test_identity_mismatch_blocks_before_model_run(self) -> None:
        config = Path(self.env["CODEX_WIND_HOME"]) / "config.json"
        data = json.loads(config.read_text())
        data["accounts"]["claude-personal"]["expected_email"] = "wrong@example.com"
        config.write_text(json.dumps(data))
        prompt = self.root / "prompt.txt"
        prompt.write_text("Must not run.")
        result = self.run_cli(
            "run", "claude-personal", "--prompt-file", str(prompt),
            "--workspace", str(self.workspace), "--work",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("identity mismatch", result.stderr)
        self.assertNotIn("ARGS=", result.stdout)

class ExtendedTests(CodexWindTests):
    def update(self, name, **fields):
        path = Path(self.env['CODEX_WIND_HOME']) / 'config.json'
        data = json.loads(path.read_text())
        data['accounts'].setdefault(name, {}).update(fields)
        path.write_text(json.dumps(data))

    def fake(self, name, source):
        path = self.bin / name
        path.write_text('#!' + sys.executable + '\n' + source)
        path.chmod(0o755)

    def task(self, name='claude-personal', *options):
        prompt = self.root / 'task.txt'
        prompt.write_text('A bounded task.')
        return self.run_cli('run', name, '--prompt-file', str(prompt),
                            '--workspace', str(self.workspace), *options)

    def test_empty_reply_is_failure_in_exchange_too(self):
        self.fake('claude', '''import sys,json
if sys.argv[1] == 'auth': print(json.dumps(dict(loggedIn=True,authMethod='claude.ai',email='personal@example.com')))
''')
        result = self.task('claude-personal', '--review')
        self.assertEqual(result.returncode, 4, result.stderr)
        log = next((Path(self.env['CODEX_WIND_HOME'])/'exchanges').glob('*.md'))
        self.assertIn('- Exit: 4', log.read_text())

    def test_timeout_kills_worker_and_records_failure(self):
        self.fake('claude', '''import sys,json,time
if sys.argv[1] == 'auth': print(json.dumps(dict(loggedIn=True,authMethod='claude.ai',email='personal@example.com')))
else: time.sleep(20)
''')
        result = self.task('claude-personal', '--work', '--timeout', '1')
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn('timed out', result.stderr)

    def test_review_allows_only_read_tools_and_disables_hooks(self):
        result = self.task('claude-personal', '--review')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--tools Read,Glob,Grep', result.stdout)
        self.assertIn('disableAllHooks', result.stdout)
        self.assertNotIn('bypassPermissions', result.stdout)

    def test_invalid_timeout_cannot_become_default(self):
        result = self.task('claude-personal', '--review', '--timeout', '0')
        self.assertEqual(result.returncode, 2)
        self.assertIn('positive', result.stderr)

    def test_codex_uses_own_home_and_only_final_message(self):
        self.update('codex', kind='codex', label='Codex', config_dir=str(self.profile))
        self.env['CODEX_HOME'] = '/wrong/inherited'
        self.fake('codex', '''import sys,os
from pathlib import Path
if sys.argv[1:3] == ['mcp','list']:
 print('[{\"name\":\"writer\",\"enabled\":true}]')
elif sys.argv[1:3] == ['login','status']:
 print('Logged in using ChatGPT',file=sys.stderr)
else:
 assert os.environ['CODEX_HOME'].endswith('personal-profile')
 assert 'OPENAI_API_KEY' not in os.environ
 assert '-s' in sys.argv and sys.argv[sys.argv.index('-s')+1]=='read-only'
 assert 'mcp_servers.writer.enabled=false' in sys.argv
 Path(sys.argv[sys.argv.index('-o')+1]).write_text('FINAL ONLY')
 print('STARTUP CHATTER')
''')
        result = self.task('codex', '--review')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'FINAL ONLY')

    def test_codex_startup_text_is_not_an_answer(self):
        self.update('codex', kind='codex', label='Codex', config_dir=str(self.profile))
        self.fake('codex', '''import sys
if sys.argv[1:3] == ['mcp','list']:
 print('[{\"name\":\"writer\",\"enabled\":true}]')
elif sys.argv[1:3] == ['mcp','list']: print('[]')
elif sys.argv[1:3] == ['login','status']: print('Logged in using ChatGPT')
else: print('connected to server but no final response')
''')
        result = self.task('codex', '--review')
        self.assertEqual(result.returncode, 4)
        self.assertNotIn('connected', result.stdout)

    def test_codex_rejects_api_key_login(self):
        self.update('codex', kind='codex', label='Codex', config_dir=str(self.profile))
        self.fake('codex', "print('Logged in using an API key')\n")
        result = self.task('codex', '--review')
        self.assertEqual(result.returncode, 2)
        self.assertIn('ChatGPT', result.stderr)

    def test_cursor_has_plan_mode_and_work_has_force(self):
        self.update('cursor', kind='cursor', label='Cursor', expected_email='personal@example.com')
        self.fake('cursor-agent', '''import sys
if sys.argv[1]=='status': print('Logged in as personal@example.com')
else: print('ARGS='+' '.join(sys.argv[1:]))
''')
        review = self.task('cursor', '--review')
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertIn('--mode plan', review.stdout)
        self.assertNotIn('--force', review.stdout)
        work = self.task('cursor', '--work')
        self.assertIn('--force', work.stdout)
        bad = self.task('cursor', '--work', '--effort', 'high')
        self.assertEqual(bad.returncode, 2)

    def test_grok_prompt_is_a_private_file_not_process_argument(self):
        self.update('grok', kind='grok', label='Grok')
        self.fake('grok', '''import sys
from pathlib import Path
if sys.argv[1]=='models': print('grok-model')
else:
 p=Path(sys.argv[sys.argv.index('--prompt-file')+1])
 assert p.stat().st_mode & 0o777 == 0o600
 assert 'A bounded task.' in p.read_text()
 assert 'A bounded task.' not in sys.argv
 print('{\"text\":\"GROK OK\",\"stopReason\":\"end_turn\"}')
''')
        result = self.task('grok', '--review')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('GROK OK', result.stdout)

    def test_import_preserves_aliases_and_never_grants_full_access(self):
        source = self.root / 'second-wind.json'
        source.write_text(json.dumps({'version':4,
            'primary':{'config_dir':str(self.home/'.claude'),'account':'work@example.com','label':'Work'},
            'secondary':{'enabled':True,'config_dir':str(self.profile),'account':'personal@example.com','label':'Personal'},
            'codex':{'enabled':True,'config_dir':str(self.profile),'account':'work@example.com','label':'Codex','full_access':True},
            'cursor':{'enabled':False}}))
        before = source.read_bytes()
        result = self.run_cli('import-second-wind', '--config', str(source))
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads((Path(self.env['CODEX_WIND_HOME'])/'config.json').read_text())
        self.assertIn('claude-work', config['accounts'])
        self.assertFalse(config['accounts']['codex']['full_access'])
        self.assertNotIn('cursor', config['accounts'])
        self.assertEqual(source.read_bytes(), before)
        self.assertTrue(list(Path(self.env['CODEX_WIND_HOME']).glob('config.backup-*.json')))

    def test_traversal_account_name_is_rejected(self):
        result = self.task('../outside', '--review')
        self.assertEqual(result.returncode, 2)
        self.assertIn('account names', result.stderr)

    def test_stderr_chatter_is_diagnostic_not_the_answer(self):
        self.fake('claude', """import sys,json
if sys.argv[1] == 'auth': print(json.dumps(dict(loggedIn=True,authMethod='claude.ai',email='personal@example.com')))
else:
 print('ANSWER')
 print('STARTUP WARNING',file=sys.stderr)
""")
        result = self.task('claude-personal', '--review')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'ANSWER')
        log = next((Path(self.env['CODEX_WIND_HOME'])/'exchanges').glob('*.md'))
        self.assertIn('STARTUP WARNING', log.read_text())

    def test_warning_before_auth_json_is_tolerated(self):
        self.fake('claude', """import json
print('Client startup warning')
print(json.dumps(dict(loggedIn=True,authMethod='claude.ai',email='personal@example.com')))
""")
        self.assertEqual(self.run_cli('status','claude-personal').returncode, 0)

    def test_sigterm_stops_worker_and_records_interruption(self):
        import time
        self.fake('claude', """import sys,json,time
from pathlib import Path
if sys.argv[1] == 'auth': print(json.dumps(dict(loggedIn=True,authMethod='claude.ai',email='personal@example.com')))
else:
 Path('started').write_text('yes')
 time.sleep(3)
 Path('orphaned').write_text('bad')
""")
        prompt = self.root/'interrupt.txt'
        prompt.write_text('bounded work')
        process = subprocess.Popen([sys.executable,str(SCRIPT),'run','claude-personal',
            '--prompt-file',str(prompt),'--workspace',str(self.workspace),'--work'],
            env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline = time.monotonic()+5
            while not (self.workspace/'started').exists() and time.monotonic()<deadline:
                time.sleep(0.02)
            self.assertTrue((self.workspace/'started').exists())
            process.terminate()
            _, errors = process.communicate(timeout=5)
            self.assertEqual(process.returncode,130,errors)
            time.sleep(3)
            self.assertFalse((self.workspace/'orphaned').exists())
            log = next((Path(self.env['CODEX_WIND_HOME'])/'exchanges').glob('*.md'))
            self.assertIn('- Exit: 130',log.read_text())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_import_accepts_default_codex_home_and_keeps_custom_fields(self):
        self.update('codex',kind='codex',label='Old',config_dir=str(self.home/'.codex'),custom='kept')
        source=self.root/'source.json'
        source.write_text(json.dumps({'version':4,'primary':{'account':'unknown'},
                                     'codex':{'enabled':True,'label':'New'}}))
        result=self.run_cli('import-second-wind','--config',str(source))
        self.assertEqual(result.returncode,0,result.stderr)
        config=json.loads((Path(self.env['CODEX_WIND_HOME'])/'config.json').read_text())
        self.assertEqual(config['accounts']['codex']['custom'],'kept')
        self.assertEqual(config['accounts']['codex']['config_dir'],'~/.codex')
        self.assertIn('SKIPPED primary',result.stderr)

    def test_unknown_codex_mcp_configuration_blocks_review(self):
        self.update('codex',kind='codex',label='Codex',config_dir=str(self.profile))
        self.fake('codex', """import sys
if sys.argv[1:3]==['login','status']: print('Logged in using ChatGPT')
elif sys.argv[1:3]==['mcp','list']: print('not json')
else: print('MUST NOT RUN')
""")
        result=self.task('codex','--review')
        self.assertEqual(result.returncode,2)
        self.assertNotIn('MUST NOT RUN',result.stdout)
        self.assertIn('refusing',result.stderr)


if __name__ == "__main__":
    unittest.main()
