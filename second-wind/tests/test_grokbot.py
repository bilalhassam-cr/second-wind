"""Conversation routes exercised over HTTP, with no vendor client or live key."""
import http.server
import json
import os
import signal
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
        self.route_failure = self.poll_failure = self.send_failure = 0
        self.polls_failed = 0
        self.null_turn = self.malformed = False
        self.created_value = None
        self.slow_session = False
        self.post_busy = 0
        self.drop_send = False
        self.delay_session = 0
        self.delay_send = 0
        self.neither = self.bad_body = False
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
                failure = owner.failure
                if self.path != '/v0/me':
                    failure = failure or owner.route_failure
                if self.path.endswith('/messages'):
                    failure = failure or owner.send_failure
                if '/entries?' in self.path and owner.messages and owner.poll_failure:
                    failure = owner.poll_failure
                    owner.polls_failed += 1
                if failure:
                    self.send_response(failure)
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'echo ' + owner.env['TEST_BOT_KEY']}).encode())
                    return
                path = urlsplit(self.path)
                if path.path == '/v0/me':
                    result = {'userEmail': owner.email}
                elif path.path.endswith('/sessions'):
                    if owner.delay_session:
                        time.sleep(owner.delay_session)
                    if owner.slow_session:
                        time.sleep(0.2)
                    result = {'id': 'bot/id', 'latestUpdatedSeq': str(len(owner.entries) - 1),
                              'createdAtMs': owner.created_value if owner.created else 0,
                              'echo': owner.env['TEST_BOT_KEY']}
                elif path.path.endswith('/messages'):
                    if owner.delay_send:
                        time.sleep(owner.delay_send)
                    owner.messages.append(body)
                    if owner.drop_send:
                        self.close_connection = True
                        return
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
                if '/entries?' in self.path:
                    if owner.messages and owner.post_busy:
                        result['turn'] = {'idle': False, 'inFlight': True, 'queued': None}
                        owner.post_busy -= 1
                    if owner.neither:
                        result['turn'] = {'idle': False, 'inFlight': False, 'queued': 0}
                    if owner.null_turn:
                        result['turn'].update(queued=None, inFlight=None)
                    if owner.malformed and owner.messages:
                        result.pop('latestUpdatedSeq')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                try:
                    self.wfile.write((b'broken' if owner.bad_body and self.path.endswith('/messages') else json.dumps(result).encode()))
                except (BrokenPipeError, ConnectionResetError):
                    pass

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
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn('no new replies', again.stdout)
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
        self.assertIn('never resend', result.stderr.lower())
        self.reply('late reply')
        self.busy = False
        result = self.cli('grokbot', 'team', 'check', '--wait', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('late reply', result.stdout)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(len(list((self.home / 'log').glob('*.md'))), 2)

    def test_created_bot_refused(self):
        self.created = True
        result = self.run_bot()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('empty bot', result.stderr)
        self.assertEqual(self.messages, [])

    def test_withdrawn_route(self):
        self.route_failure = 404
        result = self.run_bot()
        self.assertEqual(result.returncode, 2)
        self.assertIn('Cursor may have withdrawn', result.stderr)

    def test_delivery_failure(self):
        self.delivery = 'rejected'
        result = self.run_bot()
        self.assertEqual(result.returncode, 124)
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
        self.assertEqual(result.returncode, 124)
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
        self.assertIn('BLOCKED', result.stdout)
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
        for path in self.home.glob('config*'):
            self.assertNotIn(self.env['TEST_BOT_KEY'], path.read_text())

    def test_invalid_setup_and_account_are_refused_before_http(self):
        result = self.cli('grokbot', 'setup', '--key-env', 'TEST_BOT_KEY',
                          '--bots', 'Assistant,assistant')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls, [])
        self.config['accounts']['team']['default_bot'] = 'Missing'
        self.write_config()
        self.assertEqual(self.run_bot().returncode, 2)
        self.assertEqual(self.calls, [])

    def test_slow_start_is_124(self):
        self.reply_text = ''
        result = self.run_bot()
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn('never resend', result.stderr.lower())

    def test_post_send_failures_and_redaction(self):
        self.poll_failure = 503
        result = self.run_bot()
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertEqual(self.polls_failed, 2)
        self.assertIn('never resend', result.stderr.lower())
        self.assertNotIn(self.env['TEST_BOT_KEY'], result.stderr + self.logs())
        result = self.cli('grokbot', 'check', '--wait', '1')
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertNotIn(self.env['TEST_BOT_KEY'], result.stderr + self.logs())

    def test_busy_then_settled_empty_is_four(self):
        self.reply_text = ''
        self.post_busy = 1
        result = self.run_bot()
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn('never resend', result.stderr.lower())

    def test_status_without_pin_is_auth(self):
        del self.config['accounts']['team']['expected_email']
        self.write_config()
        result = self.cli('status', 'team')
        self.assertIn('AUTH', result.stdout)
        self.assertNotIn('READY', result.stdout)

    def test_send_uncertain_and_pre_send_failure(self):
        self.send_failure = 503
        result = self.run_bot()
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn('may have been delivered', result.stderr)
        self.send_failure = 0
        self.route_failure = 503
        result = self.run_bot()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('nothing sent', result.stderr.lower())

    def test_send_connection_lost_is_maybe_delivered(self):
        self.drop_send = True
        result = self.run_bot()
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn('may have been delivered', result.stderr)
        self.assertEqual(len(self.messages), 1)

    def test_session_lookup_has_deadline(self):
        self.delay_session = 15
        started = time.monotonic()
        result = self.run_bot('--timeout', '1')
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('nothing sent', result.stderr.lower())
        self.assertEqual(self.messages, [])

    def test_pre_send_timeout(self):
        self.env['SW_GROKBOT_POLL_SECONDS'] = '2'
        self.reply('history')
        # A non-idle pre-send page consumes the deadline without sending.
        self.neither = True
        result = self.run_bot('--timeout', '1')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('nothing sent', result.stderr.lower())
        self.assertEqual(self.messages, [])

    def test_modes_and_state_secrets(self):
        result = self.run_bot('--work')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Report only', self.messages[0]['text'])
        for path in self.home.glob('*.json'):
            self.assertNotIn(self.env['TEST_BOT_KEY'], path.read_text())
        self.assertIn('Credentials kept: TEST_BOT_KEY', self.logs())
        self.assertIn('No subprocess is started', self.logs())

    def test_null_turn_and_malformed_page(self):
        self.null_turn = True
        self.assertEqual(self.run_bot().returncode, 0)
        self.messages.clear()
        self.malformed = True
        result = self.run_bot()
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn('malformed', result.stderr)

    def test_names_spaces_and_other_kind_flags(self):
        self.config['accounts']['team'].update(bots=['Assistant '], default_bot='Assistant ')
        self.write_config()
        result = self.run_bot()
        self.assertEqual(result.returncode, 2)
        self.assertIn('surrounding spaces', result.stderr)
        self.assertEqual(self.calls, [])
        self.config['accounts']['team'] = {'kind': 'cursor', 'label': 'Cursor'}
        self.write_config()
        self.assertIn('requires --review or --work', self.run_bot().stderr)
        self.assertIn('--bot is only', self.run_bot('--bot', 'Assistant').stderr)

    def test_created_null_future_read_interrupt(self):
        self.created = True
        for created in (None, time.time() * 1000 + 60000):
            self.created_value = created
            for action in ('read', 'interrupt'):
                result = self.cli('grokbot', action)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('empty bot', result.stderr)
            self.assertEqual(self.run_bot().returncode, 2)
        self.assertEqual(self.messages, [])

    def test_check_no_new_does_not_record(self):
        self.assertEqual(self.run_bot().returncode, 0)
        before = self.logs()
        result = self.cli('grokbot', 'check', '--wait', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('no new replies', result.stdout)
        self.assertEqual(before, self.logs())
        self.busy = True
        result = self.cli('grokbot', 'check', '--wait', '1')
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn('never resend', result.stderr.lower())
        self.assertEqual(before, self.logs())

    def test_interrupted_run_recovers_unread(self):
        self.busy = False
        self.pending = True
        process = subprocess.Popen([sys.executable, str(RUNNER), 'run', 'team',
                                   '--prompt-file', str(self.prompt), '--workspace', str(self.root)],
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 5
        while not self.messages and time.monotonic() < deadline:
            time.sleep(0.001)
        self.busy = True
        self.reply('saved partial reply')
        while time.monotonic() < deadline:
            paths = list(self.home.glob('grokbot-*.json'))
            if paths and json.loads(paths[0].read_text()).get('unread'):
                break
            time.sleep(0.001)
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 130, err)
        self.assertIn('never resend', err)
        self.busy = False
        result = self.cli('grokbot', 'check', '--wait', '1')
        self.assertIn('saved partial reply', result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_round2_send_4xx(self):
        self.send_failure = 403
        result = self.run_bot()
        self.assertEqual(result.returncode, 2)
        self.assertIn('Nothing sent: Cursor rejected the message (403)', result.stderr)
        state = json.loads(next(self.home.glob('grokbot-*.json')).read_text())
        self.assertFalse(state.get('open'))

    def test_round2_unreadable_send(self):
        self.bad_body = True
        result = self.run_bot()
        self.assertEqual(result.returncode, 124)
        self.assertIn('may have been delivered', result.stderr)

    def test_round2_unread_refuses_new_run(self):
        self.assertEqual(self.run_bot().returncode, 0)
        path = next(self.home.glob('grokbot-*.json'))
        state = json.loads(path.read_text())
        state['unread'] = ['earlier reply']
        path.write_text(json.dumps(state))
        result = self.run_bot()
        self.assertEqual(result.returncode, 2)
        self.assertIn('unread replies from the last run; run check first', result.stderr)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(json.loads(path.read_text())['unread'], ['earlier reply'])

    def test_round2_check_busy_with_text(self):
        self.assertEqual(self.run_bot().returncode, 0)
        self.busy = True
        self.reply('partial')
        result = self.cli('grokbot', 'check', '--wait', '1')
        self.assertEqual(result.returncode, 124)
        self.assertIn('partial', result.stdout)
        self.assertIn('more may follow; check again; never resend', result.stderr)

    def test_round2_check_identity_error(self):
        self.assertEqual(self.run_bot().returncode, 0)
        self.failure = 503
        result = self.cli('grokbot', 'check', '--wait', '1')
        self.assertEqual(result.returncode, 124)
        self.assertIn('earlier message was delivered; check again; never resend', result.stderr)

    def test_round2_check_interrupted_keeps_unread(self):
        self.assertEqual(self.run_bot().returncode, 0)
        self.busy = True
        process = subprocess.Popen([sys.executable, str(RUNNER), 'grokbot', 'check', '--wait', '30'],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.reply('saved reply')
        path = next(self.home.glob('grokbot-*.json'))
        deadline = time.monotonic() + 5
        while not json.loads(path.read_text()).get('unread') and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertEqual(json.loads(path.read_text())['unread'], ['saved reply'])
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 130, err)
        self.assertEqual(json.loads(path.read_text())['unread'], ['saved reply'])
        self.assertIn('never resend', err)
        self.busy = False
        self.assertIn('saved reply', self.cli('grokbot', 'check', '--wait', '1').stdout)

    def test_round2_record_failure(self):
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        # Exercise a failure at the recording boundary after an accepted send.
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('round2_runner', RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        args = runner.parser().parse_args(['run', 'team', '--prompt-file', str(self.prompt),
                                           '--workspace', str(self.root)])
        with patch.dict(os.environ, self.env), patch.object(runner, 'record_exchange', side_effect=ValueError('record failed')), redirect_stderr(err):
            self.assertEqual(runner.command_run(args), 124)
        self.assertIn('Delivered. Collect with check; never resend.', err.getvalue())
        result = self.cli('grokbot', 'check', '--wait', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('finished', result.stdout)

    def test_round3_request_not_issued(self):
        import importlib.util
        import io
        from contextlib import redirect_stderr
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('deadline_runner', RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        original = runner.GrokBot.request
        def request(client, route, body=None, deadline=None):
            if route.endswith('/messages'):
                return original(client, route, body, time.monotonic() - 1)
            return original(client, route, body, deadline)
        args = runner.parser().parse_args(['run', 'team', '--prompt-file', str(self.prompt),
                                           '--workspace', str(self.root)])
        err = io.StringIO()
        with patch.dict(os.environ, self.env), patch.object(runner.GrokBot, 'request', request), redirect_stderr(err):
            self.assertEqual(runner.command_run(args), 2)
        self.assertIn('Nothing sent.', err.getvalue())
        self.assertEqual(self.messages, [])
        state = json.loads(next(self.home.glob('grokbot-*.json')).read_text())
        self.assertFalse(state.get('open'))

    def test_round4_marker_save_failure_is_nothing_sent(self):
        import importlib.util
        import io
        from contextlib import redirect_stderr
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('marker_runner', RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        original = runner.save_grokbot_state
        def save(path, data):
            if data.get('open'):
                raise OSError('disk full')
            return original(path, data)
        args = runner.parser().parse_args(['run', 'team', '--prompt-file', str(self.prompt),
                                           '--workspace', str(self.root)])
        err = io.StringIO()
        with patch.dict(os.environ, self.env), patch.object(runner, 'save_grokbot_state', save), redirect_stderr(err):
            self.assertEqual(runner.command_run(args), 2)
        self.assertIn('Nothing sent.', err.getvalue())
        self.assertNotIn('may have been delivered', err.getvalue())
        self.assertEqual(self.messages, [])

    def test_round4_check_interrupt_without_state_claims_nothing(self):
        import importlib.util
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('check_interrupt_runner', RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        def interrupted():
            raise KeyboardInterrupt
        args = runner.parser().parse_args(['grokbot', 'team', 'check'])
        err, out = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env), patch.object(runner, 'load_config', interrupted), \
                redirect_stderr(err), redirect_stdout(out):
            self.assertEqual(runner.check_grokbot(args), 130)
        self.assertNotIn('delivered', err.getvalue() + out.getvalue())

    def test_round3_final_handler_ignores_interrupts(self):
        import importlib.util
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('signals_runner', RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        original = runner.record_exchange
        def record(*args, **kwargs):
            self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)
            self.assertEqual(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
            os.kill(os.getpid(), signal.SIGTERM)
            os.kill(os.getpid(), signal.SIGINT)
            return original(*args, **kwargs)
        args = runner.parser().parse_args(['run', 'team', '--prompt-file', str(self.prompt),
                                           '--workspace', str(self.root)])
        previous = [signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)]
        with patch.dict(os.environ, self.env), patch.object(runner, 'record_exchange', record), redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(runner.command_run(args), 0)
        self.assertEqual(previous, [signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)])

    def test_round3_open_message(self):
        self.pending = True
        result = self.run_bot('--timeout', '1')
        self.assertEqual(result.returncode, 124, result.stderr)
        path = next(self.home.glob('grokbot-*.json'))
        self.assertTrue(json.loads(path.read_text())['open'])
        self.reply('late reply')
        result = self.run_bot()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('an earlier message has not been collected; run check first', result.stderr)
        result = self.cli('grokbot', 'check', '--wait', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('late reply', result.stdout)
        self.assertFalse(json.loads(path.read_text())['open'])
        self.pending = False
        self.assertEqual(self.run_bot().returncode, 0)

    def test_round3_check_usage(self):
        for arguments in ([], ['--bot', 'Missing'], ['--wait', '-1']):
            result = self.cli('grokbot', 'check', *arguments)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn('delivered', result.stderr)
            self.assertNotIn('never resend', result.stderr)
        result = self.cli('grokbot', 'missing', 'check')
        self.assertEqual(result.returncode, 2)
        (self.home / 'config.json').write_text('broken')
        self.assertEqual(self.cli('grokbot', 'check').returncode, 2)

    def test_round3_check_missing_key_records(self):
        self.assertEqual(self.run_bot().returncode, 0)
        self.env.pop('TEST_BOT_KEY')
        result = self.cli('grokbot', 'check', '--wait', '1')
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn('key is missing', self.logs())
        self.assertIn('Exit code: 124', self.logs())

    def test_round3_interrupt_before_and_sending(self):
        for sending in (False, True):
            with self.subTest(sending=sending):
                self.delay_session = 0 if sending else 15
                self.pending = True
                self.delay_send = 15 if sending else 0
                self.calls.clear()
                process = subprocess.Popen([sys.executable, str(RUNNER), 'run', 'team',
                                           '--prompt-file', str(self.prompt), '--workspace', str(self.root)],
                                          env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                deadline = time.monotonic() + 5
                suffix = '/messages' if sending else '/sessions'
                while not any(call[1].endswith(suffix) for call in self.calls) and time.monotonic() < deadline:
                    time.sleep(0.001)
                self.assertTrue(any(call[1].endswith(suffix) for call in self.calls))
                process.send_signal(signal.SIGTERM)
                out, err = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 130, err)
                self.assertIn('may have been delivered' if sending else 'Nothing sent.', err)

    def test_shell_default_is_review_for_bot(self):
        self.config['defaults'] = {'mode': 'work'}
        self.write_config()
        result = subprocess.run(['/bin/sh', str(RUNNER.with_name('run.sh')), 'team', str(self.prompt)],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Report only', self.messages[0]['text'])

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
