"""Conversation routes exercised over HTTP, with no vendor client or live key."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

RUNNER = Path(__file__).resolve().parents[1] / 'scripts' / 'second_wind.py'


class GrokBotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / 'state'
        self.home.mkdir()
        self.calls, self.messages = [], []
        self.entries = []
        self.busy = self.created = self.pending = False
        self.failure = 0
        self.delivery = 'accepted_temporal'
        self.reply_text = 'finished'
        self.email = 'user@example.com'
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def handle_request(self):
                from urllib.parse import parse_qs, urlsplit
                body = json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.headers.get('Content-Length') else None
                owner.calls.append((self.command, self.path, body, dict(self.headers)))
                if owner.failure:
                    self.send_response(owner.failure)
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'echo ' + owner.env['TEST_BOT_KEY']}).encode())
                    return
                path = urlsplit(self.path)
                if path.path == '/v0/me':
                    result = {'userEmail': owner.email}
                elif path.path.endswith('/sessions'):
                    result = {'id': 'bot/id', 'latestUpdatedSeq': str(len(owner.entries) - 1),
                              'createdAtMs': time.time() * 1000 if owner.created else 0}
                elif path.path.endswith('/messages'):
                    owner.messages.append(body)
                    owner.entries.append({'seq': str(len(owner.entries)), 'kind': 'message', 'text': body['text']})
                    if not owner.pending:
                        owner.reply(owner.reply_text)
                    result = {'delivery': owner.delivery, 'messageId': body['messageId']}
                elif path.path.endswith('/interrupt'):
                    owner.busy = False
                    result = {}
                else:
                    cursor = int(parse_qs(path.query)['afterUpdatedSeq'][0])
                    result = {'entries': owner.entries[cursor + 1:], 'latestUpdatedSeq': str(len(owner.entries) - 1),
                              'turn': {'idle': not owner.busy, 'inFlight': owner.busy, 'queued': 0}}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.env = dict(os.environ, SW_HOME=str(self.home), HOME=str(self.root), TEST_BOT_KEY='synthetic-secret-marker',
                        SW_GROKBOT_BASE_URL='http://127.0.0.1:%s' % self.server.server_port,
                        SW_GROKBOT_POLL_SECONDS='0.01', SW_NO_REFRESH='1')
        self.config = {'version': 4, 'accounts': {'team': {'kind': 'grokbot', 'label': 'Grok Bot',
                       'api_key_env': 'TEST_BOT_KEY', 'expected_email': self.email,
                       'bots': ['Assistant', 'Research'], 'default_bot': 'Assistant'}}}
        self.write_config()
        self.prompt = self.root / 'prompt.txt'
        self.prompt.write_text('Do the requested task')

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def write_config(self):
        (self.home / 'config.json').write_text(json.dumps(self.config))

    def reply(self, text):
        self.entries.append({'seq': str(len(self.entries)), 'kind': 'send-message', 'text': text})

    def cli(self, *args):
        return subprocess.run([sys.executable, str(RUNNER), *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def run_bot(self, *args):
        return self.cli('run', 'team', '--prompt-file', str(self.prompt), '--workspace', str(self.root), *args)

    def logs(self):
        return '\n'.join(p.read_text() for p in (self.home / 'log').glob('*'))

    def test_unknown_bot_makes_no_http_call(self):
        result = self.run_bot('--bot', 'Missing')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Assistant, Research', result.stderr)
        self.assertEqual(self.calls, [])

    def test_busy_sends_nothing_and_logs_failure(self):
        self.busy = True
        result = self.run_bot()
        self.assertEqual(result.returncode, 2)
        self.assertIn('busy', result.stderr)
        self.assertEqual(self.messages, [])
        self.assertIn('Exit code: 2', self.logs())

    def test_finished_review_and_headers(self):
        result = self.run_bot('--review', '--bot', 'assistant')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('finished', result.stdout)
        self.assertIn('Report only. Do not send, post, buy, delete, contact anyone or change anything.', self.messages[0]['text'])
        self.assertIn("cannot see the caller's files", self.messages[0]['text'])
        self.assertIn('not enforced', self.logs())
        self.assertEqual(self.calls[1][2], {'name': 'Assistant'})
        headers = {k.lower(): v for k, v in self.calls[0][3].items()}
        self.assertEqual(headers['x-cursor-client-type'], 'agent-serve')
        self.assertIn('x-request-id', headers)
        state = next(self.home.glob('grokbot-*.json'))
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        again = self.cli('grokbot', 'check', '--wait', '1')
        self.assertEqual(again.returncode, 4, again.stderr)
        self.assertNotIn('finished', again.stdout)

    def test_deadline_then_check_without_resend(self):
        self.pending = True
        # A busy page after sending avoids treating an old idle report as completion.
        original = self.messages
        def busy_after_send():
            while not original:
                time.sleep(0.001)
            self.busy = True
        thread = threading.Thread(target=busy_after_send)
        thread.start()
        result = self.run_bot('--work', '--timeout', '1')
        thread.join()
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn('never resend', result.stderr)
        self.reply('late reply')
        self.busy = False
        result = self.cli('grokbot', 'team', 'check', '--wait', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('late reply', result.stdout)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(len(list((self.home / 'log').glob('*.md'))), 2)

    def test_created_warning(self):
        self.created = True
        result = self.run_bot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('newly created', result.stderr)
        self.assertIn('newly created', self.logs())

    def test_withdrawn_route(self):
        self.failure = 404
        result = self.run_bot()
        self.assertEqual(result.returncode, 2)
        self.assertIn('Cursor may have withdrawn', result.stderr)

    def test_delivery_failure(self):
        self.delivery = 'rejected'
        result = self.run_bot()
        self.assertEqual(result.returncode, 2)
        self.assertIn('not accepted', result.stderr)

    def test_key_redacted_from_output_exchange_and_ledger(self):
        self.reply(self.env['TEST_BOT_KEY'])
        self.prompt.write_text('echo ' + self.env['TEST_BOT_KEY'])
        self.reply_text = self.env['TEST_BOT_KEY']
        result = self.run_bot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(self.env['TEST_BOT_KEY'], result.stdout + result.stderr + self.logs())
        self.delivery = self.env['TEST_BOT_KEY']
        result = self.run_bot()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(self.env['TEST_BOT_KEY'], result.stdout + result.stderr + self.logs())
        self.failure = 500
        result = self.cli('status', 'team')
        self.assertNotIn(self.env['TEST_BOT_KEY'], result.stdout + result.stderr)
        self.assertIn('[redacted]', result.stdout)

    def test_status_ready_mismatch_missing_and_unauthorised(self):
        result = self.cli('status', 'team')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('READY', result.stdout)
        self.assertIn(self.email, result.stdout)
        self.email = 'other@example.com'
        result = self.cli('status', 'team')
        self.assertIn('identity mismatch', result.stdout)
        self.assertNotEqual(self.run_bot().returncode, 0)
        self.assertEqual(self.messages, [])
        del self.env['TEST_BOT_KEY']
        result = self.cli('status', 'team')
        self.assertIn('NOT READY', result.stdout)
        self.assertIn('missing', result.stdout)
        self.env['TEST_BOT_KEY'] = 'synthetic-secret-marker'
        self.failure = 401
        result = self.cli('status', 'team')
        self.assertIn('NOT READY', result.stdout)
        self.assertIn('401', result.stdout)

    def test_setup_backup_verified_email_and_no_session_call(self):
        before = (self.home / 'config.json').read_bytes()
        result = self.cli('grokbot', 'setup', '--account', 'new-team', '--key-env', 'TEST_BOT_KEY',
                          '--bots', 'Assistant,Research', '--default', 'research')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.home / 'config.json').read_text())['accounts']['new-team']
        self.assertEqual(data['expected_email'], self.email)
        self.assertEqual(data['default_bot'], 'Research')
        self.assertEqual(next(self.home.glob('config.backup-*')).read_bytes(), before)
        self.assertEqual([call[1] for call in self.calls], ['/v0/me'])

    def test_invalid_setup_and_account_are_refused_before_http(self):
        result = self.cli('grokbot', 'setup', '--key-env', 'TEST_BOT_KEY',
                          '--bots', 'Assistant,assistant')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls, [])
        self.config['accounts']['team']['default_bot'] = 'Missing'
        self.write_config()
        self.assertEqual(self.run_bot().returncode, 2)
        self.assertEqual(self.calls, [])

    def test_empty_reply_is_four(self):
        self.reply_text = ''
        result = self.run_bot()
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn('empty reply', result.stderr)
        self.assertIn('Exit code: 4', self.logs())

    def test_key_file_permissions_read_and_interrupt(self):
        key_file = self.root / 'key'
        key_file.write_text(self.env['TEST_BOT_KEY'])
        profile = self.config['accounts']['team']
        del profile['api_key_env']
        profile['api_key_file'] = str(key_file)
        key_file.chmod(0o644)
        self.write_config()
        self.assertIn('chmod 600', self.cli('status').stdout)
        key_file.chmod(0o600)
        self.reply('recent reply')
        result = self.cli('grokbot', 'read', '--last', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('bot: recent reply', result.stdout)
        result = self.cli('grokbot', 'interrupt')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.calls[-1][1].endswith('/interrupt'))
        count = len(self.calls)
        result = self.cli('grokbot', 'bots')
        self.assertEqual(result.stdout, 'Assistant\nResearch\n')
        self.assertEqual(len(self.calls), count)


if __name__ == '__main__':
    unittest.main()
