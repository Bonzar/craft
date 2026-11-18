"""Durable source-first phases and cooperative bounded refresh generation."""
import unittest
from datetime import datetime, timedelta, timezone

from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import ModelRequest
from prototypes.assertion_memory.queue import CaptureQueue, LeaseLost
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker


class QueuePriorityTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.queue = CaptureQueue(self.store, clock=lambda: self.now, lease_seconds=10, max_attempts=2)
        self.engine = MemoryEngine(self.store)

    def assertion(self, key):
        return self.queue.capture(CaptureInput('Record ' + key, 'user', 'test://' + key, 's', assertion_id=key))

    def raw(self, key):
        return self.queue.capture_event('s', key, 'Raw ' + key, source='test://' + key)

    def worker(self, model=None, **kwargs):
        return LinkWorker(self.queue, self.engine, model, source_first=True, **kwargs)

    def test_link_phase_atomically_yields_refresh_and_raw_extract_wins(self):
        self.assertion('old')
        generated=[]
        self.engine.refresh_from=lambda key: generated.append(key) or []
        worker=self.worker(lambda request:'{"source_coverage":{"status":"unresolved","reason":"Semantic source inventory remains unresolved.","omissions":[]}}'
            if request.task=='Review source material coverage' else '{"assertions":[],"links":[]}')
        first=worker.run_once()
        self.assertEqual((first.status,first.phase,first.attempts),('pending','refresh',0))
        self.assertEqual(generated,[])
        self.now+=timedelta(seconds=1)
        event=self.raw('new')
        next_job=worker.run_once()
        self.assertEqual(next_job.event_id,event.id)
        self.assertEqual(next_job.status,'completed')
        self.assertEqual(generated,[])

    def test_all_ready_link_work_precedes_heavy_refresh(self):
        self.assertion('one');self.assertion('two');self.assertion('three')
        generated=[]
        self.engine.refresh_from=lambda key: generated.append(key) or []
        worker=self.worker(lambda request:'{"links":[]}')
        jobs=[worker.run_once() for _ in range(3)]
        self.assertEqual({job.assertion_id for job in jobs},{'one','two','three'})
        self.assertTrue(all(job.phase=='refresh' and job.status=='pending' for job in jobs))
        self.assertEqual(generated,[])
        self.assertEqual(worker.run_once().status,'completed')
        self.assertEqual(len(generated),1)

    def test_refresh_yields_between_model_calls_and_reuses_durable_exact_batches(self):
        calls=[]
        def provider(request):
            calls.append(request.task)
            return '{"assertions":[]}' if request.task.startswith('Extract') else 'summary '+request.task
        self.engine=MemoryEngine(self.store,model=provider)
        added=[]
        def refresh(key):
            self.engine.model(ModelRequest('batch-one',[]))
            if not added:
                added.append(self.raw('arriving'))
            self.engine.model(ModelRequest('batch-two',[]))
            return []
        self.engine.refresh_from=refresh
        self.assertion('old')
        worker=self.worker(provider)
        worker.run_once() # link -> durable pending refresh
        yielded=worker.run_once()
        self.assertEqual((yielded.status,yielded.phase,yielded.attempts),('pending','refresh',0))
        self.assertEqual(calls,['batch-one'])
        self.assertEqual(worker.run_once().event_id,added[0].id)
        # A different worker resumes from persisted generation cache, not a process-local cache.
        done=self.worker(provider).run_once()
        self.assertEqual(done.status,'completed')
        self.assertEqual(calls.count('batch-one'),1)
        self.assertEqual(calls.count('batch-two'),1)
        self.assertIs(self.engine.model,provider)

    def test_stage_retries_do_not_repeat_committed_link_work(self):
        self.assertion('one')
        worker=self.worker()
        links=[]
        original=worker._link
        def link(claim):
            links.append(claim.job.id);return original(claim)
        worker._link=link
        worker.run_once()
        self.engine.refresh_from=lambda key: (_ for _ in ()).throw(RuntimeError('refresh unavailable'))
        first=worker.run_once()
        self.assertEqual((first.phase,first.status,first.attempts),('refresh','pending',1))
        self.now+=timedelta(seconds=2)
        second=worker.run_once()
        self.assertEqual((second.phase,second.status,second.attempts),('refresh','failed',2))
        self.assertEqual(links,['one'])

    def test_expired_refresh_claim_resumes_refresh_without_regenerating_links(self):
        self.assertion('one')
        worker=self.worker()
        worker.run_once()
        abandoned=self.queue._claim(with_model=False,source_first=True)
        self.assertEqual(abandoned.job.phase,'refresh')
        self.now+=timedelta(seconds=11)
        self.engine.refresh_from=lambda key:[]
        result=worker.run_once()
        self.assertEqual((result.phase,result.status,result.attempts),('done','completed',2))
        with self.assertRaises(LeaseLost):
            self.queue._finish(abandoned,{})

    def test_fairness_allows_refresh_after_bounded_source_phases(self):
        self.assertion('old')
        worker=self.worker(lambda request:'{"assertions":[],"links":[]}',source_batch_limit=2)
        worker.run_once()
        for key in ('a','b','c'):
            self.raw(key)
        # The completed link counts as one source phase; one extract exhausts this budget.
        self.assertIsNotNone(worker.run_once().event_id)
        self.engine.refresh_from=lambda key:[]
        fair=worker.run_once()
        self.assertEqual(fair.assertion_id,'old')
        self.assertEqual(fair.status,'completed')
        self.assertTrue(any(job.phase=='extract' and job.status=='waiting_model' for job in self.queue.list()))

    def test_fair_refresh_advances_multiple_batches_under_continuous_source_backlog(self):
        calls=[]
        def provider(request):
            calls.append(request.task)
            return '{"assertions":[]}' if request.task.startswith('Extract') else 'summary'
        self.engine=MemoryEngine(self.store,model=provider)
        self.engine.refresh_from=lambda key: [self.engine.model(ModelRequest(name,[])) for name in ('first','second','third')] and []
        self.assertion('old')
        worker=self.worker(provider,source_batch_limit=1)
        worker.run_once()
        for key in ('a','b','c'):
            self.raw(key)
        self.assertEqual(worker.run_once().status,'pending')
        self.assertIsNotNone(worker.run_once().event_id)
        self.assertEqual(worker.run_once().status,'pending')
        self.assertIsNotNone(worker.run_once().event_id)
        self.assertEqual(worker.run_once().status,'completed')
        source_tasks = {'Extract attributable assertions from the user event', 'Review source material coverage'}
        self.assertEqual([name for name in calls if name not in source_tasks],['first','second','third'])
        self.assertEqual([name for name in calls if name in source_tasks], [
            'Extract attributable assertions from the user event', 'Review source material coverage',
            'Extract attributable assertions from the user event', 'Review source material coverage'])
        self.assertTrue(any(job.status=='waiting_model' for job in self.queue.list()))

    def test_expired_phase_attempt_limit_is_bounded_and_does_not_leave_running_lease(self):
        self.assertion('old')
        self.worker().run_once()
        first=self.queue._claim(with_model=False,source_first=True)
        self.now+=timedelta(seconds=11)
        second=self.queue._claim(with_model=False,source_first=True)
        self.assertEqual(second.job.attempts,2)
        self.now+=timedelta(seconds=11)
        self.assertIsNone(self.queue._claim(with_model=False,source_first=True))
        self.assertEqual(self.queue.get('old').status,'failed')
        with self.assertRaises(LeaseLost):
            self.queue._finish(first,{})

    def test_no_model_waiting_mode_finishes_refresh_without_busy_loop(self):
        self.assertion('one');self.assertion('two')
        worker=self.worker()
        jobs=worker.drain(limit=20)
        self.assertLess(len(jobs),20)
        self.assertIsNone(worker.run_once())
        self.assertTrue(any(job.status=='waiting_model' for job in self.queue.list()))
        self.assertIsNone(self.queue._seconds_until_work(with_model=False))


if __name__=='__main__':
    unittest.main()
