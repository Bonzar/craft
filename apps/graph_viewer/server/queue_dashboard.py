"""Read-only status through the existing owner-authenticated Graph web service."""
from __future__ import annotations
import asyncio
from contextlib import closing
import os
from pathlib import Path
import secrets
import re
import pwd
import sqlite3
import stat
import time

from prototypes.assertion_memory.queue_status import build_queue_status
from prototypes.assertion_memory import dashboard_projection

SCOPES = {
    'test': ('assertion-test-deploy', Path('/srv/assertion-release/test/state/fictional.sqlite3')),
    'prod': ('assertion-production-deploy', Path('/var/lib/assertion-memory/memory.sqlite3')),
}
CURSOR = re.compile(r'(test|prod):[0-9a-f]{16}:[0-9]{1,15}\Z')


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
        account, canonical = SCOPES[stage]
        if Path(config) != dashboard_projection.projection_path(stage) or Path(source) != canonical:
            raise ValueError('Expected canonical lane projection and database')
        self.stage, self.db = stage, canonical
        self.publisher_uid = pwd.getpwnam(account).pw_uid
        self.epoch = secrets.token_hex(8)
        self.streams = asyncio.Semaphore(8)

    def metadata(self):
        return dashboard_projection.read(stage=self.stage,database=self.db,uid=self.publisher_uid)

    def snapshot(self, *, limit, offset):
        fields = self.metadata()
        with closing(_connect(self.db)) as conn:
            conn.execute('BEGIN')
            deadline=time.monotonic()+2
            conn.set_progress_handler(lambda:time.monotonic()>=deadline, 2000)
            try:
                status_keys=('stage','release_version','processing_session_ids',
                    'auto_process_fresh_mac','model_configured','interactive_enabled')
                result=build_queue_status(conn,limit=limit,offset=offset,
                                          **{k:fields[k] for k in status_keys})
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
