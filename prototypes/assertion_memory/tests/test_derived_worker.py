"""Real reviewed builder through worker provider boundaries and durable cache."""
import unittest

from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker
from prototypes.assertion_memory.tests import test_background_overviews as background_fixture


class DerivedWorkerTest(unittest.TestCase):
    semantic_model = background_fixture.BackgroundOverviewTest.model

    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.requests = []
        self.queue = CaptureQueue(self.store)
        for key,text in (('voltage','Voltage selection requires independent confirmation.'),
                         ('gate','Gate opening requires registry confirmation.')):
            self.store.append_assertion(assertion_id=key,text=text,actor='Mira',source='fixture://'+key,
                episode='fictional',scope='s',recorded_at='2026-01-01')

    def model(self, request):
        if request.task=='Review source material coverage':
            return '{"source_coverage":{"status":"accepted","reason":"No material source assertion in this fictional context.","omissions":[]}}'
        if request.task.startswith('Extract'):
            return '{"assertions":[]}'
        if request.task=='Propose evidence links for the source assertion':
            return '{"links":[]}'
        return self.semantic_model(request)

    def worker(self, model=None, **kwargs):
        engine = MemoryEngine(self.store,model=model or self.model,verify_overviews=True)
        return LinkWorker(self.queue,engine,source_first=True,**kwargs)

    def test_exact_cached_calls_resume_after_source_arrival_between_provider_steps(self):
        arrived=[]
        def model(request):
            result=self.model(request)
            if request.task=='Discover reusable memory themes' and not arrived:
                arrived.append(self.queue.capture_event('s','arriving','Fictional context',source='fixture://arriving'))
            return result
        worker=self.worker(model)
        yielded=worker.run_once()
        self.assertEqual(yielded.status,'pending')
        self.assertEqual(worker.run_once().event_id,arrived[0].id)
        # Fresh engine/worker must use SQLite responses, not a local model wrapper cache.
        resumed=self.worker(model).run_once()
        # Accepted empty source coverage also schedules its default scope receipt.
        self.assertEqual((resumed.scope,resumed.status),('default','idle'))
        resumed=self.worker(model).run_once()
        self.assertEqual((resumed.scope,resumed.status),('s','idle'))
        self.assertEqual(sum(r.task=='Discover reusable memory themes' for r in self.requests),1)
        row=worker.derived.status('s')[0]
        self.assertIsNotNone(row['current_version_id'])
        self.assertEqual(row['desired_frontier'],row['completed_frontier'])

    def test_derived_provider_progress_gets_bounded_turn_under_raw_backlog(self):
        for index in range(20):
            self.queue.capture_event('s',str(index),'Fictional context',source='fixture://'+str(index))
        worker=self.worker(source_batch_limit=1)
        first=worker.run_once()
        self.assertIsNotNone(first.event_id)
        for _ in range(3):
            worker.run_once()
        self.assertTrue(any(r.task=='Discover reusable memory themes' for r in self.requests))
        self.assertTrue(self.queue._ready_sources(with_model=True))

    def test_derived_failure_does_not_fail_completed_source_jobs(self):
        def model(request):
            if request.task=='Discover reusable memory themes':
                raise RuntimeError('fictional provider interruption')
            return self.model(request)
        event=self.queue.capture_event('s','raw','Fictional context',source='fixture://raw')
        worker=self.worker(model)
        self.assertEqual(worker.run_once().status,'completed')
        failed=worker.run_once()
        self.assertEqual(failed.status,'failed')
        self.assertEqual(self.queue.get(event.id).status,'completed')
        self.assertIn('fictional provider interruption',worker.derived.status('s')[0]['error'])

    def test_source_refresh_defers_managed_auto_dag_until_coalesced_scope_generation(self):
        worker=self.worker()
        self.assertEqual(worker.run_once().status,'idle')
        root=worker.derived.status('s')[0]['root_view_id']
        before=worker.engine.view_history(root)
        calls=len(self.requests)
        self.queue.capture(CaptureInput('Voltage requires a supplemental confirmation.','Mira',
            'fixture://clarification','another-session',scope='s',assertion_id='clarification'))
        self.assertEqual(worker.run_once().phase,'refresh')
        self.assertEqual(worker.run_once().status,'completed')
        self.assertEqual(worker.engine.view_history(root),before)
        self.assertEqual(len(self.requests),calls)
        self.assertEqual(worker.derived.status('s')[0]['state'],'pending')


if __name__ == '__main__':
    unittest.main()
