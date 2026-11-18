"""Foreground admission waits for another writer without replaying semantic work."""
from pathlib import Path
import json
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.publication_fences import bind_guard, GuardLeaseLost
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore


class QuerySQLiteBusyTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = MemoryStore(str(Path(self.directory.name) / 'memory.sqlite3'))
        self.addCleanup(self.store.close)
        self.calls = []
        self.writers = []
        self.engine = MemoryEngine(self.store, model=self.provider)
        self.service = MemoryService(self.store, self.engine)
        self.store._connection.execute('PRAGMA busy_timeout=5')
        self.addCleanup(self.stop_writers)

    def stop_writers(self):
        for release, thread in self.writers:
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def hold_other_writer(self):
        locked, release = threading.Event(), threading.Event()
        def writer():
            with sqlite3.connect(self.store.path, isolation_level=None) as db:
                db.execute('BEGIN IMMEDIATE')
                locked.set()
                release.wait(.12)
                db.rollback()
        thread = threading.Thread(target=writer)
        self.writers.append((release, thread))
        thread.start()
        self.assertTrue(locked.wait(2), 'model call must not hold the SQLite writer lock')

    def provider(self, request):
        self.assertFalse(self.store._connection.in_transaction)
        self.calls.append(request.task)
        self.hold_other_writer()
        if request.task == 'Plan memory retrieval for a question':
            return json.dumps({'reuse_view_id': None, 'related_view_ids': [], 'subquestions': [],
                               'reason': 'Single coherent synthetic question.'})
        return 'A permit is required only during the pilot.'

    def test_route_publication_waits_without_calling_planner_again(self):
        result = self.service.query('What conditions were reported?', scope='s')
        self.assertEqual(self.calls, ['Plan memory retrieval for a question'])
        version = self.engine.get_version(result['view']['id'])
        self.assertEqual(version.overview_review, None)
        self.assertEqual(len(self.engine.question_history('What conditions were reported?', scopes=('s',))), 1)
        self.assertEqual(self.store._connection.execute('PRAGMA busy_timeout').fetchone()[0], 5)

    def test_answer_publication_waits_without_calling_writer_again(self):
        self.store.append_assertion(assertion_id='permit', text='A permit is required only during the pilot.',
            actor='fixture', source='fixture://permit', episode='pilot', scope='s', recorded_at='2026-01-01')
        self.engine.define_view(ViewSpec('permit-view', 'Pilot conditions', 'What pilot conditions apply?',
            scopes=('s',), root_ids=('permit',)))
        result = self.service.query('What pilot conditions apply?', scope='s')
        self.assertEqual(self.calls, ['What pilot conditions apply?'])
        version = self.engine.get_version(result['view']['id'])
        self.assertEqual([row['id'] for row in version.evidence], ['permit'])
        self.assertEqual(len(self.engine.view_history('permit-view')), 1)
        self.assertEqual(self.engine.get_version(version.id), version)

    def test_ownership_fence_is_checked_after_busy_wait(self):
        with bind_guard(self.engine, lambda db: False), self.assertRaises(GuardLeaseLost):
            self.service.query('What conditions were reported?', scope='s')
        self.assertEqual(self.calls, ['Plan memory retrieval for a question'])
        self.assertEqual(self.engine.list_views(), [])

    def test_nonbusy_sql_body_error_propagates_without_retry(self):
        entered = []
        def fail(*args):
            with self.store.transaction(write=True) as db:
                entered.append(True)
                db.execute('SELECT * FROM deliberately_missing_foreground_table')
        with patch.object(self.service, '_query', side_effect=fail), self.assertRaises(sqlite3.OperationalError) as caught:
            self.service.query('Synthetic nonbusy failure?', scope='s')
        self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_ERROR)
        self.assertEqual(entered, [True])
        self.assertEqual(self.calls, [])
        self.assertFalse(self.store._connection.in_transaction)


if __name__ == '__main__':
    unittest.main()
