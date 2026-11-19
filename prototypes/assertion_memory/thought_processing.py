"""Canonical worker lanes, retaining the existing supervisor's exclusive pause lock."""
from contextlib import contextmanager
from datetime import datetime, timezone
import signal
import threading

from .model import ProcessingCancelled, check_processing_cancelled, processing_cancellation
from .processing_pool import ActiveClaims, ProcessingPool
from .storage import MemoryStore
from .queue import CaptureQueue
from .thought_processing_selection import source_predicate, maintenance_predicate, processing_deadline
from .thought_service import traced_engine
from .thought_trace import ThoughtTrace
from .thought_worker import ThoughtWorker


class TracedThoughtWorker(ThoughtWorker):
    def __init__(self, *args, source_batch=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_batch = source_batch
        self.batch_contexts = {}

    def _context(self, event):
        if event.id in self.batch_contexts:
            return self.batch_contexts[event.id]
        return super()._context(event)

    @contextmanager
    def _attempt(self, operation, inputs, cancel_claim):
        original = self.engine
        trace = ThoughtTrace(original.store, operation, inputs)
        self.engine = traced_engine(original.thoughts, original.model, original.candidates, trace)
        if operation == 'source' and self.source_batch is not None:
            self.engine.gateway = self._batch_initial_construction(trace, self.engine.gateway, inputs)
        try:
            yield trace
        except ProcessingCancelled:
            # Releasing the lease and finalizing its trace are one durable outcome.
            with original.store.transaction(write=True) as db:
                cancel_claim(db)
                trace.finish('interrupted')
        except BaseException:
            trace.finish('failed')
            raise
        finally:
            self.engine = original

    def _batch_initial_construction(self, trace, fallback, inputs):
        is_initial = True

        def gateway(task, request, bindings):
            nonlocal is_initial
            if task == 'Construct thought' and is_initial:
                is_initial = False
                source_records = [row for row in request.records if row.get('role') == 'source']
                if len(source_records) == 1 and not request.repair_instructions:
                    draft = self.source_batch.submit(request, bindings, self._generate_source_batch)
                    if draft is not None:
                        trace.record('source_batch_draft', inputs=bindings, details={
                            'batch_run_id': draft.run_id, 'source': draft.source,
                            'event_id': inputs['event_id'],
                        })
                        return draft.body
            return fallback(task, request, bindings)

        return gateway

    def _generate_source_batch(self, request, bindings):
        source_ids = list(self.batch_contexts) or list(dict.fromkeys(
            row['event_id'] for row in bindings if 'event_id' in row))
        batch_trace = ThoughtTrace(self.queue.store, 'source_model_batch',
                                   {'source_event_ids': source_ids})
        try:
            result = batch_trace.call(self.model, request, bindings)
        except ProcessingCancelled:
            batch_trace.finish('interrupted')
            raise
        except BaseException:
            batch_trace.finish('failed')
            raise
        batch_trace.finish()
        return result, batch_trace.id

    def process_source_group(self, event_ids, limit):
        from .source_model_batch import (pending_sources, PreparedSourceGroup, generate_source_drafts,
                                         MAX_EXPLICIT_BATCH_CHARACTERS)
        if len(self.processing_session_ids or ()) != 1 or not 1 <= len(event_ids) <= limit:
            raise ValueError('one source session and a sufficient queue attempt limit are required')
        events = pending_sources(self.queue, self.processing_session_ids[0], event_ids)
        is_pausing = self.is_pausing or (lambda: False)
        self.batch_contexts = {event.id: self._context(event) for event in events}
        try:
            requests = [self.engine.source_construction_request(event, self.batch_contexts[event.id])
                        for event in events]
            if is_pausing():
                return
            drafts = generate_source_drafts(requests, self._generate_source_batch,
                                           max_characters=MAX_EXPLICIT_BATCH_CHARACTERS, exact=True)
            if any(draft is None for draft in drafts):
                raise ValueError('one group response did not cover every source; original jobs remain queued')
            self.source_batch = PreparedSourceGroup(requests, drafts)
            for event in events:
                if is_pausing():
                    return
                claim = self._claim_source(event_id=event.id)
                if claim is None:
                    raise ValueError(f'queued source became unavailable: {event.id}')
                self._source(claim)
        finally:
            self.source_batch = None
            self.batch_contexts = {}
        for _ in range(max(0, limit - len(events))):
            task = self.work.claim(is_pausing=self.is_pausing)
            if task is None:
                break
            self._consolidate(task)

    def _source(self, claim):
        with self._attempt('source', {'event_id': claim.job.event_id, 'job_id': claim.job.id,
                           'attempt':claim.job.attempts,'previous_error':claim.job.error},
                           lambda db: self._cancel_source(db, claim)) as trace:
            check_processing_cancelled()
            result = super()._source(claim)
            trace.finish('completed' if result.status == 'completed' else 'retry_required')
            return result

    def _consolidate(self, task):
        with self._attempt(task.get('action','consolidate'), {'thought_id': task['thought_id'],
                           'version': task['version'], 'generation': task.get('generation'),
                           'attempt':task.get('attempts'),'previous_error':task.get('error')},
                           lambda db: self._cancel_thought(db, task)) as trace:
            check_processing_cancelled()
            result = super()._consolidate(task)
            trace.finish('completed' if result['status'] == 'completed' else 'retry_required')
            return result


    def _cancel_source(self, db, claim):
        now = self.queue._time()
        db.execute("UPDATE memory_capture_jobs SET status='pending',attempts=max(0,attempts-1),"
                   "lease_token=NULL,lease_until=NULL,available_at=?,updated_at=? "
                   "WHERE id=? AND status='running' AND lease_token=?",
                   (now, now, claim.job.id, claim.token))

    def _cancel_thought(self, db, task):
        db.execute("UPDATE memory_thought_work SET status='pending',attempts=max(0,attempts-1),"
                   "lease_token=NULL,lease_until=NULL,available_at=? "
                   "WHERE thought_id=? AND status='running' AND lease_token=?",
                   (self.queue._time(), task['thought_id'], task['lease_token']))


def _recover_selected(queue, session_ids):
    """The caller owns the same exclusive worker lock as ordinary processing."""
    now=queue._time()
    with queue.store.transaction(write=True) as db:
        predicate,arguments=source_predicate(session_ids,'e')
        db.execute("UPDATE memory_capture_jobs SET status='pending',lease_token=NULL,lease_until=NULL,"
            "available_at=?,updated_at=? WHERE status='running' AND event_id IN "
            f'(SELECT e.id FROM memory_raw_events e WHERE {predicate})',(now,now,*arguments))
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_thought_work'").fetchone()
        if session_ids is not None and exists:
            predicate,arguments=maintenance_predicate(db,session_ids,'w')
            db.execute("UPDATE memory_thought_work SET status='pending',lease_token=NULL,lease_until=NULL,"
                "available_at=? WHERE status='running' AND thought_id IN "
                f'(SELECT w.thought_id FROM memory_thought_work w WHERE {predicate})',(now,*arguments))


def _deadline(store, config):
    with store.transaction() as db:
        return processing_deadline(db,config.processing_session_ids,
            now=datetime.now(timezone.utc).isoformat(timespec='microseconds'),
            with_model=config.provider!='none')


@contextmanager
def _worker_signals():
    """SIGINT/SIGTERM drain; SIGUSR1 also cancels outstanding model calls."""
    pausing = False
    cancelling = False

    def request_pause(signum, frame):
        nonlocal pausing, cancelling
        pausing = True
        if signum == signal.SIGUSR1:
            cancelling = True

    previous = {}
    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1):
                previous[signum] = signal.signal(signum, request_pause)
        yield (lambda: pausing), (lambda: cancelling)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def run_pool(config, lock, idle_handoff, *, limit=0):
    """Called only after the MCP supervisor acquired db+'.worker.lock'."""
    with _worker_signals() as (is_pausing, is_cancelling):
        return _run_pool(config, lock, idle_handoff, limit, is_pausing, is_cancelling)


