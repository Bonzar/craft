"""Canonical worker lanes, retaining the existing supervisor's exclusive pause lock."""
from contextlib import contextmanager
from datetime import datetime, timezone

from .processing_pool import ActiveClaims, ProcessingPool
from .storage import MemoryStore
from .queue import CaptureQueue
from .thought_processing_selection import source_predicate, maintenance_predicate, processing_deadline
from .thought_service import traced_engine
from .thought_trace import ThoughtTrace
from .thought_worker import ThoughtWorker


class TracedThoughtWorker(ThoughtWorker):
    @contextmanager
    def _attempt(self, operation, inputs):
        original = self.engine
        trace = ThoughtTrace(original.store, operation, inputs)
        self.engine = traced_engine(original.thoughts, original.model, original.candidates, trace)
        try:
            yield trace
        except BaseException:
            trace.finish('failed')
            raise
        finally:
            self.engine = original

    def _source(self, claim):
        with self._attempt('source', {'event_id': claim.job.event_id, 'job_id': claim.job.id,
                           'attempt':claim.job.attempts,'previous_error':claim.job.error}) as trace:
            result = super()._source(claim)
            trace.finish('completed' if result.status == 'completed' else 'retry_required')
            return result

    def _consolidate(self, task):
        with self._attempt(task.get('action','consolidate'), {'thought_id': task['thought_id'],
                           'version': task['version'], 'generation': task.get('generation'),
                           'attempt':task.get('attempts'),'previous_error':task.get('error')}) as trace:
            result = super()._consolidate(task)
            trace.finish('completed' if result['status'] == 'completed' else 'retry_required')
            return result


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


def run_pool(config, lock, idle_handoff, *, limit=0):
    """Called only AFTER the MCP supervisor acquired db+'.worker.lock'."""
    if config.processing_session_ids == ():
        return
    claims = ActiveClaims()
    with MemoryStore(config.db) as store:
        queue=CaptureQueue(store)
        _recover_selected(queue,config.processing_session_ids)

        @contextmanager
        def factory(lane, observer):
            with MemoryStore(config.db) as local:
                current = config.service(local)
                yield TracedThoughtWorker(current.queue, current.engine, lane=lane,
                                          lock_owned=True, claim_observer=observer,
                                          processing_session_ids=config.processing_session_ids)

        def heartbeat():
            if claims.snapshot():
                with store.transaction(write=True) as db:
                    claims.renew(db)

        completed = 0
        while True:
            remaining = limit-completed if limit else 0
            pool = ProcessingPool(factory, source_workers=config.source_workers, limit=remaining,
                idle_seconds=config.worker_idle_seconds, claims=claims, heartbeat=heartbeat)
            completed += pool.run()['completed']
            if limit and completed >= limit:
                return
            if idle_handoff(lock, lambda: _deadline(store,config)):
                return
