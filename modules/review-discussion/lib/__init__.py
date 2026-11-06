"""Shared review event consumer. Standard library only; no harness API here."""
import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from jarvis import Storage, Trace, is_autonomous, mode, registry
from jarvis.storage import default_state_dir

SLUG = 'review-discussion'
PROCESS = SLUG + '-process.json'
JOURNAL = SLUG + '.jsonl'
ID = re.compile(r'^[A-Za-z0-9_-]{1,150}$')
CURSOR = re.compile(r'^(0|[1-9][0-9]*)$')
INTERVAL = 5
HTTP_TIMEOUT = 8
# 50 bounded comments plus 8k-codepoint quotes, including JSON escaping.
MAX_RESPONSE = 8_000_000
MAX_QUOTE_PREVIEW = 240


def origin(value):
    """A credential can only be sent to an explicitly selected Sites origin."""
    try:
        url = urllib.parse.urlsplit(value)
        valid = (url.scheme == 'https' and url.hostname
                 and url.hostname.endswith('.chatgpt.site')
                 and url.hostname != '.chatgpt.site' and not url.username
                 and not url.password and not url.query and not url.fragment
                 and url.path in ('', '/') and url.port in (None, 443)
                 and not any(c.isspace() for c in value))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError('review_url_must_be_https_site_origin')
    return 'https://' + url.hostname


def identifier(value, name):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('invalid_' + name)
    return value


def cursor(value):
    if not isinstance(value, str) or not CURSOR.fullmatch(value) or len(value) > 16:
        raise ValueError('invalid_cursor')
    return value


