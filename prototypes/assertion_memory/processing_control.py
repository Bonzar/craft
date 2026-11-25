"""One durable pause for background claims, independent of intake and session policy."""
from __future__ import annotations
import asyncio
from contextlib import closing, asynccontextmanager
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

REVISION = re.compile(r'[0-9a-f]{32}\Z')
INITIAL = '0' * 32


class Conflict(ValueError):
    pass


def read_control(db):
    table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_processing_control'").fetchone()
    if table is None:
        return False, INITIAL
    row = db.execute('SELECT paused,revision FROM memory_processing_control WHERE singleton=1').fetchone()
    if row is None or row[0] not in (0,1) or type(row[1]) is not str or not REVISION.fullmatch(row[1]):
        raise ValueError('Bad processing control state')
    return bool(row[0]), row[1]


def paused(db):
    return read_control(db)[0]


def set_control(db, *, paused, expected_revision):
    if not db.in_transaction:
        raise ValueError('Pause control requires a serialized write transaction')
    if type(paused) is not bool or type(expected_revision) is not str or not REVISION.fullmatch(expected_revision):
        raise ValueError('Invalid processing control change')
    old_paused, old_revision = read_control(db)
    if old_revision != expected_revision:
        raise Conflict('Processing control changed')
    if paused == old_paused:
        return old_paused, old_revision
    db.execute('CREATE TABLE IF NOT EXISTS memory_processing_control ('
               'singleton INTEGER PRIMARY KEY CHECK(singleton=1), '
               'paused INTEGER NOT NULL CHECK(paused IN (0,1)), '
               'revision TEXT NOT NULL)')
    result = uuid4().hex
    db.execute('INSERT INTO memory_processing_control VALUES (1,?,?) '
               'ON CONFLICT(singleton) DO UPDATE SET paused=excluded.paused,revision=excluded.revision',
               (int(paused),result))
    return paused, result


@asynccontextmanager
async def restart_on_resume(path, launch):
    """The actual Base scheduler wakes on a resume even without a new ingest."""
    def current():
        with closing(sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True,
                                     isolation_level=None,timeout=.1)) as db:
            return read_control(db)

    async def watch():
        last = None
        while True:
            try:
                actual = await asyncio.to_thread(current)
                if (last is None and actual!=(False,INITIAL) or last is not None and actual!=last) and not actual[0]:
                    await asyncio.to_thread(launch)
                last = actual
            except (OSError, sqlite3.Error, ValueError):
                # An unavailable control row does not launch new workers.
                pass
            await asyncio.sleep(.5)

    worker = asyncio.create_task(watch())
    try:
        yield
    finally:
        worker.cancel()
        await asyncio.gather(worker,return_exceptions=True)
