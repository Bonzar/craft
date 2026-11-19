"""Run a single queued dialogue message and its attributable maintenance work."""
from datetime import datetime
import json
from time import sleep

from .session_api import _id
from .thought_processing_selection import source_predicate
from .thought_source_policy import eligible_sql
from .thought_worker_queue import ThoughtWorkQueue


SOURCE_OPEN = ('pending', 'waiting_model', 'running')
WAIT_SLICE_SECONDS = 1


class MessageRun:
    """Checkpoint the selected source and its queue generations in their own transaction."""

    def __init__(self, queue, session_id):
        self.queue, self.store, self.session_id = queue, queue.store, session_id
        with self.store.transaction(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS memory_one_message_run ('
                       'session_id TEXT PRIMARY KEY,event_id TEXT NOT NULL,progress TEXT NOT NULL)')
            self.event_id = self._select(db)

    def _select(self, db):
        previous = db.execute('SELECT event_id FROM memory_one_message_run WHERE session_id=?',
                              (self.session_id,)).fetchone()
        if previous:
            return previous[0]
        session, session_args = source_predicate((self.session_id,))
        policy, policy_args = eligible_sql()
        row = db.execute('SELECT e.id FROM memory_raw_events e JOIN memory_capture_jobs j ON j.event_id=e.id '
                         'WHERE j.status IN (?,?,?) AND '
                         "coalesce(json_extract(e.metadata,'$.role'),e.actor) IN ('user','assistant') "
                         f'AND {session} AND {policy} '
                         "ORDER BY coalesce(json_extract(e.metadata,'$.source_message_at'),e.recorded_at),e.rowid LIMIT 1",
                         (*SOURCE_OPEN, *session_args, *policy_args)).fetchone()
        if row:
            db.execute('INSERT INTO memory_one_message_run VALUES (?,?,?)',
                       (self.session_id, row[0], json.dumps({'jobs': {}, 'run_ids': []})))
        return row[0] if row else None

    def load(self, db):
        row = db.execute('SELECT progress FROM memory_one_message_run WHERE session_id=?',
                         (self.session_id,)).fetchone()
        return json.loads(row[0])

    def save(self, db, progress):
        db.execute('UPDATE memory_one_message_run SET progress=? WHERE session_id=?',
                   (json.dumps(progress), self.session_id))

    def trace(self, run_id):
        with self.store.transaction(write=True) as db:
            progress = self.load(db)
            progress['run_ids'].append(run_id)
            self.save(db, progress)

    def close(self):
        with self.store.transaction(write=True) as db:
            db.execute('DELETE FROM memory_one_message_run WHERE session_id=?', (self.session_id,))


class MessageWork(ThoughtWorkQueue):
    def __init__(self, worker, run):
        super().__init__(worker.queue, processing_session_ids=(run.session_id,))
        self.run = run

    def request(self, thought, action, cause):
        with self.store.transaction(write=True) as db:
            before = self.get(thought.id)
            accepted = super().request(thought, action, cause)
            current = self.get(thought.id)
            if not accepted or current is None:
                return accepted
            if before and before['desired_generation'] == current['desired_generation']:
                return accepted
            progress = self.run.load(db)
            previous = progress['jobs'].get(thought.id)
            has_older_work = before and before['completed_generation'] < before['desired_generation']
            owns_older_work = (before and previous and previous['exclusive']
                               and previous['generation'] == before['desired_generation'])
            progress['jobs'][thought.id] = {'generation': current['desired_generation'],
                                            'exclusive': not has_older_work or bool(owns_older_work)}
            self.run.save(db, progress)
            return accepted

    def rows(self, db):
        for thought_id, target in self.run.load(db)['jobs'].items():
            row = self.get(thought_id)
            item = {'thought_id': thought_id, 'generation': target['generation']}
            if row is None:
                yield {**item, 'status': 'blocked', 'error': 'Queued thought job disappeared'}
                continue
            yield {**item, **_job_state(row, target)}

    def claim_next(self, pausing):
        with self.store.transaction(write=True) as db:
            for row in self.rows(db):
                if row['status'] in ('pending','running'):
                    claim = self.claim(thought_id=row['thought_id'], is_pausing=pausing)
                    if claim is not None:
                        return claim
        return None


def _job_state(row, target):
    fields = ('action','completed_version','status','error','available_at','lease_until')
    result = {key: row[key] for key in fields}
    if row['completed_generation'] >= target['generation']:
        return {**result, 'status': 'completed', 'error': None}
    if not target['exclusive'] or row['desired_generation'] != target['generation']:
        return {**result, 'status': 'blocked', 'error': 'Merged with another queued cause'}
    return result


def _wait(queue, until):
    remaining = (datetime.fromisoformat(until) - datetime.fromisoformat(queue._time())).total_seconds()
    if remaining <= 0:
        return False
    sleep(min(remaining, WAIT_SLICE_SECONDS))
    return True


def _run_source(worker, event_id, pausing):
    job = worker.queue.get(event_id)
    while job.status in SOURCE_OPEN and not pausing():
        claim = worker._claim_source(event_id=event_id)
        if claim is None:
            latest = worker.queue.get(event_id)
            if latest.status not in SOURCE_OPEN:
                return latest
            if latest.status == 'pending' and _wait(worker.queue, latest.available_at):
                job = latest
                continue
            # A killed process can leave an unexpired lease. Keep this source pinned.
            return latest
        job = worker._source(claim)
        if job.status == 'waiting_model':
            return job
    return job


def _run_followups(worker, pausing):
    queue = worker.work
    while not pausing():
        task = queue.claim_next(pausing)
        if task is not None:
            worker._consolidate(task)
            continue
        with queue.store.transaction() as db:
            remaining = list(queue.rows(db))
        delayed = [job['available_at'] for job in remaining if job['status'] == 'pending']
        if not delayed or not _wait(worker.queue, min(delayed)):
            break
    with queue.store.transaction() as db:
        return list(queue.rows(db))


def process_one(worker, session_id, *, collect=None):
    """The caller may pass a traced worker and collect a compact report before releasing the lock."""
    _id(session_id, 'session_id')
    if worker.processing_session_ids != (session_id,):
        raise ValueError('the worker must be restricted to the same session')
    if worker.engine.model is None and worker.engine.gateway is None:
        raise ValueError('one-message processing requires a configured model')
    with worker._lock() as owned:
        if not owned:
            raise RuntimeError('memory worker is already locked')
        run = MessageRun(worker.queue, session_id)
        if run.event_id is None:
            return {'status': 'empty', 'session_id': session_id}
        return _process_locked(worker, run, collect)


def _process_locked(worker, run, collect):
    original_queue = worker.work
    worker.work = MessageWork(worker, run)
    worker.message_run = run
    pausing = worker.is_pausing or (lambda: False)
    try:
        source = _run_source(worker, run.event_id, pausing)
        jobs = _run_followups(worker, pausing) if source.status in ('completed','failed') else []
        with run.store.transaction() as db:
            run_ids = run.load(db)['run_ids']
        in_progress = source.status in SOURCE_OPEN or any(j['status'] in ('pending','running') for j in jobs)
        status = 'interrupted' if pausing() or in_progress else 'completed'
        if status != 'interrupted' and (source.status == 'failed' or any(j['status'] != 'completed' for j in jobs)):
            status = 'blocked'
        result = {'status': status, 'session_id': run.session_id, 'event_id': run.event_id,
                  'source_status': source.status, 'source_error': source.error,
                  'source_thought_ids': source.extracted_ids, 'jobs': jobs, 'run_ids': run_ids}
        if collect:
            result.update(collect(result))
        if status != 'interrupted':
            run.close()
        return result
    finally:
        worker.work = original_queue
        del worker.message_run