@dataclass(frozen=True)
class Config:
    url: str
    session: str
    target: str
    harness: str

    @classmethod
    def from_env(cls, env, session_id, harness):
        target = str(uuid.UUID(env.get('REVIEW_TARGET_THREAD') or session_id))
        if target != str(uuid.UUID(session_id)):
            raise ValueError('review_target_must_match_current_session')
        return cls(origin(env.get('REVIEW_URL', '')),
                   identifier(env.get('REVIEW_SESSION'), 'session'),
                   target, identifier(harness, 'harness'))

    @property
    def key(self):
        digest = hashlib.sha256(json.dumps([
            self.url, self.session, self.target], separators=(',', ':')).encode()).hexdigest()
        return SLUG + '-sent-' + digest[:24] + '.json'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base, token):
        self.base = origin(base)
        if not isinstance(token, str) or not token or len(token) > 8192 or any(
                ord(c) < 32 or ord(c) == 127 for c in token):
            raise ValueError('review_token_missing_or_invalid')
        self.token = token

    def request(self, path, payload):
        request = urllib.request.Request(
            self.base + '/api/delivery/' + path,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'User-Agent': 'Mozilla/5.0 Jarvis-Review-Delivery/1',
                     'X-Review-Client': 'codex',
                     'OAI-Sites-Authorization': 'Bearer ' + self.token}, method='POST')
        try:
            with urllib.request.build_opener(NoRedirect()).open(
                    request, timeout=HTTP_TIMEOUT) as response:
                if response.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise RuntimeError('review_response_not_json')
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise RuntimeError('review_response_too_large')
                result = json.loads(raw.decode('utf-8'))
                if not isinstance(result, dict):
                    raise ValueError('review_response_invalid')
                return result
        except urllib.error.HTTPError as error:
            # Never include response bodies, request headers, or token in traces.
            raise RuntimeError('review_http_' + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise RuntimeError('review_connection_failed') from None
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError('review_response_invalid') from None

    def claim(self, session, target, instance):
        return self.request('claim', {'session': session, 'target': target, 'instance': instance})

    def ack(self, session, target, instance, cursor, event_id):
        return self.request('ack', {'session': session, 'target': target, 'instance': instance,
                                    'cursor': cursor, 'eventId': event_id})


def validate_event(item, config):
    if not isinstance(item, dict) or item.get('name') != 'comment.created':
        raise ValueError('invalid_comment_event')
    identifier(item.get('eventId'), 'event_id')
    cursor(item.get('cursor'))
    data = item.get('data')
    if not isinstance(data, dict) or data.get('session') != config.session:
        raise ValueError('event_session_mismatch')
    for key in ('review', 'thread', 'message_id'):
        identifier(data.get(key), key)
    for key in ('text', 'quote', 'url'):
        if not isinstance(data.get(key), str) or len(data[key]) > 100_000:
            raise ValueError('invalid_event_' + key)
    if not isinstance(data.get('quote_truncated'), bool):
        raise ValueError('invalid_event_quote_truncated')
    expected = config.url + '/?session=' + urllib.parse.quote(config.session, safe='')
    if data['url'] != expected:
        raise ValueError('event_url_mismatch')
    if not isinstance(item.get('timestamp'), str) or len(item['timestamp']) > 100:
        raise ValueError('invalid_event_timestamp')
    return item


def message(item):
    data = item['data']
    quote = data['quote']
    is_fragment = data['quote_truncated'] or len(quote) > MAX_QUOTE_PREVIEW
    if len(quote) > MAX_QUOTE_PREVIEW:
        quote = quote[:MAX_QUOTE_PREVIEW].rstrip() + '…'
    # Preserve the exact event, revision and thread in the source link, without a
    # visible machine envelope or another local copy of private comment data.
    link = (data['url'] + '&review=' + urllib.parse.quote(data['review'], safe='')
            + '&thread=' + urllib.parse.quote(data['thread'], safe='')
            + '#event=' + urllib.parse.quote(item['eventId'], safe=''))
    label = 'К тексту (фрагмент):' if is_fragment else 'К тексту:'

    def quote_lines(text):
        return '\n'.join('> ' + line for line in text.split('\n'))

    return (quote_lines(data['text']) + '\n\n' + label + '\n' + quote_lines(quote)
            + '\n\n[Обсуждение](' + link + ')')


class Consumer:
    def __init__(self, storage, config, adapter, client, instance=None):
        self.storage, self.config, self.adapter, self.client = storage, config, adapter, client
        self.instance = instance or str(uuid.uuid4())

    def step(self):
        config = self.config
        claim = self.client.claim(config.session, config.target, self.instance)
        server_cursor = cursor(claim.get('cursor'))
        if not isinstance(claim.get('busy'), bool):
            raise ValueError('invalid_claim_busy')
        if claim['busy']:
            return 'busy'
        if not isinstance(claim.get('leaseSeconds'), int) or claim['leaseSeconds'] < 30:
            raise ValueError('invalid_claim_lease')
        events = claim.get('events')
        if not isinstance(events, list) or len(events) > 50:
            raise ValueError('invalid_claim_events')
        if not events:
            return 'idle'
        # Validate the entire batch before delivering any of it. The next claim
        # re-fetches the remainder; one queue operation per lease renewal.
        last = int(server_cursor)
        for item in events:
            validate_event(item, config)
            if int(item['cursor']) <= last:
                raise ValueError('event_cursor_not_increasing')
            last = int(item['cursor'])
        item = events[0]
        sent = self.storage.read_json(config.key, default={})
        if not isinstance(sent, dict):
            raise ValueError('invalid_sent_journal')
        if item['eventId'] not in sent:
            queue_id = self.adapter.send(config.target, message(item))
            sent[item['eventId']] = {'queueId': identifier(queue_id, 'queue_id'),
                                     'cursor': item['cursor'], 'at': time.time()}
            # ACK failure must never cause another native queue submission.
            self.storage.write_json(config.key, sent)
            sync_state(self.storage, config.key)
        acknowledged = self.client.ack(config.session, config.target, self.instance,
                                       item['cursor'], item['eventId'])
        if int(cursor(acknowledged.get('cursor'))) < int(item['cursor']):
            raise ValueError('ack_cursor_not_advanced')
        note(self.storage, 'delivered', eventId=item['eventId'], cursor=item['cursor'])
        return 'delivered'


class ProcessLock:
    """Lifetime flock is authoritative; PID is diagnostic, never a kill target."""
    def __init__(self, storage):
        self.storage, self.handle = storage, None

    def acquire(self):
        path = self.storage.path(SLUG + '.lock')
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open('a+')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self.handle = handle
        return True

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None


def note(storage, action, **fields):
    storage.append_line(JOURNAL, json.dumps({'at': time.time(), 'action': action, **fields}))


def sync_state(storage, name):
    # Atomic rename protects readers; fsync commits the receipt before remote ACK.
    for path in (storage.path(name), storage.session_dir):
        handle = os.open(path, os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)


def record(storage, module_dir, action, delivered=True, env=None):
    decision = mode.read(SLUG, storage, Path(mode.PERSONAL_CONFIG).expanduser(),
                         registry.modules_dir(module_dir) / mode.SOURCE_CONFIG_NAME)
    Trace(storage).write(event='notification', module=SLUG, module_class='Consumer',
                         response='silence' if delivered else 'error',
                         mode_enabled=decision.enabled, mode_source=decision.source,
                         autonomous=is_autonomous(env), delivered=delivered, reason=action,
                         not_delivered_reason=None if delivered else action)


def status(storage):
    value = storage.read_json(SLUG + '-status.json', default={'state': 'not_started'})
    process = storage.read_json(PROCESS, default={})
    pid = process.get('pid')
    alive = False
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
            alive = True
        except PermissionError:
            alive = True
        except ProcessLookupError:
            pass
    lock = ProcessLock(storage)
    free = lock.acquire()
    lock.close()
    return {**value, 'running': not free, 'pidAlive': alive, 'process': process}


def get_adapter(module_dir, harness, env):
    found = [item for item in registry.adapters(module_dir, SLUG)
             if item.harness == harness]
    if len(found) != 1:
        raise RuntimeError('review_delivery_adapter_missing_or_ambiguous')
    part = registry.library_of(found[0])
    if part is None:
        raise RuntimeError('review_delivery_adapter_has_no_library')
    return part.load().Adapter(env)


def prepare(module_dir):
    """No installer work: prepare the portable entrypoint at session start."""
    binary = module_dir / 'bin/review-discussion'
    binary.parent.mkdir(parents=True, exist_ok=True)
    content = (module_dir / 'data/launcher.py').read_text(encoding='utf-8')
    if not binary.exists() or binary.read_text(encoding='utf-8') != content:
        temporary = binary.with_name(binary.name + '.' + str(os.getpid()) + '.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.chmod(0o755)
        os.replace(temporary, binary)
    return binary


def start(config, storage, module_dir, env):
    # A second short lock serializes launch attempts until the child holds its
    # lifetime lock. Parent hands that lock to the child rather than racing it.
    lock = ProcessLock(storage)
    if not lock.acquire():
        current = storage.read_json(PROCESS, default={})
        if any(current.get(key) != value for key, value in (
                ('target', config.target), ('session', config.session), ('url', config.url))):
            raise RuntimeError('review_consumer_binding_mismatch')
        return 'already_running'
    try:
        binary = prepare(module_dir)
        command = [sys.executable, str(binary), 'run', '--session', config.target,
                   '--harness', config.harness, '--state-dir', str(storage.persistent_dir),
                   '--lock-fd', str(lock.handle.fileno())]
        child = subprocess.Popen(command, env=dict(env), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True, pass_fds=(lock.handle.fileno(),))
        storage.write_json(PROCESS, {'pid': child.pid, 'target': config.target,
                                    'session': config.session, 'url': config.url, 'at': time.time()})
        note(storage, 'started', pid=child.pid)
        return 'started'
    finally:
        lock.close()


def enabled(storage, module_dir):
    return mode.read(SLUG, storage, Path(mode.PERSONAL_CONFIG).expanduser(),
                     registry.modules_dir(module_dir) / mode.SOURCE_CONFIG_NAME).enabled


def serve(config, storage, module_dir, env, lock_fd=None):
    lock = ProcessLock(storage)
    if lock_fd is not None:
        lock.handle = os.fdopen(lock_fd, 'a+')
    elif not lock.acquire():
        return 0
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    try:
        adapter = get_adapter(module_dir, config.harness, env)
        client = Client(config.url, env.get('REVIEW_TOKEN', ''))
        consumer = Consumer(storage, config, adapter, client)
        failures = 0
        while not stopping and enabled(storage, module_dir):
            try:
                result = consumer.step()
                if result == 'delivered':
                    record(storage, module_dir, 'review comment queued and acknowledged', env=env)
                failures = 0
                delay = 0 if result == 'delivered' else INTERVAL
                storage.write_json(SLUG + '-status.json', {
                    'at': time.time(), 'state': result, 'pid': os.getpid(),
                    'target': config.target, 'session': config.session})
            except Exception as error:
                failures += 1
                delay = min(60, INTERVAL * 2 ** min(failures - 1, 4))
                # Exception messages may contain arbitrary external data.
                reason = str(error) if re.fullmatch(r'[a-z][a-z0-9_]*', str(error)) else type(error).__name__
                note(storage, 'retry', reason=reason, delay=delay)
                record(storage, module_dir, reason, delivered=False, env=env)
                storage.write_json(SLUG + '-status.json', {
                    'at': time.time(), 'state': 'retry', 'reason': reason, 'delay': delay})
            # Small waits let process signals take effect during retry backoff.
            deadline = time.monotonic() + delay
            while not stopping and time.monotonic() < deadline:
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        note(storage, 'stopped')
        return 0
    finally:
        lock.close()


def main(module_dir, argv=None):
    parser = argparse.ArgumentParser(description='Portable review comment delivery')
    parser.add_argument('command', choices=('start', 'run', 'check', 'status', 'stop'))
    parser.add_argument('--session', required=True, help='Current chat UUID; never a name')
    parser.add_argument('--harness', required=True)
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--lock-fd', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--token-stdin', action='store_true')
    args = parser.parse_args(argv)
    env = dict(os.environ)
    if args.token_stdin:
        env['REVIEW_TOKEN'] = sys.stdin.readline(8194).strip()
    storage = Storage(args.state_dir or default_state_dir(module_dir), str(uuid.UUID(args.session)))
    if args.command in ('status', 'stop'):
        if args.command == 'stop':
            mode.set_session(SLUG, False, storage)
        print(json.dumps(status(storage)))
        return 0
    try:
        config = Config.from_env(env, args.session, args.harness)
        Client(config.url, env.get('REVIEW_TOKEN', ''))
        get_adapter(module_dir, config.harness, env)
        if args.command == 'check':
            print('Configuration valid; no network requests or messages sent.')
            return 0
        if not enabled(storage, module_dir):
            print('review-discussion mode is off')
            return 1
        if args.command == 'start':
            result = start(config, storage, module_dir, env)
            record(storage, module_dir, 'review consumer ' + result, env=env)
            print(result)
            return 0
        return serve(config, storage, module_dir, env, lock_fd=args.lock_fd)
    except Exception as error:
        reason = str(error) if re.fullmatch(r'[a-z][a-z0-9_]*', str(error)) else type(error).__name__
        note(storage, 'failed', reason=reason)
        storage.write_json(SLUG + '-status.json', {'at': time.time(), 'state': 'failed', 'reason': reason})
        record(storage, module_dir, reason, delivered=False, env=env)
        print('review-discussion: ' + reason, file=sys.stderr)
        return 1