def _run_pool(config, lock, idle_handoff, limit, is_pausing, is_cancelling):
    if config.processing_session_ids == ():
        return
    claims = ActiveClaims()
    from .source_model_batch import SourceModelBatch
    source_batch = (SourceModelBatch(config.source_workers)
                    if config.batch_source_construction and config.source_workers > 1 else None)
    with MemoryStore(config.db) as store:
        queue=CaptureQueue(store)
        if config.source_event_ids is not None:
            if not is_pausing():
                with processing_cancellation(is_cancelling):
                    current = config.service(store)
                    worker = TracedThoughtWorker(current.queue, current.engine, lock_owned=True,
                                                 processing_session_ids=config.processing_session_ids,
                                                 is_pausing=is_pausing)
                    worker.process_source_group(config.source_event_ids, limit)
            return
        _recover_selected(queue,config.processing_session_ids)

        @contextmanager
        def factory(lane, observer):
            with MemoryStore(config.db) as local, processing_cancellation(is_cancelling):
                current = config.service(local)
                yield TracedThoughtWorker(current.queue, current.engine, lane=lane,
                                          lock_owned=True, claim_observer=observer,
                                          processing_session_ids=config.processing_session_ids,
                                          is_pausing=is_pausing, source_batch=source_batch)

        def heartbeat():
            if claims.snapshot():
                with store.transaction(write=True) as db:
                    claims.renew(db)

        completed = 0
        while not is_pausing():
            remaining = limit-completed if limit else 0
            pool = ProcessingPool(factory, source_workers=config.source_workers, limit=remaining,
                idle_seconds=config.worker_idle_seconds, claims=claims, heartbeat=heartbeat, is_pausing=is_pausing)
            completed += pool.run()['completed']
            if is_pausing() or (limit and completed >= limit):
                return
            if idle_handoff(lock, lambda: _deadline(store,config)):
                return
