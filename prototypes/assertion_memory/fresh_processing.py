"""Durable automatic-processing admission for the configured fresh-only Mac."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
import logging
from contextvars import ContextVar
import re

_AUTH = ContextVar('fresh_processing_machine', default=False)
_UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
_LOG = logging.getLogger(__name__)


@contextmanager
def authenticated_fresh_mac():
    """Only the private host may assert this, after checking its pinned client key."""
    token = _AUTH.set(True)
    try:
        yield
    finally:
        _AUTH.reset(token)


def is_authenticated_fresh_mac():
    return _AUTH.get()


def _schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS memory_fresh_mac_streams ('
               'session_id TEXT PRIMARY KEY, stream_id TEXT UNIQUE NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS memory_fresh_mac_events ('
               'event_id TEXT PRIMARY KEY REFERENCES memory_raw_events(id) ON DELETE CASCADE)')


def before_register(db, producer_id, session_id, replace_epoch):
    if not _AUTH.get():
        return False
    # Native local adapter uses UUID session ids and one generated producer id.
    if (not _UUID.fullmatch(session_id) or not producer_id.startswith('jarvis:')
            or not _UUID.fullmatch(producer_id[7:]) or replace_epoch is not None):
        return False
    if db.execute("SELECT 1 FROM memory_raw_events WHERE session_id=? "
                  "OR json_extract(metadata,'$.native_session_id')=? LIMIT 1",
                  (session_id,session_id)).fetchone():
        return False
    from .ingest_streams import schema
    schema(db)
    return db.execute('SELECT 1 FROM memory_ingest_streams WHERE session_id=? LIMIT 1',
                      (session_id,)).fetchone() is None


def admit_stream(db, registered):
    _schema(db)
    db.execute('INSERT INTO memory_fresh_mac_streams VALUES (?,?)',
               (registered['session_id'], registered['stream_id']))


def admit_event(db, event_id, session_id, delivered, *, created):
    if not _AUTH.get() or not created or delivered is None:
        return
    _schema(db)
    db.execute('INSERT INTO memory_fresh_mac_events SELECT ? WHERE EXISTS ('
               'SELECT 1 FROM memory_fresh_mac_streams WHERE session_id=? AND stream_id=?)',
               (event_id,session_id,delivered['stream_id']))


def ensure_selector(db):
    """Can be used in read-only probes before the first qualifying registration."""
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                      "AND name='memory_fresh_mac_events'").fetchone() is not None


@asynccontextmanager
async def reconcile_processing(launch, *, interval=15):
    """Retry a wake lost to an explicitly selected worker or a paused lock."""
    stop = asyncio.Event()

    async def loop():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                try:
                    await asyncio.to_thread(launch)
                except Exception:
                    # Operational signal only; do not include arbitrary exception text.
                    _LOG.warning('processing_reconcile_failed')

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        stop.set()
        await task


# Keep the existing legacy helper available without imposing its policy on batches.
reconcile_fresh_mac = reconcile_processing
