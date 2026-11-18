"""Only the exclusive server worker can revoke an interrupted source lease."""
import fcntl
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from prototypes.assertion_memory import mcp_server
from prototypes.assertion_memory.queue import CaptureQueue, LeaseLost
from prototypes.assertion_memory.storage import MemoryStore


class WorkerStartupRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = str(Path(self.directory.name) / 'toy.sqlite3')
        self.store = MemoryStore(self.db)
        self.addCleanup(self.store.close)
        self.queue = CaptureQueue(self.store, lease_seconds=600)
        self.raw = self.queue.capture_event('toy', 'one', 'Synthetic source.', source='test://one')
        self.claim = self.queue._claim(with_model=True)
        self.config = mcp_server.ServiceConfig(self.db, provider='none')

    def row(self, queue=None):
        with (queue or self.queue).store.transaction() as connection:
            return dict(connection.execute('SELECT * FROM memory_capture_jobs WHERE id=?', (self.raw.id,)).fetchone())

    def run_at_loop_boundary(self, callback):
        def worker(queue, engine, **options):
            return SimpleNamespace(queue=queue,
                derived=SimpleNamespace(recover_abandoned=lambda: None),
                names=SimpleNamespace(recover_abandoned=lambda: None),
                run_once=lambda: callback(queue) or object())
        with patch.object(mcp_server, 'LinkWorker', side_effect=worker):
            mcp_server.run_worker(self.config, limit=1)

    def test_another_exclusive_owner_prevents_any_lease_reset(self):
        before = self.row()
        with open(self.db + '.worker.lock', 'a') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(mcp_server, 'LinkWorker') as worker:
                mcp_server.run_worker(self.config, limit=1)
                worker.assert_not_called()
        self.assertEqual(self.row(), before)
        with self.store.transaction() as connection:
            self.queue._assert_owned(connection, self.claim)

    def test_exclusive_startup_requeues_now_and_revokes_old_token_without_losing_results(self):
        for identity in ('synthetic-result', 'synthetic-context'):
            self.store.append_assertion(assertion_id=identity, text='Retained synthetic assertion.',
                actor='user', source='test://one', episode='toy', recorded_at=self.raw.recorded_at)
        self.store.append_edge(edge_id='synthetic-edge', from_id='synthetic-result',
            to_id='synthetic-context', kind='supports', recorded_at=self.raw.recorded_at)
        with self.store.transaction(write=True) as connection:
            connection.execute('UPDATE memory_capture_jobs SET candidates=?,proposals=?,refreshed_views=?,extracted_ids=? WHERE id=?',
                (json.dumps(['synthetic-candidate']), json.dumps([{'kind': 'fixture', 'status': 'unresolved'}]),
                 json.dumps({'fixture': 'version'}), json.dumps(['synthetic-result']), self.raw.id))
            retained = {table: [tuple(row) for row in connection.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                        for table in ('memory_assertions', 'memory_edges', 'memory_raw_events')}
        before = self.row()
        observed = {}
        def inspect(queue):
            observed.update(self.row(queue))
            with queue.store.transaction() as connection:
                with self.assertRaises(LeaseLost):
                    queue._assert_owned(connection, self.claim)
            with self.assertRaises(LeaseLost):
                queue._finish_extraction(self.claim, self.raw, {'assertions': [], 'reviews': []})
        self.run_at_loop_boundary(inspect)
        self.assertEqual(observed['status'], 'pending')
        self.assertIsNone(observed['lease_token'])
        self.assertIsNone(observed['lease_until'])
        self.assertLessEqual(observed['available_at'], self.queue._time())
        self.assertIn('interrupted', observed['error'].lower())
        for field in ('phase', 'attempts', 'candidates', 'proposals', 'refreshed_views', 'extracted_ids'):
            self.assertEqual(observed[field], before[field], field)
        self.assertEqual(self.queue.get_event(self.raw.id), self.raw)
        with self.store.transaction() as connection:
            self.assertEqual({table: [tuple(row) for row in connection.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                              for table in retained}, retained)

    def test_startup_leaves_every_nonrunning_job_unchanged(self):
        with self.store.transaction(write=True) as connection:
            connection.execute("UPDATE memory_capture_jobs SET status='failed',error='Existing failure' WHERE id=?", (self.raw.id,))
        for index, state in enumerate(('pending', 'completed', 'waiting_model', 'waiting_origin')):
            raw = self.queue.capture_event('toy', str(index + 2), 'Another synthetic source.', source='test://other')
            with self.store.transaction(write=True) as connection:
                connection.execute('UPDATE memory_capture_jobs SET status=? WHERE id=?', (state, raw.id))
        with self.store.transaction() as connection:
            before = [tuple(row) for row in connection.execute('SELECT * FROM memory_capture_jobs ORDER BY id')]
            raw_before = [tuple(row) for row in connection.execute('SELECT * FROM memory_raw_events ORDER BY id')]
        self.run_at_loop_boundary(lambda queue: None)
        with self.store.transaction() as connection:
            self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM memory_capture_jobs ORDER BY id')], before)
            self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM memory_raw_events ORDER BY id')], raw_before)


if __name__ == '__main__':
    unittest.main()
