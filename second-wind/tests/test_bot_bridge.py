"""Real CLI/HTTP boundary with synthetic clients; no external subscriptions."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import sys
import unittest
from unittest import mock
from types import SimpleNamespace

from test_grokbot import GrokBotTests

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import bot_bridge as bridge
import second_wind as sw


class BridgeTests(unittest.TestCase):
    setUpBase = GrokBotTests.setUp
    stop_server = GrokBotTests.stop_server
    write_config = GrokBotTests.write_config
    reply = GrokBotTests.reply
    cli = GrokBotTests.cli

    def setUp(self):
        self.setUpBase()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.profile = self.root / 'profile'
        self.profile.mkdir()
        self.env['PATH'] = str(self.bin) + os.pathsep + self.env['PATH']
        self.native_id = '11111111-1111-4111-8111-111111111111'
        self.config['accounts']['worker'] = {'kind': 'claude', 'label': 'Worker',
            'config_dir': str(self.profile), 'expected_email': 'user@example.com'}
        self.config['accounts']['code'] = {'kind': 'codex', 'label': 'Code', 'config_dir': str(self.profile)}
        self.write_config()
        script = '''#!/usr/bin/env python3
import json,sys,os
from pathlib import Path
a=sys.argv[1:]
if a[:2]==['auth','status']:
 print(json.dumps(dict(loggedIn=True,authMethod='claude.ai',email='user@example.com')));sys.exit()
if a[:2]==['login','status']:
 print('Logged in using ChatGPT', file=sys.stderr);sys.exit()
if a[:2]==['mcp','list']:
 print('[]');sys.exit()
prompt=sys.stdin.read()
root=Path(os.environ['SW_HOME'])
with (root/'invocations').open('a') as f:f.write(json.dumps(a)+'\\n')
while (root/'hold').exists():
 import time; time.sleep(0.05)
if (root/'fail').exists():print('failed',file=sys.stderr);sys.exit(7)
id='11111111-1111-4111-8111-111111111111'
if 'exec' in a:
 Path(a[a.index('-o')+1]).write_text('native reply')
 print(json.dumps({'type':'thread.started','thread_id':id}))
else:
 print(json.dumps({'result':'native reply','session_id':id,'is_error':False}))
'''
        for name in ('claude', 'codex'):
            (self.bin / name).write_text(script)
            (self.bin / name).chmod(0o755)
        result = self.cli('bridge', 'setup', '--source', 'team', '--bot', 'Assistant',
                          '--allow-account', 'worker', '--allow-account', 'code', '--root', str(self.root), '--allow-work')
        self.assertEqual(result.returncode, 0, result.stderr)

    def request(self, **overrides):
        req = dict(id='first', action='start', session='discussion', account='worker',
                   workspace=str(self.root), mode='review', prompt='A bounded task')
        req.update(overrides)
        self.reply(bridge.MARKER + json.dumps(req))
        return req

    def poll(self):
        proc = self.cli('bridge', 'poll')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def rows(self, table):
        with sqlite3.connect(self.home / 'bridge/state.sqlite3') as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM ' + table)]

    def invocations(self):
        p = self.home / 'invocations'
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def test_native_start_followup_and_idempotency(self):
        req = self.request()
        self.poll()
        self.assertEqual(self.rows('jobs')[0]['status'], 'completed')
        self.assertEqual(self.rows('conversations')[0]['native_id'], self.native_id)
        self.reply(bridge.MARKER + json.dumps(req))
        self.reply(bridge.MARKER + json.dumps(dict(id='second', action='send', session='discussion', prompt='Follow up')))
        self.poll()
        calls = self.invocations()
        self.assertEqual(len(calls), 2)
        self.assertIn('--resume', calls[1])
        self.assertIn(self.native_id, calls[1])
        self.assertEqual(len(self.messages), 2)
        self.assertTrue(all(m['text'].startswith(bridge.RESULT) for m in self.messages))

    def test_codex_start_and_resume(self):
        self.request(account='code')
        self.poll()
        self.reply(bridge.MARKER + json.dumps(dict(id='second', action='send', session='discussion', prompt='Continue')))
        self.poll()
        calls = self.invocations()
        self.assertIn('--json', calls[0])
        self.assertIn('read-only', calls[1])
        self.assertEqual(calls[1][-3:], ['resume', self.native_id, '-'])

    def test_work_uses_existing_worker_permissions(self):
        self.request(mode='work')
        self.poll()
        self.assertIn('bypassPermissions', self.invocations()[0])

    def test_unknown_account_root_and_fields_rejected(self):
        self.request(account='not-enabled')
        self.request(id='outside', workspace='/')
        self.request(id='extra', shell='touch forbidden')
        self.request(id='malformed-action', action=['start'])
        self.poll()
        self.assertEqual([r['status'] for r in self.rows('jobs')], ['rejected'] * 4)
        self.assertEqual(self.invocations(), [])

    def test_embedded_requests_and_user_messages_ignored(self):
        self.reply('Example: ' + bridge.MARKER + '{}')
        self.entries.append({'seq': str(len(self.entries)), 'kind': 'message', 'text': bridge.MARKER + '{}'})
        self.poll()
        self.assertEqual(self.rows('jobs'), [])

    def test_results_do_not_trigger_loops(self):
        self.request()
        self.poll()
        self.reply(bridge.RESULT + json.dumps({'reply': bridge.MARKER + '{}'}))
        self.poll()
        self.assertEqual(len(self.invocations()), 1)

    def test_reply_retry_does_not_repeat_worker(self):
        self.request()
        self.send_failure = 503
        self.poll()
        self.assertEqual(self.rows('outbox')[0]['status'], 'pending')
        self.send_failure = 0
        self.poll()
        self.assertEqual(len(self.invocations()), 1)
        self.assertEqual(self.rows('outbox')[0]['status'], 'sent')

    def test_uncertain_send_reuses_message_id(self):
        self.request()
        self.drop_send = True
        self.poll()
        self.drop_send = False
        self.poll()
        self.assertEqual(self.messages[0]['messageId'], self.messages[1]['messageId'])
        self.assertEqual(len(self.invocations()), 1)

    def test_crashed_job_not_replayed(self):
        self.request()
        self.poll()
        with sqlite3.connect(self.home / 'bridge/state.sqlite3') as db:
            db.execute("UPDATE jobs SET status='running'")
            db.execute("UPDATE conversations SET status='running'")
        self.poll()
        self.assertEqual(len(self.invocations()), 1)
        self.assertEqual(self.rows('jobs')[0]['status'], 'interrupted')
        self.assertEqual(self.rows('conversations')[0]['status'], 'interrupted')

    def test_worker_failure_blocks_followup(self):
        (self.home / 'fail').touch()
        self.request()
        self.poll()
        self.reply(bridge.MARKER + json.dumps(dict(id='second', action='send', session='discussion', prompt='Continue')))
        self.poll()
        self.assertEqual([r['status'] for r in self.rows('jobs')], ['failed', 'failed'])
        self.assertEqual(len(self.invocations()), 1)

    def test_close_prevents_followup(self):
        self.request()
        self.poll()
        self.reply(bridge.MARKER + json.dumps(dict(id='close', action='close', session='discussion')))
        self.poll()
        self.reply(bridge.MARKER + json.dumps(dict(id='second', action='send', session='discussion', prompt='Continue')))
        self.poll()
        self.assertEqual(self.rows('conversations')[0]['status'], 'closed')
        self.assertEqual(len(self.invocations()), 1)

    def test_cancel_preempts_queued_work(self):
        self.request()
        self.reply(bridge.MARKER + json.dumps(dict(id='cancel', action='cancel', request_id='first')))
        self.poll()
        self.assertEqual(self.rows('jobs')[0]['status'], 'cancelled')
        self.assertEqual(self.invocations(), [])

    def test_historical_requests_not_replayed(self):
        self.request()
        with sqlite3.connect(self.home / 'bridge/state.sqlite3') as db:
            db.execute('DELETE FROM cursors')
        self.poll()
        self.assertEqual(self.rows('jobs'), [])

    def test_identity_mismatch_never_executes(self):
        self.request()
        self.email = 'other@example.com'
        proc = self.cli('bridge', 'poll')
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.invocations(), [])
        self.assertNotIn('Traceback', proc.stderr)

    def test_native_metadata_private_and_plain_reply(self):
        prompt = self.root / 'direct.txt'
        prompt.write_text('hello')
        metadata = self.root / 'native.json'
        proc = self.cli('run', 'worker', '--prompt-file', str(prompt), '--workspace', str(self.root),
                        '--review', '--session-result', str(metadata))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), 'native reply')
        self.assertEqual(json.loads(metadata.read_text())['session_id'], self.native_id)
        self.assertEqual(metadata.stat().st_mode & 0o777, 0o600)

    def test_key_not_in_bridge_files(self):
        self.request()
        self.poll()
        for path in (self.home / 'bridge').rglob('*'):
            if path.is_file():
                self.assertNotIn(self.env['TEST_BOT_KEY'].encode(), path.read_bytes(), str(path))

    def test_symlink_escape_refused(self):
        link = self.root / 'escape'
        link.symlink_to('/')
        self.request(workspace=str(link))
        self.poll()
        self.assertEqual(self.invocations(), [])

    def test_followup_rechecks_policy(self):
        self.request()
        self.poll()
        p = self.home / 'bridge/config.json'
        cfg = json.loads(p.read_text()); cfg['accounts'] = ['code']; p.write_text(json.dumps(cfg))
        self.reply(bridge.MARKER + json.dumps(dict(id='second', action='send', session='discussion', prompt='Continue')))
        self.poll()
        self.assertEqual(len(self.invocations()), 1)
        self.assertEqual(self.rows('jobs')[-1]['status'], 'failed')

    def test_crashed_parent_keeps_worker_lock_until_child_exits(self):
        self.request()
        hold = self.home / 'hold'
        hold.touch()
        self.addCleanup(lambda: hold.unlink(missing_ok=True))
        proc = subprocess.Popen([sys.executable, str(SCRIPTS / 'second_wind.py'), 'bridge', 'poll'],
                                env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 8
            while not self.invocations() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(len(self.invocations()), 1)
            proc.kill()
            proc.wait(timeout=2)
            attempt = self.cli('bridge', 'poll')
            self.assertNotEqual(attempt.returncode, 0)
            self.assertIn('already running', attempt.stderr)
        finally:
            hold.unlink(missing_ok=True)
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.cli('bridge', 'poll')
            if result.returncode == 0:
                break
            time.sleep(0.05)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.invocations()), 1)
        self.assertEqual(self.rows('jobs')[0]['status'], 'interrupted')

    def test_changed_request_with_same_id_not_executed(self):
        self.request()
        self.poll()
        self.request(prompt='Different task with reused id')
        self.poll()
        self.assertEqual(len(self.invocations()), 1)

    def test_work_disabled_policy(self):
        p = self.home / 'bridge/config.json'
        cfg = json.loads(p.read_text()); cfg['modes'] = ['review']; p.write_text(json.dumps(cfg))
        self.request(mode='work')
        self.poll()
        self.assertEqual(self.rows('jobs')[0]['status'], 'rejected')
        self.assertEqual(self.invocations(), [])

    def test_status_includes_native_session_metadata(self):
        self.request()
        self.poll()
        self.reply(bridge.MARKER + json.dumps(dict(id='status', action='status')))
        self.poll()
        payload = json.loads(self.rows('jobs')[-1]['result'])
        self.assertEqual(payload['sessions'][0]['native_id'], self.native_id)

    def test_removed_bot_does_not_execute_or_receive(self):
        self.request()
        self.send_failure = 503
        self.poll()
        p = self.home / 'bridge/config.json'
        cfg = json.loads(p.read_text()); cfg['bots'] = []; p.write_text(json.dumps(cfg))
        self.send_failure = 0
        self.poll()
        self.assertEqual(self.rows('outbox')[0]['status'], 'pending')
        self.assertEqual(self.messages, [])

    def test_service_restart_waits_and_keeps_key_out_of_plist(self):
        calls, prints = [], []
        def launch(command, **kwargs):
            calls.append(command)
            if command[1] == 'print':
                prints.append(True)
                return subprocess.CompletedProcess(command, 0 if len(prints) == 1 else 113, '', '')
            return subprocess.CompletedProcess(command, 0, '', '')
        with mock.patch.dict(os.environ, self.env, clear=True), \
             mock.patch.object(bridge.sys, 'platform', 'darwin'), \
             mock.patch.object(bridge.sw.GrokBot, 'identity', return_value={'email': 'user@example.com'}), \
             mock.patch.object(Path, 'home', return_value=self.root), \
             mock.patch.object(bridge.subprocess, 'run', side_effect=launch), \
             mock.patch.object(bridge.time, 'sleep'):
            bridge.service(SimpleNamespace(action='start'))
        self.assertEqual([c[1] for c in calls], ['bootout', 'print', 'print', 'bootstrap'])
        plist = self.root / 'Library/LaunchAgents' / (bridge.LABEL + '.plist')
        self.assertNotIn(self.env['TEST_BOT_KEY'], plist.read_text())
        key_file = self.home / 'bridge/service-key'
        self.assertEqual(key_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(key_file.read_text(), self.env['TEST_BOT_KEY'])


# The imported fixture class must not be collected again by unittest discovery.
del GrokBotTests
