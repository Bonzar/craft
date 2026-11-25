"""Coalesced canonical change work; CaptureQueue continues owning raw jobs."""
from __future__ import annotations
from contextlib import contextmanager
import json
from uuid import uuid4
from .processing_control import paused as processing_paused
from .queue import LeaseLost
from .thought_processing_selection import normalize_session_ids,current_cohort,maintenance_predicate,pending_cohort


class ThoughtWorkQueue:
    def __init__(self, queue, processing_session_ids=None):
        self.queue, self.store = queue, queue.store
        self.processing_session_ids = normalize_session_ids(processing_session_ids)
        with self.store.transaction(write=True) as c:
            c.execute('''CREATE TABLE IF NOT EXISTS memory_thought_work (
                thought_id TEXT PRIMARY KEY, desired_version INTEGER NOT NULL,
                completed_version INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, available_at TEXT NOT NULL,
                lease_token TEXT, lease_until TEXT, error TEXT)''')
            columns = {row[1] for row in c.execute('PRAGMA table_info(memory_thought_work)')}
            additions = {'action': "TEXT NOT NULL DEFAULT 'consolidate'",
                'desired_generation': 'INTEGER NOT NULL DEFAULT 0',
                'completed_generation': 'INTEGER NOT NULL DEFAULT 0',
                'claimed_generation': 'INTEGER NOT NULL DEFAULT 0'}
            for name, declaration in additions.items():
                if name not in columns:
                    c.execute('ALTER TABLE memory_thought_work ADD COLUMN ' + name + ' ' + declaration)
            if 'desired_generation' not in columns:
                c.execute('UPDATE memory_thought_work SET desired_generation=1 WHERE desired_version>completed_version')
            c.execute('CREATE TABLE IF NOT EXISTS memory_thought_work_causes '
                '(thought_id TEXT NOT NULL,cause TEXT NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(thought_id,cause))')
            cause_columns = {row[1] for row in c.execute('PRAGMA table_info(memory_thought_work_causes)')}
            if 'generation' not in cause_columns:
                c.execute('ALTER TABLE memory_thought_work_causes ADD COLUMN generation INTEGER')
                # Old receipts have no ordering evidence: place them in one current group.
                c.execute('UPDATE memory_thought_work_causes SET generation=coalesce((SELECT desired_generation '
                    'FROM memory_thought_work w WHERE w.thought_id=memory_thought_work_causes.thought_id),0)')
            c.execute('CREATE INDEX IF NOT EXISTS memory_thought_work_cause_generation '
                'ON memory_thought_work_causes(thought_id,generation)')
            c.execute('CREATE TABLE IF NOT EXISTS memory_thought_cause_sessions '
                '(thought_id TEXT NOT NULL,cause TEXT NOT NULL,session_id TEXT NOT NULL,PRIMARY KEY(thought_id,cause,session_id),'
                'FOREIGN KEY(thought_id,cause) REFERENCES memory_thought_work_causes(thought_id,cause) ON DELETE CASCADE)')
            c.execute('CREATE INDEX IF NOT EXISTS memory_thought_cause_session_lookup '
                'ON memory_thought_cause_sessions(session_id,thought_id,cause)')
            c.execute('CREATE TABLE IF NOT EXISTS memory_thought_generations '
                '(request_key TEXT PRIMARY KEY,response TEXT NOT NULL)')

    def enqueue(self, changed):
        for thought in changed:
            self.request(thought, 'consolidate', ('published', thought.id, thought.version))

    def enqueue_refresh(self, thought, cause):
        return self.request(thought, 'refresh', ('basis', *cause))

    def request(self, thought, action, cause):
        if action not in ('refresh', 'consolidate'):
            raise ValueError('thought action must be refresh or consolidate')
        with self.store.transaction(write=True) as c:
            existing = c.execute('SELECT * FROM memory_thought_work WHERE thought_id=?', (thought.id,)).fetchone()
            generation = existing['desired_generation'] + 1 if existing else 1
            inserted = c.execute('INSERT OR IGNORE INTO memory_thought_work_causes(thought_id,cause,generation) VALUES(?,?,?)',
                (thought.id, json.dumps(cause, sort_keys=True), generation)).rowcount
            sessions = current_cohort() or ()
            if not inserted:
                prior = c.execute('SELECT generation FROM memory_thought_work_causes WHERE thought_id=? AND cause=?',
                    (thought.id,json.dumps(cause,sort_keys=True))).fetchone()
                if existing is None or prior[0] <= existing['completed_generation']:
                    return False
            tagged = 0
            for session_id in sessions:
                tagged += c.execute('INSERT OR IGNORE INTO memory_thought_cause_sessions VALUES(?,?,?)',
                    (thought.id,json.dumps(cause,sort_keys=True),session_id)).rowcount
            if not inserted:
                return bool(tagged)
            now = self.queue._time()
            if existing is None:
                c.execute('INSERT INTO memory_thought_work(thought_id,desired_version,status,available_at,action,desired_generation) '
                    "VALUES(?,?,'pending',?,?,1)", (thought.id, thought.version, now, action))
                return True
            pending_refresh = (existing['status'] in ('pending', 'running')
                and existing['desired_generation'] > existing['claimed_generation'] and existing['action'] == 'refresh')
            action = 'refresh' if pending_refresh else action
            c.execute('UPDATE memory_thought_work SET desired_version=max(desired_version,?),'
                "desired_generation=?,status=CASE WHEN status='running' THEN status ELSE 'pending' END,"
                'action=?,attempts=0,error=NULL,available_at=min(available_at,?) WHERE thought_id=?',
                (thought.version, generation, action, now, thought.id))
            return True

    def claim(self, *, is_pausing=None, thought_id=None):
        with self.store.transaction(write=True) as c:
            # SQLite write admission may have waited past the pause request.
            if processing_paused(c) or (is_pausing is not None and is_pausing()):
                return None
            now = self.queue._time()
            eligible,args = maintenance_predicate(c,self.processing_session_ids,'w')
            if thought_id is not None:
                eligible += ' AND w.thought_id=?'
                args = [*args, thought_id]
            c.execute("UPDATE memory_thought_work AS w SET status='failed',lease_token=NULL,lease_until=NULL,"
                "error='Thought lease expired too many times' WHERE w.status='running' AND w.lease_until<=? AND w.attempts>=? AND "+eligible,
                (now,self.queue._max_attempts,*args))
            row = c.execute("SELECT w.* FROM memory_thought_work w WHERE ((w.status='pending' AND w.available_at<=?) "
                "OR (w.status='running' AND w.lease_until<=?)) AND "+eligible+" ORDER BY w.available_at,w.thought_id LIMIT 1",(now,now,*args)).fetchone()
            if row is None:
                return None
            task = dict(row)
            task['lease_token'] = uuid4().hex
            task['version'] = task['desired_version']
            task['generation'] = task['desired_generation']
            task['processing_session_ids'] = pending_cohort(c,task,self.processing_session_ids)
            task['attempts'] += 1
            c.execute("UPDATE memory_thought_work SET status='running',lease_token=?,lease_until=?,attempts=?,claimed_generation=? WHERE thought_id=?",
                (task['lease_token'], self.queue._time(self.queue._lease_seconds), task['attempts'], task['generation'], task['thought_id']))
            return task

    def causes(self, task):
        """Only unfinished causes belonging to this immutable claim generation."""
        with self.store.transaction() as c:
            self.owned(c, task)
            rows = c.execute('SELECT cause FROM memory_thought_work_causes WHERE thought_id=? '
                'AND generation>? AND generation<=? ORDER BY generation,cause',
                (task['thought_id'], task['completed_generation'], task['generation']))
            return [json.loads(row[0]) for row in rows]

    def owned(self, c, task):
        if c.execute("SELECT 1 FROM memory_thought_work WHERE thought_id=? AND status='running' "
            'AND lease_token=? AND lease_until>?', (task['thought_id'], task['lease_token'], self.queue._time())).fetchone() is None:
            raise LeaseLost('thought change lease expired')

    def renew(self, task):
        with self.store.transaction(write=True) as c:
            self.owned(c, task)
            c.execute('UPDATE memory_thought_work SET lease_until=? WHERE thought_id=?',
                (self.queue._time(self.queue._lease_seconds), task['thought_id']))

    def finish(self, task, processed_version):
        with self.store.transaction(write=True) as c:
            self.owned(c, task)
            c.execute("UPDATE memory_thought_work SET completed_version=max(completed_version,?),"
                "completed_generation=max(completed_generation,?),status=CASE WHEN desired_generation>? THEN 'pending' ELSE 'completed' END,attempts=0,"
                'lease_token=NULL,lease_until=NULL,error=NULL WHERE thought_id=?',
                (processed_version, task['generation'], task['generation'], task['thought_id']))
            return self.get(task['thought_id'])

    def fail(self, task, error):
        with self.store.transaction(write=True) as c:
            self.owned(c, task)
            latest = self.get(task['thought_id'])
            newer = latest['desired_generation'] > task['generation']
            status = 'pending' if newer or task['attempts'] < self.queue._max_attempts else 'failed'
            c.execute('UPDATE memory_thought_work SET status=?,error=?,lease_token=NULL,lease_until=NULL,'
                'attempts=?,available_at=? WHERE thought_id=?',
                (status, f'{type(error).__name__}: {error}'[:1000], 0 if newer else task['attempts'],
                 self.queue._time(0 if newer else 2 ** min(task['attempts'], 8)), task['thought_id']))
            return self.get(task['thought_id'])

    def get(self, thought_id):
        with self.store.transaction() as c:
            row = c.execute('SELECT * FROM memory_thought_work WHERE thought_id=?', (thought_id,)).fetchone()
            return dict(row, version=row['desired_version']) if row else None

    def deadline(self):
        with self.store.transaction() as c:
            eligible,args = maintenance_predicate(c,self.processing_session_ids,'w')
            return c.execute("SELECT min(CASE WHEN w.status='running' THEN w.lease_until ELSE w.available_at END) "
                "FROM memory_thought_work w WHERE w.status IN ('pending','running') AND "+eligible,args).fetchone()[0]

    def cached(self, key):
        with self.store.transaction() as c:
            row = c.execute('SELECT response FROM memory_thought_generations WHERE request_key=?', (key,)).fetchone()
            return row[0] if row else None

    def save(self, task, key, response):
        with self.store.transaction(write=True) as c:
            self.owned(c, task)
            c.execute('INSERT INTO memory_thought_generations VALUES(?,?) ON CONFLICT(request_key) DO NOTHING', (key, response))


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


