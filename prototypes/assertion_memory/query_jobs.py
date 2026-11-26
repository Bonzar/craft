"""Durable interactive jobs. A single worker owns this lane, independent of capture."""
import asyncio
from contextlib import asynccontextmanager, closing
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from uuid import uuid4

from . import genai_traces, query_timing as timing

ID = re.compile(r'[a-zA-Z0-9_-]{1,100}\Z')


class QueryJobs:
    def __init__(self, db, run, ready, *, wait_seconds=5.0):
        self.db, self.run, self.ready = db, run, ready
        self.wait_seconds = float(wait_seconds)
        self.signal, self.stop = threading.Event(), threading.Event()
        self.thread = None
        self.lock_path = Path(db).with_name(Path(db).name + '.interactive.lock')

    def _connect(self):
        return sqlite3.connect(self.db, isolation_level=None, timeout=0.5)

    def _init(self):
        with closing(self._connect()) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS memory_interactive_queries (
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE, arguments TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('queued','running','completed','failed')),
                result TEXT, created REAL NOT NULL)""")
            c.execute("""CREATE INDEX IF NOT EXISTS memory_interactive_queries_waiting
                         ON memory_interactive_queries(state,created)""")
            c.execute("""CREATE INDEX IF NOT EXISTS memory_interactive_queries_active_request
                         ON memory_interactive_queries(arguments) WHERE state IN ('queued','running')""")

    @asynccontextmanager
    async def lifespan(self):
        # Uvicorn uses one worker here. Also protect against a second local
        # server trying to recover or claim the same database's query jobs.
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._init()
            with closing(self._connect()) as c:
                # After an abrupt process loss it may be impossible to know
                # whether the final write happened. Never repeat it silently.
                failure = json.dumps({'answer_accepted': False, 'answer_error': {
                    'code': 'worker_interrupted', 'message': 'Interactive worker restarted before result was recorded.'}})
                c.execute("UPDATE memory_interactive_queries SET state='failed',result=? WHERE state='running'",
                          (failure,))
            self.stop.clear()
            # Keep this lease with the worker itself during an in-flight shutdown.
            worker_fd = os.dup(fd)
            try:
                self.thread = threading.Thread(target=self._own, args=(worker_fd,),
                                               name='memory-interactive-queries', daemon=True)
                self.thread.start()
            except BaseException:
                os.close(worker_fd)
                raise
            try:
                yield
            finally:
                self.stop.set()
                self.signal.set()
                await asyncio.to_thread(self.thread.join, 1)
        finally:
            os.close(fd)

    def _own(self, fd):
        try:
            self._loop()
        finally:
            os.close(fd)

    def _loop(self):
        while not self.stop.is_set():
            try:
                from .database_clear import database_lease
                with database_lease(self.db):
                    claim = self._claim()
                    if claim is not None:
                        key, arguments, created = claim
                        with timing.bind(trace_id=key):
                            queue_wait_ms = max(0, round((time.time() - created)*1000))
                            timing.emit('queue_wait', 'ok', queue_wait_ms)
                            self._execute(key, arguments, queue_wait_ms=queue_wait_ms)
                if claim is None:
                    self.signal.wait(0.25)
                    self.signal.clear()
            except (sqlite3.OperationalError,BlockingIOError):
                self.stop.wait(0.15)

    def _execute(self, key, arguments, *, queue_wait_ms):
        otel = timing.begin_otel('job_total', queue_wait_ms=queue_wait_ms)
        started = time.monotonic()
        state = 'failed'
        timing.emit('job_total', 'start')
        try:
            try:
                result = self.run(**json.loads(arguments))
                if not isinstance(result, dict):
                    raise ValueError('unexpected query response')
                serialized = self._wire(result)
                state = 'completed'
            except Exception:
                serialized = self._wire({'answer_accepted': False, 'answer_error': {
                    'code': 'query_failed', 'message': 'The interactive query could not finish.'}})
                state = 'failed'
            # Retry the receipt write only, never rerun model work.
            persisted = time.monotonic()
            while True:
                try:
                    with closing(self._connect()) as c:
                        c.execute("UPDATE memory_interactive_queries SET state=?,result=? WHERE id=? AND state='running'",
                                  (state, serialized, key))
                    timing.emit('result_persist', 'ok', timing.elapsed(persisted))
                    break
                except sqlite3.OperationalError:
                    if self.stop.wait(0.15):
                        timing.emit('result_persist', 'interrupted', timing.elapsed(persisted))
                        state = 'interrupted'
                        return
        finally:
            outcome = 'ok' if state == 'completed' else state
            genai_traces.end(otel, outcome)
            timing.emit('job_total', outcome, timing.elapsed(started))

    @staticmethod
    def _wire(obj):
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

    def _claim(self):
        with closing(self._connect()) as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute("SELECT id,arguments,created FROM memory_interactive_queries WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
            if row:
                c.execute("UPDATE memory_interactive_queries SET state='running' WHERE id=?", (row[0],))
            c.commit()
            return row

    def _submission(self, arguments, request_key, ready=None, *, insert, proposed_id=None):
        with closing(self._connect()) as c:
            c.execute('BEGIN IMMEDIATE' if insert else 'BEGIN')
            row = c.execute('SELECT id,arguments FROM memory_interactive_queries WHERE request_key=?',
                            (request_key,)).fetchone() if request_key is not None else None
            if row and row[1] != arguments:
                raise ValueError('request ID already refers to a different question')
            if not row and request_key is None:
                row = c.execute("""SELECT id,arguments FROM memory_interactive_queries
                                   WHERE arguments=? AND state IN ('queued','running') LIMIT 1""",
                                (arguments,)).fetchone()
            if row:
                return row[0]
            if not insert:
                return None
            key = proposed_id or uuid4().hex
            if ready is None:
                c.execute("INSERT INTO memory_interactive_queries VALUES(?,?,?,'queued',NULL,?)",
                          (key, request_key, arguments, time.time()))
            else:
                c.execute("INSERT INTO memory_interactive_queries VALUES(?,?,?,'completed',?,?)",
                          (key, request_key, arguments, self._wire(ready), time.time()))
            c.commit()
            return key

    def _read(self, key):
        with closing(self._connect()) as c:
            row = c.execute('SELECT state,result FROM memory_interactive_queries WHERE id=?',(key,)).fetchone()
        if row is None:
            return {'query_job_id': key, 'query_state': 'not_found', 'answer_accepted': False}
        state, result = row
        if result is None:
            return {'query_job_id': key, 'query_state': state, 'answer_accepted': False, 'query_poll_after_ms': 1000}
        return {**json.loads(result), 'query_job_id': key, 'query_state': state}

    async def __call__(self, *, query_job_id=None, query_request_id=None, **arguments):
        if self.thread is None or not self.thread.is_alive() or self.stop.is_set():
            return {'query_state':'unavailable','answer_accepted':False}
        for key in (query_job_id,query_request_id):
            if key is not None and (not isinstance(key,str) or not ID.fullmatch(key)):
                raise ValueError('Invalid query job or request ID')
        if query_job_id is not None:
            if query_request_id is not None or any(v is not None for v in arguments.values()):
                raise ValueError('Use only query_job_id to check an existing job')
            return await asyncio.to_thread(self._read,query_job_id)
        topic = arguments.get('query')
        if not isinstance(topic,str) or not topic.strip() or len(topic)>8000:
            raise ValueError('query must contain between 1 and 8000 characters')
        started = time.monotonic()
        deadline = asyncio.get_running_loop().time() + self.wait_seconds
        wire = self._wire(arguments)
        key = await asyncio.to_thread(self._submission,wire,query_request_id,insert=False)
        if key is None:
            proposed_id = uuid4().hex
            probe_started = time.monotonic()
            try:
                # This path never calls a model. It is bounded and may fall
                # through to the worker if selection is unavailable/expensive.
                direct = await asyncio.wait_for(asyncio.to_thread(self.ready,**arguments),
                                            min(1.0,max(0.01,deadline-asyncio.get_running_loop().time())))
            except (TimeoutError, sqlite3.OperationalError) as failure:
                direct = None
                probe_status = 'timeout' if isinstance(failure, TimeoutError) else 'unavailable'
            else:
                probe_status = 'ready' if direct is not None else 'miss'
            probe_ms = timing.elapsed(probe_started)
            if direct is not None and query_request_id is None:
                with timing.bind(trace_id=proposed_id):
                    timing.emit('ready_probe', probe_status, probe_ms)
                    timing.emit('submission', 'ready', timing.elapsed(started))
                return {**direct,'query_state':'completed'}
            key = await asyncio.to_thread(self._submission,wire,query_request_id,direct,insert=True,proposed_id=proposed_id)
            with timing.bind(trace_id=key):
                timing.emit('ready_probe', probe_status, probe_ms)
                timing.emit('submission', ('reused' if key != proposed_id else 'ready'
                            if direct is not None else 'queued'), timing.elapsed(started))
            self.signal.set()
        else:
            with timing.bind(trace_id=key):
                timing.emit('submission', 'reused', timing.elapsed(started))
        while True:
            result = await asyncio.to_thread(self._read,key)
            remaining = deadline-asyncio.get_running_loop().time()
            if result['query_state'] not in ('queued','running') or remaining<=0:
                return result
            await asyncio.sleep(min(0.15,remaining))
