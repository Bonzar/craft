"""Canonical worker lanes, retaining the existing supervisor's exclusive pause lock."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import signal
import threading

from .processing_control import paused as processing_paused
from .model import ProcessingCancelled, check_processing_cancelled, processing_cancellation
from .processing_pool import ActiveClaims, ProcessingPool
from .storage import MemoryStore, background_write_retry
from .queue import CaptureQueue
from .thought_processing_selection import source_predicate, maintenance_predicate, processing_deadline
from .thought_service import traced_engine
from .thought_trace import ThoughtTrace
from .thought_worker import ThoughtWorker

GROUP_HEARTBEAT_SECONDS = 30


@contextmanager
def _keep_group_claims(queue, active_claims):
    stop = threading.Event()
    interval = min(GROUP_HEARTBEAT_SECONDS, queue._lease_seconds / 3)

    def renew_until_stopped():
        if stop.wait(interval):
            return
        with MemoryStore(queue.store.path) as heartbeat_store, background_write_retry():
            while not stop.is_set():
                if active_claims.snapshot():
                    with heartbeat_store.transaction(write=True) as db:
                        active_claims.renew(db, lease_seconds=queue._lease_seconds)
                stop.wait(interval)

    with ThreadPoolExecutor(max_workers=1) as executor:
        renewal = executor.submit(renew_until_stopped)
        try:
            yield renewal
        finally:
            stop.set()


class TracedThoughtWorker(ThoughtWorker):
    def __init__(self, *args, source_batch=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_batch = source_batch
        self.batch_contexts = {}
        self.capture_batch_id = None

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
            if task=='Construct thought' and self.capture_batch_id is not None:
                raise ValueError('batch already consumed its sole source generation')
            return fallback(task, request, bindings)

        return gateway

    def _generate_source_batch(self, request, bindings):
        source_ids = list(self.batch_contexts) or list(dict.fromkeys(
            row['event_id'] for row in bindings if 'event_id' in row))
        batch_trace = ThoughtTrace(self.queue.store, 'source_model_batch',
                                   {'source_event_ids': source_ids,
                                    **({'capture_batch_id':self.capture_batch_id} if self.capture_batch_id else {})})
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

    def process_source_group(self, event_ids, limit, *, capture_batch_id=None):
        from .thought_worker_queue import track_group_followups
        if (len(self.processing_session_ids or ()) != 1 or not 1 <= len(event_ids) <= limit
                or len(set(event_ids)) != len(event_ids)):
            raise ValueError('one source session and a sufficient queue attempt limit are required')
        active_claims = ActiveClaims()
        previous_observer = self.claim_observer

        def observe(event, kind, identity, token):
            active_claims.observe(event, kind, identity, token)
            if previous_observer is not None:
                previous_observer(event, kind, identity, token)

        self.claim_observer = observe
        if capture_batch_id is not None and self.claim_bound_source_extractor_factory is not None:
            raise ValueError('configured claim-bound extractor has no whole-batch construction capability')
        self.capture_batch_id=capture_batch_id
        try:
            with (background_write_retry(), track_group_followups(self) as work,
                  _keep_group_claims(self.queue, active_claims) as renewal):
                if self._run_source_group(event_ids, renewal, active_claims):
                    self._process_group_followups(work, limit - len(event_ids), renewal)
        except ProcessingCancelled:
            return
        finally:
            self.capture_batch_id=None
            self.claim_observer = previous_observer

    def _run_source_group(self, event_ids, renewal, active_claims):
        claims = self._claim_source_group(event_ids)
        for claim in claims:
            active_claims.observe('claimed', 'source', claim.job.id, claim.token)
        error = None
        try:
            if claims:
                self._process_claimed_group(claims, renewal, active_claims)
        except Exception as failure:
            from .model import ModelProviderUnavailable
            if isinstance(failure, ModelProviderUnavailable):
                from .queue import LeaseLost
                for claim in claims:
                    try:
                        self.queue._fail(claim, failure)
                    except LeaseLost:
                        pass
                return False
            error = f'{type(failure).__name__}: {failure}'[:1000]
            raise
        finally:
            with self.queue.store.transaction(write=True) as db:
                for claim in claims:
                    self._cancel_source(db, claim, error=error)
        return bool(claims)

    def _claim_source_group(self, event_ids):
        from .source_model_batch import pending_sources
        with self.queue.store.transaction(write=True) as c:
            if processing_paused(c) or (self.is_pausing and self.is_pausing()):
                return []
            events = pending_sources(self.queue, self.processing_session_ids[0], event_ids,
                                     capture_batch_id=self.capture_batch_id)
            claims = [self._claim_source(event_id=event.id,capture_batch_id=self.capture_batch_id) for event in events]
            if any(claim is None for claim in claims):
                if self.is_pausing and self.is_pausing():
                    # Roll back any partial admission before returning to the supervisor.
                    raise ProcessingCancelled()
                raise ValueError('a queued source became unavailable during group admission')
            return claims

    def _process_claimed_group(self, claims, renewal, active_claims):
        from .source_model_batch import (PreparedSourceGroup, generate_source_drafts,
                                         MAX_EXPLICIT_BATCH_CHARACTERS)
        events = [self.queue.get_event(claim.job.event_id) for claim in claims]
        try:
            check_processing_cancelled()
            # Claim-bound extractors own their paginated context and source windows;
            # do not run the legacy bounded batch constructor before them.
            if self.claim_bound_source_extractor_factory is None:
                self.batch_contexts = {event.id: self._context(event) for event in events}
                requests = [self.engine.source_construction_request(event, self.batch_contexts[event.id])
                            for event in events]
                drafts = generate_source_drafts(requests, self._generate_source_batch,
                                               max_characters=MAX_EXPLICIT_BATCH_CHARACTERS, exact=True)
                if any(draft is None for draft in drafts):
                    raise ValueError('one group response did not cover every source; original jobs remain queued')
                self.source_batch = PreparedSourceGroup(requests, drafts)
            for claim in claims:
                check_processing_cancelled()
                if renewal.done():
                    renewal.result()
                self._source(claim)
                active_claims.observe('released', 'source', claim.job.id, claim.token)
        finally:
            self.source_batch = None
            self.batch_contexts = {}
    def _process_group_followups(self, work, limit, renewal):
        for _ in range(limit):
            check_processing_cancelled()
            if renewal.done():
                renewal.result()
            task = work.claim_next(self.is_pausing)
            if task is None:
                break
            self._consolidate(task)

    def _source(self, claim):
        with self._attempt('source', {'event_id': claim.job.event_id, 'job_id': claim.job.id,
                           'attempt':claim.job.attempts,'previous_error':(
                               'Prior claim-bound source error details redacted'
                               if self.claim_bound_source_extractor_factory is not None and claim.job.error
                               else claim.job.error)},
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


    def _cancel_source(self, db, claim, *, error=None):
        now = self.queue._time()
        db.execute("UPDATE memory_capture_jobs AS j SET status='pending',attempts=max(0,attempts-1),"
                   "lease_token=NULL,lease_until=NULL,available_at=?,updated_at=?,error=coalesce(?,error) "
                   "WHERE id=? AND status='running' AND lease_token=?",
                   (now, now, error, claim.job.id, claim.token))

    def _cancel_thought(self, db, task):
        db.execute("UPDATE memory_thought_work SET status='pending',attempts=max(0,attempts-1),"
                   "lease_token=NULL,lease_until=NULL,available_at=? "
                   "WHERE thought_id=? AND status='running' AND lease_token=?",
                   (self.queue._time(), task['thought_id'], task['lease_token']))


def _recover_selected(queue, session_ids):
    """The caller owns the same exclusive worker lock as ordinary processing."""
    now=queue._time()
    with queue.store.transaction(write=True) as db:
        from .thought_processing_selection import source_claim_predicate
        predicate,arguments=source_claim_predicate(db,session_ids)
        db.execute("UPDATE memory_capture_jobs SET status='pending',lease_token=NULL,lease_until=NULL,"
            "available_at=?,updated_at=? WHERE status='running' AND event_id IN "
            f"(SELECT e.id FROM memory_raw_events e WHERE json_extract(e.metadata,'$.capture_batch_id') IS NULL AND {predicate})",
            (now,now,*arguments))
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_thought_work'").fetchone()
        if session_ids is not None and exists:
            predicate,arguments=maintenance_predicate(db,session_ids,'w')
            db.execute("UPDATE memory_thought_work SET status='pending',lease_token=NULL,lease_until=NULL,"
                "available_at=? WHERE status='running' AND thought_id IN "
                f'(SELECT w.thought_id FROM memory_thought_work w WHERE {predicate})',(now,*arguments))


def _deadline(store, config, *, include_batches=True):
    with store.transaction() as db:
        from .mcp_server import _processing_selector
        return processing_deadline(db,_processing_selector(config,db),
            now=datetime.now(timezone.utc).isoformat(timespec='microseconds'),
            with_model=config.provider!='none',include_batches=include_batches)


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
        from .queue_session_control import worker_selection_changed
        return _run_pool(config, lock, idle_handoff, limit,
                         lambda: is_pausing() or worker_selection_changed(config), is_cancelling)


def _run_pool(config, lock, idle_handoff, limit, is_pausing, is_cancelling):
    from .mcp_server import _processing_selector
    claims = ActiveClaims()
    from .source_model_batch import SourceModelBatch
    source_batch = (SourceModelBatch(config.source_workers)
                    if config.batch_source_construction and config.source_workers > 1 else None)
    with MemoryStore(config.db) as store:
        with store.transaction() as db:
            selector = _processing_selector(config,db)
        if selector == () and limit:
            return
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
        if selector != ():
            from .fresh_processing import ensure_selector
            with store.transaction() as db:
                recover = not config.auto_process_fresh_mac or ensure_selector(db)
            if recover:
                _recover_selected(queue,selector)
        if not limit:
            from .capture_batch import fence_interrupted
            with store.transaction(write=True) as db:
                fence_interrupted(db)

        @contextmanager
        def factory(lane, observer):
            with MemoryStore(config.db) as local, processing_cancellation(is_cancelling):
                current = config.service(local)
                yield TracedThoughtWorker(current.queue, current.engine, lane=lane,
                                          lock_owned=True, claim_observer=observer,
                                          processing_session_ids=selector,
                                          is_pausing=is_pausing, source_batch=source_batch)

        def heartbeat():
            if claims.snapshot():
                with store.transaction(write=True) as db:
                    claims.renew(db)

        completed = 0
        while not is_pausing():
            with store.transaction() as db:
                selector=_processing_selector(config,db)
            if not limit:
                from .capture_batch import run_next
                with processing_cancellation(is_cancelling):
                    if run_next(config,store,is_pausing):
                        continue
            if selector == ():
                if idle_handoff(lock, lambda: _deadline(store,config)):
                    return
                continue
            remaining = limit-completed if limit else 0
            pool = ProcessingPool(factory, source_workers=config.source_workers, limit=remaining,
                idle_seconds=min(config.worker_idle_seconds,1.0) if config.auto_process_fresh_mac else config.worker_idle_seconds,
                claims=claims, heartbeat=heartbeat, is_pausing=is_pausing)
            completed += pool.run()['completed']
            if is_pausing() or (limit and completed >= limit):
                return
            if idle_handoff(lock, lambda: _deadline(store,config,include_batches=not limit)):
                return

