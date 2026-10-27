from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "second_wind.py"


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
        config_home = self.root / "second-wind-home"
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
            "SW_HOME": str(config_home),
            "PATH": f"{self.bin}:{self.env.get('PATH', '')}",
            "ANTHROPIC_API_KEY": "must-not-leak",
            "SW_NO_REFRESH": "1",
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
        logs = list((Path(self.env["SW_HOME"]) / "exchanges").glob("*.md"))
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
        config = Path(self.env["SW_HOME"]) / "config.json"
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
        path = Path(self.env['SW_HOME']) / 'config.json'
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
        log = next((Path(self.env['SW_HOME'])/'exchanges').glob('*.md'))
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

    AGY = '''import sys,os,json
if sys.argv[1]=='models':
 if os.environ.get('AGY_SIGNED_OUT'): print('Error: Please sign in to view available models.'); sys.exit(1)
 print('gemini-4-argon-high\\tGemini 4 Argon (High)')
 sys.exit(0)
prompt=sys.stdin.read()
assert 'A bounded task.' in prompt
assert not any('A bounded task.' in a for a in sys.argv)
assert '-p' not in sys.argv and '--print' not in sys.argv
assert ('file-viewing tools' in prompt) == ('--mode' in sys.argv)
assert 'GEMINI_API_KEY' not in os.environ and 'AGY_ADC_AUTH' not in os.environ
if '--model' in sys.argv and sys.argv[sys.argv.index('--model')+1]=='missing-model':
 print(json.dumps(dict(status='ERROR',response='',error='model missing-model is not recognized'))); sys.exit(1)
print(json.dumps(dict(status='SUCCESS',response='ARGS='+' '.join(sys.argv[1:])+' HOME='+os.environ['HOME'])))
'''

    def test_antigravity_review_is_plan_mode_and_prompt_is_stdin(self):
        self.update('agy', kind='antigravity', label='Antigravity')
        self.fake('agy', self.AGY)
        self.env['GEMINI_API_KEY'] = 'inherited'
        self.env['AGY_ADC_AUTH'] = '1'
        review = self.task('agy', '--review', '--model', 'gemini-4-argon-high', '--effort', 'high')
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertIn('--mode plan --sandbox', review.stdout)
        self.assertIn('--output-format json', review.stdout)
        self.assertIn('--model gemini-4-argon-high', review.stdout)
        self.assertIn('--effort high', review.stdout)
        self.assertNotIn('--dangerously-skip-permissions', review.stdout)
        work = self.task('agy', '--work')
        self.assertEqual(work.returncode, 0, work.stderr)
        self.assertIn('--dangerously-skip-permissions', work.stdout)
        self.assertNotIn('--mode plan', work.stdout)
        log = sorted((Path(self.env['SW_HOME'])/'exchanges').glob('*.md'))[0].read_text()
        self.assertIn('- Sandbox: plan mode, terminal sandbox, not an OS sandbox', log)

    def test_antigravity_second_account_runs_in_its_own_home(self):
        home = self.root / 'agy-second'
        home.mkdir()
        self.update('agy2', kind='antigravity', label='Antigravity second', home=str(home), model='account-model')
        self.fake('agy', self.AGY)
        result = self.task('agy2', '--review')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('HOME=' + str(home.resolve()), result.stdout)
        self.assertIn('--model account-model', result.stdout)
        override = self.task('agy2', '--review', '--model', 'run-model')
        self.assertIn('--model run-model', override.stdout)
        self.assertNotIn('account-model', override.stdout)
        self.update('agy3', kind='antigravity', label='Missing', home=str(self.root / 'absent'))
        missing = self.task('agy3', '--review')
        self.assertEqual(missing.returncode, 2)
        self.assertIn('home does not exist', missing.stderr)

    def test_antigravity_error_status_and_sign_out_are_failures(self):
        self.update('agy', kind='antigravity', label='Antigravity')
        self.fake('agy', self.AGY)
        bad = self.task('agy', '--review', '--model', 'missing-model')
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('not recognized', bad.stderr)
        self.assertNotIn('ARGS=', bad.stdout)
        self.env['AGY_SIGNED_OUT'] = '1'
        out = self.task('agy', '--review')
        self.assertEqual(out.returncode, 2)
        self.assertIn('authentication check failed', out.stderr)

    def test_migration_preserves_aliases_and_shared_roles(self):
        target=Path(self.env['SW_HOME'])/'config.json'
        target.write_text(json.dumps({'version':4,'primary':{'config_dir':str(self.home/'.claude'),'account':'work@example.com','label':'Work'}}))
        source=self.root/'old.json'
        source.write_text(json.dumps({'version':1,'accounts':{'claude-work':{'kind':'claude','label':'Work','config_dir':str(self.home/'.claude'),'expected_email':'work@example.com'}}}))
        before=source.read_bytes()
        result=self.run_cli('migrate-codex-wind','--config',str(source))
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(target.read_text())
        self.assertEqual(data['aliases']['claude-work'],'primary')
        self.assertEqual(data['primary']['account'],'work@example.com')
        self.assertEqual(source.read_bytes(),before)
        self.assertTrue(list(target.parent.glob('config.backup-*.json')))
        self.assertEqual(self.run_cli('status','claude-work').returncode,0)

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
        log = next((Path(self.env['SW_HOME'])/'exchanges').glob('*.md'))
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
            log = next((Path(self.env['SW_HOME'])/'exchanges').glob('*.md'))
            self.assertIn('- Exit: 130',log.read_text())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_migration_refuses_conflicting_identity(self):
        target=Path(self.env['SW_HOME'])/'config.json'
        target.write_text(json.dumps({'version':4,'primary':{'config_dir':str(self.home/'.claude'),'account':'work@example.com','label':'Work'}}))
        before=target.read_bytes()
        source=self.root/'old.json'
        source.write_text(json.dumps({'version':1,'accounts':{'old-work':{'label':'Work','config_dir':str(self.home/'.claude'),'expected_email':'wrong@example.com'}}}))
        result=self.run_cli('migrate-codex-wind','--config',str(source))
        self.assertEqual(result.returncode,2)
        self.assertIn('identity conflict',result.stderr)
        self.assertEqual(target.read_bytes(),before)

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

    def test_migrated_history_is_indexed_once_and_source_kept(self):
        target=Path(self.env['SW_HOME'])/'config.json'
        target.write_text(json.dumps({'version':4,'accounts':{}}))
        source=self.root/'legacy/config.json'
        source.parent.mkdir()
        logs=source.parent/'exchanges'
        logs.mkdir()
        original=logs/'20261020-120000-1-claude-work-project.md'
        original.write_text('# Codex Wind exchange\n\n- Worker: claude-work (Work)\n- Account: work@example.com\n- Workspace: /project\n- Mode: review\n- Exit: 0\n- Duration seconds: 2.5\n\n## Response\n\n- Exit: 99\n- Workspace: /fake\nDONE\n')
        source.write_text(json.dumps({'version':1,'accounts':{'claude-work':{'label':'Work','config_dir':str(self.home/'.claude'),'expected_email':'work@example.com'}}}))
        for _ in range(2):
            result=self.run_cli('migrate-codex-wind','--config',str(source))
            self.assertEqual(result.returncode,0,result.stderr)
        directory=Path(self.env['SW_HOME'])/'log'
        rows=[json.loads(line) for path in directory.glob('*.jsonl') for line in path.read_text().splitlines()]
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['worker'],'claude-work')
        self.assertEqual(rows[0]['exit'],0)
        self.assertEqual(rows[0]['cwd'],'/project')
        self.assertEqual(Path(rows[0]['exchange']).read_bytes(),original.read_bytes())
        self.assertTrue(original.exists())

    def test_migration_rejects_account_name_with_different_profile(self):
        target = Path(self.env['SW_HOME'])/'config.json'
        data = json.loads(target.read_text())
        data['version'] = 4
        target.write_text(json.dumps(data))
        before = target.read_bytes()
        source = self.root/'old.json'
        profile = dict(data['accounts']['claude-personal'], config_dir=str(self.root/'another-profile'))
        source.write_text(json.dumps({'version':1,'accounts':{'claude-personal':profile}}))
        result = self.run_cli('migrate-codex-wind','--config',str(source))
        self.assertEqual(result.returncode,2)
        self.assertIn('account-name conflict',result.stderr)
        self.assertEqual(target.read_bytes(),before)

    def test_migration_cannot_enable_disabled_role(self):
        target = Path(self.env['SW_HOME'])/'config.json'
        target.write_text(json.dumps({'version':4,'codex':{'enabled':False}}))
        before = target.read_bytes()
        source = self.root/'old.json'
        source.write_text(json.dumps({'version':1,'accounts':{'codex':{
            'kind':'codex','label':'Codex','config_dir':str(self.profile)}}}))
        result = self.run_cli('migrate-codex-wind','--config',str(source))
        self.assertEqual(result.returncode,2)
        self.assertIn('account-name conflict',result.stderr)
        self.assertEqual(target.read_bytes(),before)

    def test_migration_cannot_alias_disabled_role_to_enabled_role(self):
        target = Path(self.env['SW_HOME'])/'config.json'
        target.write_text(json.dumps({'version':4,'codex':{'enabled':False},
            'codex2':{'enabled':True,'label':'Other Codex','config_dir':str(self.profile)}}))
        before = target.read_bytes()
        source = self.root/'old.json'
        source.write_text(json.dumps({'version':1,'accounts':{'codex':{
            'kind':'codex','label':'Codex','config_dir':str(self.profile)}}}))
        result = self.run_cli('migrate-codex-wind','--config',str(source))
        self.assertEqual(result.returncode,2)
        self.assertIn('account-name conflict',result.stderr)
        self.assertEqual(target.read_bytes(),before)

    def test_migration_retains_incoming_identity_pin_without_elevating_access(self):
        target = Path(self.env['SW_HOME'])/'config.json'
        target.write_text(json.dumps({'version':4,'codex':{'enabled':True,
            'label':'Codex','config_dir':str(self.profile),'account':'unknown','full_access':False}}))
        source = self.root/'old.json'
        source.write_text(json.dumps({'version':1,'accounts':{'old-codex':{
            'kind':'codex','label':'Codex','config_dir':str(self.profile),
            'expected_email':'personal@example.com','full_access':True}}}))
        result = self.run_cli('migrate-codex-wind','--config',str(source))
        self.assertEqual(result.returncode,0,result.stderr)
        data = json.loads(target.read_text())
        self.assertEqual(data['codex']['expected_email'],'personal@example.com')
        self.assertEqual(data['codex']['account'],'personal@example.com')
        self.assertFalse(data['codex']['full_access'])
        self.assertEqual(data['aliases']['old-codex'],'codex')

    def test_invalid_log_settings_block_before_worker(self):
        target = Path(self.env['SW_HOME'])/'config.json'
        data = json.loads(target.read_text())
        data['log'] = {'max_exchange_kb':0}
        target.write_text(json.dumps(data))
        result = self.task('claude-personal','--work')
        self.assertEqual(result.returncode,2)
        self.assertIn('log.max_exchange_kb',result.stderr)
        self.assertNotIn('ARGS=',result.stdout)

    def test_v4_role_and_alias_write_one_shared_history(self):
        target=Path(self.env['SW_HOME'])/'config.json'
        target.write_text(json.dumps({'version':4,
            'primary':{'label':'Work','config_dir':str(self.home/'.claude'),'account':'work@example.com'},
            'aliases':{'old-work':'primary'}}))
        result=self.task('old-work','--work')
        self.assertEqual(result.returncode,0,result.stderr)
        directory=Path(self.env['SW_HOME'])/'log'
        row=json.loads(next(directory.glob('*.jsonl')).read_text())
        self.assertEqual(row['worker'],'primary')
        self.assertEqual(row['account'],'work@example.com')
        self.assertTrue(Path(row['exchange']).exists())


if __name__ == "__main__":
    unittest.main()
