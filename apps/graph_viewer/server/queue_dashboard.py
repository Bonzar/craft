"""Read-only status through the existing owner-authenticated Graph web service."""
from __future__ import annotations
import asyncio
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import re
import sqlite3
import stat
import time

from prototypes.assertion_memory.queue_status import build_queue_status

SCOPES = {
    'test': (Path('/srv/assertion-release/test'), Path('/srv/assertion-release/test/state/fictional.sqlite3')),
    'prod': (Path('/srv/assertion-release/production'), Path('/var/lib/assertion-memory/memory.sqlite3')),
}
TAG = re.compile(r'assertion-memory-v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]{1,30})?\Z')
RELEASE = re.compile(r'([a-f0-9]{40})-[a-f0-9]{64}\Z')
CURSOR = re.compile(r'(test|prod):[0-9a-f]{16}:[0-9]{1,15}\Z')


def _small(path, size, *, owner=None):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as f:
        entry = os.fstat(f.fileno())
        if (not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1 or entry.st_mode & 0o022
                or owner is not None and entry.st_uid != owner):
            raise ValueError('Untrusted dashboard source')
        data = f.read(size + 1)
        if len(data) > size:
            raise ValueError('Oversized dashboard source')
        return data


def _connect(db):
    if db.resolve(strict=True) != db or not stat.S_ISREG(db.stat().st_mode) or db.stat().st_nlink != 1:
        raise ValueError('Wrong canonical database')
    connection = sqlite3.connect(db.as_uri()+'?mode=ro', uri=True, isolation_level=None, timeout=1)
    try:
        connection.execute('PRAGMA query_only=ON')
    except BaseException:
        connection.close()
        raise
    return connection


class HostedQueueDashboard:
    def __init__(self, stage, config, source):
        if stage not in SCOPES:
            raise ValueError('Unrecognized hosted dashboard lane')
        root, canonical = SCOPES[stage]
        if Path(config) != root/'state'/'http.json' or Path(source) != canonical:
            raise ValueError('Expected canonical lane configuration and database')
        self.stage, self.root, self.db, self.config = stage, root, canonical, Path(config)
        self.epoch = secrets.token_hex(8)
        self.streams = asyncio.Semaphore(8)

    def metadata(self):
        release = (self.root/'current').resolve(strict=True)
        local = Path(__file__).resolve().parents[3]
        match = RELEASE.fullmatch(release.name)
        if match is None or release != local or release.parent != self.root/'releases':
            raise ValueError('Graph service is not loaded from the active lane release')
        tag_raw = _small(release/'BASE_RELEASE_TAG', 128)
        tag = tag_raw.decode('ascii').strip()
        manifest = _small(release/'SHA256SUMS', 5*1024*1024).decode('ascii').splitlines()
        if (not TAG.fullmatch(tag)
                or manifest.count(sha256(tag_raw).hexdigest()+'  ./BASE_RELEASE_TAG') != 1
                or _small(release/'REVISION',128).decode('ascii').strip() != match[1]):
            raise ValueError('Unverified release identity')
        s = json.loads(_small(self.config, 20_000))['service']
        if s.get('db') != str(self.db) or s['provider'] not in {'none','subprocess','local','codex'}:
            raise ValueError('Expected canonical scheduler configuration')
        # Presence of interactive work as a configured feature is independent of
        # whether the lazily-created queue already has rows.
        bridge_config = Path('/etc/assertion-release')/self.root.name/'private-operations.json'
        capabilities = json.loads(_small(bridge_config, 20_000, owner=0))['capabilities']
        return dict(stage=self.stage, release_version=tag,
                    processing_session_ids=s.get('processing_session_ids'),
                    auto_process_fresh_mac=s.get('auto_process_fresh_mac',False),
                    model_configured=s['provider']!='none',
                    interactive_enabled='intake_process' in capabilities)

    def snapshot(self, *, limit, offset):
        fields = self.metadata()
        with closing(_connect(self.db)) as conn:
            conn.execute('BEGIN')
            deadline=time.monotonic()+2
            conn.set_progress_handler(lambda:time.monotonic()>=deadline, 2000)
            try:
                result=build_queue_status(conn,limit=limit,offset=offset,**fields)
            finally:
                conn.set_progress_handler(None,0)
                conn.rollback()
        if self.metadata()!=fields:
            raise ValueError('Hosted dashboard configuration changed')
        return result

    async def watch(self, publisher):
        # One short PRAGMA per process at a bounded cadence. Never opens a
        # read transaction between ticks, never holds a WAL snapshot.
        while True:
            try:
                info = await asyncio.to_thread(self.metadata)
                original = self.db.stat()
                with closing(_connect(self.db)) as conn:
                    prior = conn.execute('PRAGMA data_version').fetchone()[0]
                    check_at = time.monotonic() + 4
                    while True:
                        await asyncio.sleep(.35)
                        if time.monotonic() >= check_at:
                            live = await asyncio.to_thread(self.metadata)
                            if live != info or not os.path.samestat(original,self.db.stat()):
                                publisher('queue_changed', {'stage':self.stage})
                                break
                            check_at = time.monotonic()+4
                        version = conn.execute('PRAGMA data_version').fetchone()[0]
                        if prior != version:
                            publisher('queue_changed', {'stage':self.stage})
                            prior=version
            except (OSError, ValueError, KeyError, sqlite3.Error):
                await asyncio.sleep(2)


def wire_id(dashboard, cursor):
    return f'{dashboard.stage}:{dashboard.epoch}:{cursor}'


def decode_cursor(dashboard, raw, current, oldest):
    if raw is None:
        return current, True
    if not CURSOR.fullmatch(raw):
        return current, True
    stage, epoch, index = raw.split(':')
    index = int(index)
    if (stage, epoch) != (dashboard.stage, dashboard.epoch) or index > current or index < oldest - 1:
        return current, True
    return index, False


async def dashboard_events(request, manager, dashboard):
    from .bridge_auth import stream_authorized

    def emit(kind, cursor):
        ident = wire_id(dashboard,cursor)
        return f'id: {ident}\nevent: {kind}\ndata: {{"stage":"{dashboard.stage}"}}\n\n'

    end = time.monotonic() + 90
    async with dashboard.streams:
        raw = request.headers.get('last-event-id')
        cursor, resync = decode_cursor(dashboard, raw, manager.cursor,
                                       manager.journal[0][0] if manager.journal else manager.cursor)
        if resync:
            yield emit('resync', cursor)
        else:
            yield ': connected\n\n'
        ping = time.monotonic()
        while not manager.closed and time.monotonic() < end:
            if await request.is_disconnected() or not await asyncio.to_thread(stream_authorized,request):
                return
            # Metadata is unavailable on activation drift; clients keep using
            # the authenticated snapshot and reconnect rather than inventing events.
            if manager.journal and cursor < manager.journal[0][0] - 1:
                cursor = manager.cursor
                yield emit('resync',cursor)
            for number,name,payload in list(manager.journal):
                if number<=cursor:
                    continue
                cursor = number
                if name=='queue_changed' and payload.get('stage')==dashboard.stage:
                    yield emit('queue_changed',cursor)
                elif name in {'delta','reset'}:
                    yield emit('graph_changed',cursor)
            if time.monotonic()-ping>5:
                yield ': keepalive\n\n'
                ping = time.monotonic()
            await asyncio.sleep(.15)