@contextmanager
def track_group_followups(worker):
    """Keep queue generations attributable to this invocation on its own connection."""
    original = worker.work
    with worker.queue.store.transaction(write=True) as db:
        db.execute('CREATE TEMP TABLE memory_group_progress (progress TEXT NOT NULL)')
        db.execute('INSERT INTO memory_group_progress VALUES (?)', ('{"jobs":{}}',))
    try:
        worker.work = MessageWork(worker, GroupProgress(worker.processing_session_ids[0]))
        yield worker.work
    finally:
        worker.work = original
        with worker.queue.store.transaction(write=True) as db:
            db.execute('DROP TABLE temp.memory_group_progress')


class GroupProgress:
    def __init__(self, session_id):
        self.session_id = session_id

    def load(self, db):
        return json.loads(db.execute('SELECT progress FROM temp.memory_group_progress').fetchone()[0])

    def save(self, db, progress):
        db.execute('UPDATE temp.memory_group_progress SET progress=?', (json.dumps(progress),))


def _job_state(row, target):
    fields = ('action','completed_version','status','error','available_at','lease_until')
    result = {key: row[key] for key in fields}
    if row['completed_generation'] >= target['generation']:
        return {**result, 'status': 'completed', 'error': None}
    if not target['exclusive'] or row['desired_generation'] != target['generation']:
        return {**result, 'status': 'blocked', 'error': 'Merged with another queued cause'}
    return result
