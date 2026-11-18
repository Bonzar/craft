"""Scope derivation survives bursts, worker crashes and later source arrivals."""
import unittest
from datetime import datetime, timedelta, timezone

from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.worker import LinkWorker
from prototypes.assertion_memory.derived_work import DerivedWorkQueue


class DerivedWorkTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.engine = MemoryEngine(self.store)
        # INSERT triggers use SQLite wall time; start the fake scheduler on the
        # same UTC clock so rearmed work remains eligible after the date changes.
        self.now = datetime.now(timezone.utc)
        self.work = DerivedWorkQueue(self.store, clock=lambda: self.now, lease_seconds=10)

    def add(self, key, scope='garden', **kwargs):
        return self.store.append_assertion(assertion_id=key, text='Fictional '+key,
            actor='Mira', source='test://'+key, episode='s', scope=scope,
            recorded_at=self.now.isoformat(), **kwargs)

    def test_burst_coalesces_and_commits_only_frozen_frontier(self):
        for index in range(20):
            self.add(str(index))
        self.assertEqual(len(self.work.status()), 1)
        claim = self.work.claim(self.engine, with_model=True)
        self.assertEqual(len(claim.snapshot.assertions), 20)
        frozen = claim.frontier.copy()
        self.add('later')
        self.work.finish(claim, {'root_view_id':'root','version_id':1})
        row = self.work.status()[0]
        self.assertEqual(row['completed_frontier'], frozen)
        self.assertEqual(row['state'], 'pending')
        self.assertGreater(row['desired_frontier']['generation'], frozen['generation'])

    def test_expired_lease_recovers_frozen_snapshot_and_checkpoint(self):
        self.add('first')
        claim = self.work.claim(self.engine, with_model=True)
        self.work.save_checkpoint(claim, {**claim.checkpoint, 'overview':{'stage':'leaf'}})
        self.add('later')
        self.now += timedelta(seconds=11)
        recovered = self.work.claim(self.engine, with_model=True)
        self.assertEqual(recovered.snapshot.assertions.keys(), {'first'})
        self.assertEqual(recovered.checkpoint['overview']['stage'], 'leaf')
        self.assertEqual(recovered.frontier, claim.frontier)
        with self.assertRaises(RuntimeError):
            self.work.finish(claim, {})

    def test_edge_atomically_dirties_both_scopes_and_rollback_is_clean(self):
        self.add('one', 'north'); self.add('two', 'south')
        for _ in range(2):
            claim = self.work.claim(self.engine, with_model=True)
            self.work.finish(claim, {})
        with self.assertRaises(ValueError):
            with self.store.transaction(write=True):
                self.add('rolled-back')
                raise ValueError('rollback')
        self.assertEqual({r['scope'] for r in self.work.status()}, {'north','south'})
        self.store.append_edge(edge_id='link',from_id='one',to_id='two',kind='supports',
                               recorded_at=self.now.isoformat())
        self.assertTrue(all(r['state']=='pending' for r in self.work.status()))

    def test_waiting_model_is_honest_and_later_model_resumes(self):
        self.add('first')
        self.assertIsNone(self.work.claim(self.engine, with_model=False))
        self.assertEqual(self.work.status()[0]['state'], 'waiting_model')
        claim = self.work.claim(self.engine, with_model=True)
        self.assertIsNotNone(claim)
        self.work.finish(claim, {'root_view_id':'root'})
        self.assertEqual(self.work.status()[0]['state'], 'idle')

    def test_temporal_change_requeues_but_pipeline_alone_does_not(self):
        self.add('future', valid_at=(self.now+timedelta(seconds=20)).isoformat())
        claim = self.work.claim(self.engine, with_model=True)
        self.assertEqual(claim.snapshot.assertions, {})
        self.work.finish(claim, {})
        self.now += timedelta(seconds=21)
        future = self.work.claim(self.engine, with_model=True)
        self.assertIn('future', future.snapshot.assertions)
        self.work.finish(future, {})
        self.engine.pipeline_key = 'new-pipeline'
        self.assertIsNone(self.work.claim(self.engine, with_model=True))
        self.assertEqual(self.work.legacy_coverage(self.engine)['legacy_scope_count'],1)

    def test_worker_marks_derivation_waiting_without_failing_source_job(self):
        self.add('first')
        worker = LinkWorker(CaptureQueue(self.store), self.engine, source_first=True)
        self.assertIsNone(worker.run_once())
        self.assertEqual(self.work.status()[0]['state'], 'waiting_model')

    def test_worker_deadline_includes_scheduled_temporal_scope_work(self):
        self.add('future', valid_at=(self.now+timedelta(seconds=20)).isoformat())
        claim = self.work.claim(self.engine, with_model=True)
        self.work.finish(claim, {})
        worker = LinkWorker(CaptureQueue(self.store), self.engine, source_first=True)
        worker.derived = self.work
        self.assertEqual(worker.seconds_until_work(with_model=True), 20)

    def test_round_robin_and_exclusive_supervisor_recovery_preserve_checkpoint(self):
        self.add('one', 'north'); self.add('two', 'south')
        first = self.work.claim(self.engine, with_model=True)
        self.work.save_checkpoint(first, {**first.checkpoint, 'overview':{'leaf':1}})
        self.work.recover_abandoned()
        second = self.work.claim(self.engine, with_model=True)
        self.assertNotEqual(first.scope, second.scope)
        self.work.finish(second, {})
        recovered = self.work.claim(self.engine, with_model=True)
        self.assertEqual(recovered.scope, first.scope)
        self.assertEqual(recovered.checkpoint['overview'], {'leaf':1})
        with self.assertRaises(RuntimeError):
            self.work.save_checkpoint(first, first.checkpoint)

    def test_temporal_boundary_on_cross_scope_endpoint_wakes_related_scope(self):
        self.add('local', 'north')
        self.add('prerequisite', 'south', valid_at=(self.now+timedelta(seconds=20)).isoformat())
        self.store.append_edge(edge_id='dependency',from_id='local',to_id='prerequisite',
                               kind='depends_on',recorded_at=self.now.isoformat())
        for _ in range(2):
            claim = self.work.claim(self.engine, with_model=True)
            self.work.finish(claim, {})
        north = self.work.status('north')[0]
        self.assertEqual(north['next_wake_at'], (self.now+timedelta(seconds=20)).isoformat(timespec='microseconds'))

    def test_repeated_provider_errors_are_bounded_and_new_dirty_work_rearms(self):
        self.add('first')
        for _ in range(3):
            claim = self.work.claim(self.engine, with_model=True)
            self.assertIsNotNone(claim)
            self.work.release(claim, error=RuntimeError('fictional outage'))
            self.now += timedelta(seconds=10)
        self.assertIsNone(self.work.claim(self.engine, with_model=True))
        self.assertIsNone(self.work.seconds_until_work(with_model=True))
        self.assertEqual(self.work.status()[0]['state'], 'failed')
        self.add('later')
        self.assertIsNotNone(self.work.claim(self.engine, with_model=True))


if __name__ == '__main__':
    unittest.main()
