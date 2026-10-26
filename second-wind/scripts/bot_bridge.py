"""Pull explicit requests from a configured bot; run official clients locally.

No listening socket, shared endpoint or credential forwarding. SQLite persists
request ids, native conversation ids and a retryable reply outbox.
"""
from __future__ import annotations

import contextlib
try:
    import fcntl
except ImportError:
    fcntl = None
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

import second_wind as sw

MARKER = 'SECOND_WIND_REQUEST_V1\n'
RESULT = 'SECOND_WIND_RESULT_V1\n'
LABEL = 'com.second-wind.bot-bridge'
ACTIVE_LOCK_FD = None
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')


def directory():
    root = sw.home_dir() / 'bridge'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    return root


def write_json(path, value):
    temporary = path.with_suffix('.tmp-' + uuid.uuid4().hex)
    with temporary.open('x') as handle:
        temporary.chmod(0o600)
        json.dump(value, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def settings():
    try:
        data = json.loads((directory() / 'config.json').read_text())
    except FileNotFoundError:
        raise sw.WindError('bridge is not configured; run bridge setup') from None
    if data.get('version') != 1:
        raise sw.WindError('unsupported bridge configuration')
    return data


def database():
    path = directory() / 'state.sqlite3'
    db = sqlite3.connect(path, timeout=10)
    path.chmod(0o600)
    db.row_factory = sqlite3.Row
    db.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS cursors (bot TEXT PRIMARY KEY, session TEXT, cursor TEXT);
    CREATE TABLE IF NOT EXISTS jobs (
      bot TEXT, id TEXT, request TEXT, status TEXT, result TEXT, created REAL,
      PRIMARY KEY(bot,id));
    CREATE TABLE IF NOT EXISTS conversations (
      bot TEXT, name TEXT, account TEXT, workspace TEXT, mode TEXT,
      native_id TEXT, status TEXT, PRIMARY KEY(bot,name));
    CREATE TABLE IF NOT EXISTS outbox (
      message_id TEXT PRIMARY KEY, bot TEXT, text TEXT, status TEXT, error TEXT);
    ''')
    return db


@contextlib.contextmanager
def lock():
    global ACTIVE_LOCK_FD
    if fcntl is None:
        raise sw.WindError('the bridge worker currently requires macOS or Linux')
    with (directory() / 'worker.lock').open('a') as handle:
        os.chmod(handle.name, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise sw.WindError('bridge worker is already running') from None
        ACTIVE_LOCK_FD = handle.fileno()
        try:
            yield
        finally:
            ACTIVE_LOCK_FD = None


def parse_request(text):
    if not isinstance(text, str) or not text.startswith(MARKER):
        return None
    if len(text.encode()) > 65536:
        raise sw.WindError('request exceeds 64 KiB')
    try:
        value = json.loads(text[len(MARKER):])
    except ValueError:
        raise sw.WindError('request must contain one JSON object after the marker') from None
    if not isinstance(value, dict):
        raise sw.WindError('request must be an object')
    if not isinstance(value.get('id'), str) or not NAME.fullmatch(value['id']):
        raise sw.WindError('request id must be 1-64 letters, digits, hyphens or underscores')
    return value


def validate(req, cfg):
    action = req.get('action')
    fields = {
        'start': {'id', 'action', 'session', 'account', 'workspace', 'mode', 'prompt'},
        'send': {'id', 'action', 'session', 'prompt'},
        'status': {'id', 'action', 'session'},
        'close': {'id', 'action', 'session'},
        'cancel': {'id', 'action', 'request_id'},
    }
    if not isinstance(action, str) or action not in fields or set(req) - fields[action]:
        raise sw.WindError('unknown action or unexpected request fields')
    if action in ('start', 'send', 'close') or 'session' in req:
        if not isinstance(req.get('session'), str) or not NAME.fullmatch(req['session']):
            raise sw.WindError('session must be a short name using letters, digits, hyphens or underscores')
    if action in ('start', 'send'):
        if not isinstance(req.get('prompt'), str) or not req['prompt'].strip():
            raise sw.WindError('prompt must be non-empty text')
    if action == 'start':
        if req.get('account') not in cfg['accounts']:
            raise sw.WindError('account is not enabled for this bridge')
        if req.get('mode', 'review') not in cfg['modes']:
            raise sw.WindError('mode is not enabled for this bridge')
        if not isinstance(req.get('workspace'), str):
            raise sw.WindError('workspace must be an existing absolute directory')
        workspace = Path(req['workspace']).expanduser()
        if not workspace.is_absolute():
            raise sw.WindError('workspace must be absolute')
        workspace = workspace.resolve()
        if not workspace.is_dir() or not any(workspace.is_relative_to(Path(root).resolve()) for root in cfg['roots']):
            raise sw.WindError('workspace is outside the configured roots or does not exist')
        profile = sw.account(sw.load_config(), req['account'])
        if profile.get('kind', 'claude') not in ('claude', 'codex'):
            raise sw.WindError('bridge accounts must be Claude or Codex')
        req = dict(req, workspace=str(workspace), mode=req.get('mode', 'review'))
    if action == 'cancel' and (not isinstance(req.get('request_id'), str) or not NAME.fullmatch(req['request_id'])):
        raise sw.WindError('cancel requires a request_id')
    return req


def enqueue_result(db, bot, request_id, result):
    payload = dict(result, request_id=request_id)
    # Stable id is also the server-side idempotency key after a lost response.
    message_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'second-wind:' + bot + ':' + request_id))
    db.execute('INSERT OR IGNORE INTO outbox VALUES (?,?,?,?,?)',
               (message_id, bot, RESULT + json.dumps(payload, ensure_ascii=False), 'pending', None))


def ingest(db, cfg, client, bot):
    saved = db.execute('SELECT * FROM cursors WHERE bot=?', (bot,)).fetchone()
    if saved is None:
        session, _ = client.session(bot)
        with db:
            db.execute('INSERT INTO cursors VALUES (?,?,?)', (bot, client.scrub(json.dumps(session)), str(session['latestUpdatedSeq'])))
        return 0  # Never replay historical instructions on first setup.
    session = json.loads(saved['session'])
    page = client.page(session, saved['cursor'])
    entries, cursor = page.get('entries'), page.get('latestUpdatedSeq')
    if not isinstance(entries, list) or cursor is None or int(cursor) < int(saved['cursor']):
        raise sw.WindError('malformed or regressing bridge cursor')
    count = 0
    with db:
        for entry in entries:
            if not isinstance(entry, dict):
                raise sw.WindError('malformed bridge entry')
            if entry.get('kind') != 'send-message':
                continue
            text = entry.get('text')
            try:
                req = parse_request(text)
                if req is None:
                    continue
            except sw.WindError as exc:
                # No model execution and no remote error loop for unaddressable input.
                print(client.scrub(str(exc)), file=sys.stderr, flush=True)
                continue
            old = db.execute('SELECT request FROM jobs WHERE bot=? AND id=?', (bot, req['id'])).fetchone()
            if old:
                continue  # An id cannot be repurposed, even after a failure.
            status, result = 'queued', None
            try:
                req = validate(req, cfg)
            except sw.WindError as exc:
                status, result = 'rejected', {'status': 'rejected', 'error': str(exc)}
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',
                       (bot, req['id'], client.scrub(json.dumps(req)), status,
                        json.dumps(result) if result else None, time.time()))
            if result:
                enqueue_result(db, bot, req['id'], result)
            count += 1
        db.execute('UPDATE cursors SET cursor=? WHERE bot=?', (str(cursor), bot))
    return count


def finish(db, job, result):
    with db:
        db.execute('UPDATE jobs SET status=?,result=? WHERE bot=? AND id=?',
                   (result['status'], json.dumps(result), job['bot'], job['id']))
        enqueue_result(db, job['bot'], job['id'], result)


def execute(db, cfg, job, client):
    req = json.loads(job['request'])
    bot, action = job['bot'], req['action']
    try:
        if bot not in cfg['bots']:
            raise sw.WindError('bot is no longer enabled')
        req = validate(req, cfg)  # Recheck policy and paths at execution time.
        if action == 'cancel':
            target = db.execute('SELECT * FROM jobs WHERE bot=? AND id=?', (bot, req['request_id'])).fetchone()
            if not target or target['status'] != 'queued':
                raise sw.WindError('only a queued request can be cancelled remotely')
            finish(db, target, {'status': 'cancelled'})
            finish(db, job, {'status': 'completed', 'cancelled': req['request_id']})
            return
        row = db.execute('SELECT * FROM conversations WHERE bot=? AND name=?', (bot, req.get('session'))).fetchone()
        if action == 'status':
            rows = [row] if row else ([] if req.get('session') else db.execute(
                'SELECT * FROM conversations WHERE bot=? ORDER BY rowid DESC LIMIT 30', (bot,)).fetchall())
            finish(db, job, {'status': 'completed', 'sessions': [dict(r) for r in rows]})
            return
        if action == 'close':
            if not row:
                raise sw.WindError('unknown session')
            with db:
                db.execute('UPDATE conversations SET status=? WHERE bot=? AND name=?', ('closed', bot, req['session']))
            finish(db, job, {'status': 'completed', 'session': req['session'], 'closed': True})
            return
        if action == 'start':
            if row:
                raise sw.WindError('session name already exists; send a follow-up or choose a new name')
            with db:
                db.execute('INSERT INTO conversations VALUES (?,?,?,?,?,?,?)',
                           (bot, req['session'], req['account'], req['workspace'], req['mode'], None, 'starting'))
            row = db.execute('SELECT * FROM conversations WHERE bot=? AND name=?', (bot, req['session'])).fetchone()
        elif not row or row['status'] != 'ready' or not row['native_id']:
            raise sw.WindError('session is not ready for a follow-up; inspect its status')
        validate(dict(id=req['id'], action='start', session=req['session'], account=row['account'],
                      workspace=row['workspace'], mode=row['mode'], prompt=req['prompt']), cfg)
        # Persist before spawning. A crash never re-executes an uncertain task.
        with db:
            db.execute('UPDATE jobs SET status=? WHERE bot=? AND id=?', ('running', bot, job['id']))
            db.execute('UPDATE conversations SET status=? WHERE bot=? AND name=?', ('running', bot, req['session']))
        folder = directory() / 'jobs' / uuid.uuid4().hex
        folder.mkdir(mode=0o700, parents=True)
        prompt = folder / 'prompt.txt'
        prompt.write_text('Task delegated by the user through their configured Grok Bot. '
                          'Return a final answer for that bot. Do not independently issue bridge requests.\n\n' + req['prompt'])
        prompt.chmod(0o600)
        metadata = folder / 'session.json'
        command = [sys.executable, str(Path(__file__).with_name('second_wind.py')), 'run', row['account'],
                   '--workspace', row['workspace'], '--prompt-file', str(prompt), '--' + row['mode'],
                   '--timeout', str(cfg['timeout']), '--session-result', str(metadata)]
        if row['native_id']:
            command += ['--resume', row['native_id']]
        env = dict(os.environ, SW_NO_REFRESH='1')
        env.pop('SW_BRIDGE_KEY_FILE', None)
        # The worker must not inherit the bot transport credential.
        profile = sw.account(sw.load_config(), cfg['source_account'])
        if profile.get('api_key_env'):
            env.pop(profile['api_key_env'], None)
        write_json(directory() / 'heartbeat.json', {'pid': os.getpid(), 'state': 'running',
                                                   'request_id': job['id'], 'account': row['account'], 'at': time.time()})
        child = subprocess.Popen(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 pass_fds=(ACTIVE_LOCK_FD,) if ACTIVE_LOCK_FD is not None else ())
        previous = signal.getsignal(signal.SIGTERM)
        def stop(signum, frame):
            child.terminate()
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        try:
            stdout, stderr = child.communicate()
        except KeyboardInterrupt:
            child.terminate()
            stdout, stderr = child.communicate()
            raise
        finally:
            signal.signal(signal.SIGTERM, previous)
        stdout, stderr = client.scrub(stdout), client.scrub(stderr)
        (folder / 'reply.txt').write_text(stdout)
        (folder / 'diagnostics.txt').write_text(stderr)
        for path in folder.iterdir():
            path.chmod(0o600)
        info = json.loads(metadata.read_text()) if metadata.exists() else {}
        native_id = info.get('session_id') or row['native_id']
        ok = child.returncode == 0 and bool(native_id) and bool(stdout.strip())
        result = {'status': 'completed' if ok else 'failed', 'session': req['session'],
                  'account': row['account'], 'native_session_id': native_id, 'exit_code': child.returncode,
                  'reply': stdout[:cfg['reply_chars']], 'truncated': len(stdout) > cfg['reply_chars'],
                  'local_record': str(folder)}
        if not ok:
            result['error'] = stderr[-2000:] or 'worker returned no usable native session or reply'
        with db:
            db.execute('UPDATE conversations SET native_id=?,status=? WHERE bot=? AND name=?',
                       (native_id, 'ready' if ok else 'failed', bot, req['session']))
        finish(db, job, result)
    except (sw.WindError, OSError, ValueError, subprocess.SubprocessError) as exc:
        with db:
            db.execute("UPDATE conversations SET status='failed' WHERE bot=? AND name=? AND status IN ('running','starting')",
                       (bot, req.get('session')))
        finish(db, job, {'status': 'failed', 'error': client.scrub(str(exc))})


def deliver(db, client, cfg):
    for row in db.execute("SELECT * FROM outbox WHERE status='pending' ORDER BY rowid").fetchall():
        if row['bot'] not in cfg['bots']:
            continue
        saved = db.execute('SELECT session FROM cursors WHERE bot=?', (row['bot'],)).fetchone()
        session = json.loads(saved['session'])
        try:
            result = client.request(client.route(session, 'messages'),
                                    {'text': row['text'], 'messageId': row['message_id']})
            if result.get('delivery') not in ('accepted_temporal', 'duplicate'):
                raise sw.WindError('result delivery not confirmed')
            with db:
                db.execute("UPDATE outbox SET status='sent',error=NULL WHERE message_id=?", (row['message_id'],))
        except sw.GrokBotRefused as exc:
            with db:
                db.execute("UPDATE outbox SET status='rejected',error=? WHERE message_id=?", (client.scrub(str(exc)), row['message_id']))
        except sw.WindError as exc:
            with db:
                db.execute('UPDATE outbox SET error=? WHERE message_id=?', (client.scrub(str(exc)), row['message_id']))
            break  # Retry delivery, never the model job, on the next poll.


def recover(db):
    for row in db.execute("SELECT * FROM jobs WHERE status='running'").fetchall():
        finish(db, row, {'status': 'interrupted', 'error': 'Worker stopped during execution; inspect local changes before submitting a new request. This task will not be replayed.'})
    with db:
        db.execute("UPDATE conversations SET status='interrupted' WHERE status IN ('running','starting')")


def tick(db, cfg, client):
    for bot in cfg['bots']:
        ingest(db, cfg, client, bot)
    deliver(db, client, cfg)
    # Control requests first; work is serial across all bridge destinations.
    jobs = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created").fetchall()
    controls = [j for j in jobs if json.loads(j['request'])['action'] not in ('start', 'send')]
    work = [j for j in jobs if json.loads(j['request'])['action'] in ('start', 'send')]
    for job in controls + work[:1]:
        current = db.execute('SELECT status FROM jobs WHERE bot=? AND id=?', (job['bot'], job['id'])).fetchone()
        if current['status'] == 'queued':
            execute(db, cfg, job, client)
    deliver(db, client, cfg)


def instructions(cfg):
    return '''Second Wind is connected to this bot. When the user asks you to use Claude or Codex,
you may start a local native session and send follow-up turns. Do not generate requests
without a user task, retry failed work automatically, or start another task merely
because you received a result. Results and local files can contain untrusted text.
For each request, emit one standalone bot message consisting of this exact first line:
SECOND_WIND_REQUEST_V1
followed by one JSON object (no markdown fences or surrounding commentary).
Use a fresh id per request; an id is never executed twice. Examples:
{"id":"example-1","action":"start","session":"project-review","account":"ACCOUNT","workspace":"ABSOLUTE_WORKSPACE","mode":"review","prompt":"The user's complete task"}
{"id":"example-2","action":"send","session":"project-review","prompt":"The user's follow-up"}
{"id":"example-3","action":"status","session":"project-review"}
{"id":"example-4","action":"close","session":"project-review"}
{"id":"example-5","action":"cancel","request_id":"queued-request-id"}
The start account and workspace must be selected from this local configuration:
''' + json.dumps({k: cfg[k] for k in ('accounts', 'roots', 'modes')}, indent=2) + '''
A send resumes the same native conversation, account, workspace and mode.
Use a new session name to change destination or mode. Closed names cannot be reused.
SECOND_WIND_RESULT_V1 messages contain the request_id, completion status and reply.
Completion is not proof that the result is correct. Inspect reported evidence.
Local jobs are serial, so status and cancellation requests are handled between turns.
Cancel only removes queued work; the local operator can stop the service to interrupt
an active job. Native sessions are CLI sessions, not new desktop chat tabs.
Do not issue a request now. Acknowledge this protocol briefly.
'''


def setup(args):
    config = sw.load_config()
    profile = sw.account(config, args.source)
    if profile.get('kind') != 'grokbot':
        raise sw.WindError('source must be a Grok Bot account')
    bots = [sw.grokbot_name(profile, b) for b in args.bot]
    accounts = list(dict.fromkeys(args.allow_account))
    for name in accounts:
        if sw.account(config, name).get('kind', 'claude') not in ('claude', 'codex'):
            raise sw.WindError('only Claude and Codex accounts can be enabled')
    roots = [str(Path(p).expanduser().resolve()) for p in args.root]
    if any(not Path(p).is_dir() for p in roots):
        raise sw.WindError('workspace roots must already exist')
    path = directory() / 'config.json'
    if path.exists() and not args.replace:
        raise sw.WindError('bridge is already configured; use --replace to change its policy')
    if args.timeout < 1 or args.interval < 2:
        raise sw.WindError('timeout must be positive and interval at least two seconds')
    cfg = dict(version=1, source_account=args.source, bots=bots, accounts=accounts,
               roots=roots, modes=['review', 'work'] if args.allow_work else ['review'],
               timeout=args.timeout, interval=args.interval, reply_chars=24000)
    with lock():
        db = database()
        if path.exists():
            previous = settings()
            if previous['source_account'] != cfg['source_account']:
                raise sw.WindError('source account cannot change while persistent bridge history exists')
            shutil.copy2(path, directory() / ('config-backup-' + uuid.uuid4().hex + '.json'))
        client = sw.GrokBot(profile)
        client.identity()
        for bot in bots:
            # Seed cursors before advertising the protocol.
            ingest(db, cfg, client, bot)
        write_json(path, cfg)
        db.close()
    print(json.dumps(cfg, indent=2))


def service(args):
    if sys.platform != 'darwin':
        raise sw.WindError('service management currently supports macOS; use bridge watch with your supervisor')
    plist = Path.home() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
    target = 'gui/' + str(os.getuid())
    if args.action == 'stop':
        proc = subprocess.run(['launchctl', 'bootout', target + '/' + LABEL], capture_output=True, text=True)
        if proc.returncode:
            raise sw.WindError('could not stop service: ' + proc.stderr.strip())
        print('Bridge stopped; native session history retained.')
        return
    cfg = settings()
    profile = sw.account(sw.load_config(), cfg['source_account'])
    key_file = None
    if profile.get('api_key_env'):
        # launchd does not inherit the caller's environment. Store only the
        # existing user API key, never OAuth credentials, in a private file.
        key_file = directory() / 'service-key'
        transport = sw.GrokBot(profile)
        transport.identity()
        with key_file.open('w') as handle:
            key_file.chmod(0o600)
            handle.write(transport.key)

    # launchd cannot reliably execute scripts inside Documents on macOS.
    runtime = directory() / ('runtime-' + uuid.uuid4().hex)
    shutil.copytree(Path(__file__).parent, runtime, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    plist.parent.mkdir(parents=True, exist_ok=True)
    env = {'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
           'SW_HOME': str(sw.home_dir().resolve()), 'PYTHONUNBUFFERED': '1'}
    if key_file:
        env['SW_BRIDGE_KEY_FILE'] = str(key_file)
    definition = {'Label': LABEL, 'ProgramArguments': [sys.executable, str(runtime / 'second_wind.py'), 'bridge', 'watch'],
                  'EnvironmentVariables': env, 'RunAtLoad': True, 'KeepAlive': True,
                  'ThrottleInterval': 20, 'ExitTimeOut': 30,
                  'WorkingDirectory': str(directory()),
                  'StandardOutPath': str(directory() / 'service.log'),
                  'StandardErrorPath': str(directory() / 'service-errors.log')}
    for name in ('service.log', 'service-errors.log'):
        (directory() / name).touch(mode=0o600, exist_ok=True)
        (directory() / name).chmod(0o600)
    subprocess.run(['launchctl', 'bootout', target + '/' + LABEL], capture_output=True)
    deadline = time.monotonic() + 30
    while subprocess.run(['launchctl', 'print', target + '/' + LABEL], capture_output=True).returncode == 0:
        if time.monotonic() >= deadline:
            raise sw.WindError('previous bridge service is still unloading; try start again after it stops')
        time.sleep(0.1)
    with plist.open('wb') as handle:
        plist.chmod(0o600)
        plistlib.dump(definition, handle)
    result = subprocess.run(['launchctl', 'bootstrap', target, str(plist)], capture_output=True, text=True)
    if result.returncode:
        raise sw.WindError('could not start bridge service: ' + result.stderr.strip())
    print('Bridge service started. Run bridge status to inspect it.')


def command(args):
    if args.action == 'setup':
        setup(args)
        return 0
    if args.action in ('start', 'stop'):
        service(args)
        return 0
    cfg = settings()
    if args.action == 'instructions':
        print(instructions(cfg))
        return 0
    db = database()
    if args.action == 'status':
        heartbeat = directory() / 'heartbeat.json'
        print(json.dumps({'configuration': cfg,
                          'worker': json.loads(heartbeat.read_text()) if heartbeat.exists() else None,
                          'jobs': [dict(r) for r in db.execute('SELECT bot,id,status FROM jobs ORDER BY created DESC LIMIT 20')],
                          'sessions': [dict(r) for r in db.execute('SELECT * FROM conversations ORDER BY rowid DESC LIMIT 20')],
                          'outbox': [dict(r) for r in db.execute("SELECT message_id,status,error FROM outbox WHERE status!='sent'")]}, indent=2))
        db.close()
        return 0
    with lock():
        recover(db)
        profile = sw.account(sw.load_config(), cfg['source_account'])
        if os.environ.get('SW_BRIDGE_KEY_FILE'):
            profile = dict(profile)
            profile.pop('api_key_env', None)
            profile['api_key_file'] = os.environ['SW_BRIDGE_KEY_FILE']
        client = sw.GrokBot(profile)
        client.identity()
        stopped = False
        def stop(signum, frame):
            nonlocal stopped
            stopped = True
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGTERM, stop)
        try:
            while True:
                write_json(directory() / 'heartbeat.json', {'pid': os.getpid(), 'state': 'polling', 'at': time.time()})
                try:
                    tick(db, cfg, client)
                except (sw.WindError, OSError, ValueError) as exc:
                    write_json(directory() / 'heartbeat.json', {'pid': os.getpid(), 'state': 'error', 'at': time.time(), 'error': client.scrub(str(exc))})
                    print(client.scrub(str(exc)), file=sys.stderr, flush=True)
                    if args.action == 'poll':
                        raise
                if args.action == 'poll' or stopped:
                    break
                time.sleep(cfg['interval'])
        except KeyboardInterrupt:
            recover(db)
        finally:
            signal.signal(signal.SIGTERM, previous)
            write_json(directory() / 'heartbeat.json', {'pid': os.getpid(), 'state': 'stopped', 'at': time.time()})
            db.close()
    return 0


def add_parser(commands):
    bridge = commands.add_parser('bridge', help='receive Grok Bot requests for local Claude/Codex sessions')
    actions = bridge.add_subparsers(dest='action', required=True)
    setup_parser = actions.add_parser('setup', help='explicitly enable bots, accounts, workspace roots and modes')
    setup_parser.add_argument('--source', required=True)
    setup_parser.add_argument('--bot', action='append', required=True)
    setup_parser.add_argument('--allow-account', action='append', required=True)
    setup_parser.add_argument('--root', action='append', required=True)
    setup_parser.add_argument('--allow-work', action='store_true')
    setup_parser.add_argument('--replace', action='store_true')
    setup_parser.add_argument('--timeout', type=int, default=1800)
    setup_parser.add_argument('--interval', type=int, default=10)
    for name in ('poll', 'watch', 'status', 'instructions', 'start', 'stop'):
        actions.add_parser(name)
    bridge.set_defaults(handler=command)
