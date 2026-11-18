"""Hermetic eventual editorial naming, independent of semantic publication."""
import json
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.worker import LinkWorker
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.tests.test_view_names import NamingModel


class NameWorkTest(unittest.TestCase):
    def setUp(self):
        self.store=MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.now=datetime.now(timezone.utc)+timedelta(seconds=1)
        self.model=NamingModel()
        self.engine=MemoryEngine(self.store,naming_model=self.model)
        self.queue=CaptureQueue(self.store,clock=lambda:self.now,lease_seconds=10)
        self.spec=ViewSpec('fiction-machine','Internal','Which fictional valve conditions apply?',scopes=('fiction',))

    def worker(self):
        return LinkWorker(self.queue,self.engine,source_first=True,source_batch_limit=2)

    def enqueue(self):
        return self.engine.define_named_view(self.spec,authorized_scopes=('fiction',),operation='automatic-fixture-v1',automatic=True)

    def test_automatic_definition_is_nonblocking_and_two_worker_turns_name_without_source(self):
        self.enqueue()
        self.assertEqual([],self.model.requests)
        self.assertEqual(self.spec.query,self.engine._spec(self.spec.id).title)
        before=self.store.version
        worker=self.worker()
        with patch.object(self.engine,'capture_snapshot',side_effect=AssertionError('editorial must not capture semantic scope')):
            self.assertEqual(0,worker.seconds_until_work(with_model=True))
            worker.run_once()
            self.assertEqual(1,len(self.model.requests))
            self.assertIsNone(self.engine.resolve_view_name(self.spec.id)['display_title'])
            worker.run_once()
        self.assertEqual(2,len(self.model.requests))
        self.assertEqual(self.model.title,self.engine.resolve_view_name(self.spec.id)['display_title'])
        self.assertEqual(before,self.store.version)
        self.assertEqual([],self.engine.view_checks(self.spec.id))

    def test_transient_writer_outage_recovers_after_restart_without_source(self):
        self.enqueue()
        calls=[]
        def offline(request):
            calls.append(request.task)
            raise OSError('synthetic outage')
        self.engine.naming_model=offline
        worker=self.worker(); worker.run_once()
        state=worker.names.status(scope='fiction')['entries'][0]
        self.assertEqual('retry',state['status'])
        self.assertEqual('transient',state['error_kind'])
        self.assertGreater(worker.seconds_until_work(with_model=True),0)
        self.now+=timedelta(seconds=600)
        self.engine.naming_model=self.model
        worker=self.worker(); worker.run_once();worker.run_once()
        self.assertEqual(self.model.title,self.engine.resolve_view_name(self.spec.id)['display_title'])
        self.assertEqual(1,len(calls))

    def test_reviewer_outage_preserves_writer_checkpoint_and_never_regenerates_it(self):
        self.enqueue();worker=self.worker();worker.run_once()
        saved=worker.names.status(scope='fiction')['entries'][0]
        self.assertEqual('reviewer',saved['phase'])
        def unavailable(request):
            raise ModelError('synthetic provider unavailable')
        self.engine.naming_model=unavailable
        self.worker().run_once()
        self.now+=timedelta(seconds=600)
        self.engine.naming_model=self.model
        self.worker().run_once()
        self.assertEqual(['Name memory view','Review memory view name'],[r.task for r in self.model.requests])
        self.assertEqual('accepted',self.engine.resolve_view_name(self.spec.id)['naming_status'])

    def test_deterministic_rejection_is_bounded_and_separate_from_world_state(self):
        self.model.accept=False
        self.enqueue();worker=self.worker()
        for _ in range(8):worker.run_once()
        state=worker.names.status(scope='fiction')['entries'][0]
        self.assertEqual('unresolved',state['status'])
        self.assertLessEqual(len(self.model.requests),4)
        self.assertGreaterEqual(len(self.model.requests),3)
        self.assertEqual([],self.engine.view_checks(self.spec.id))
        self.assertEqual(0,self.engine.capture_presentation_snapshot()['name_watermark'])

    def test_provider_absence_is_durable_and_recovery_needs_no_source(self):
        self.engine.naming_model=None;self.enqueue();worker=self.worker()
        worker.run_once()
        self.assertEqual('waiting_model',worker.names.status(scope='fiction')['entries'][0]['status'])
        self.assertIsNone(worker.seconds_until_work(with_model=False))
        self.engine.naming_model=self.model
        self.worker().run_once();self.worker().run_once()
        self.assertEqual('accepted',self.engine.resolve_view_name(self.spec.id)['naming_status'])

    def test_route_winner_has_no_visible_losing_definition_or_naming_job(self):
        from prototypes.assertion_memory.question_routes import _publish,PLAN_PROTOCOL
        spec=ViewSpec('fiction-request','Internal','Which fictional conditions?',scopes=('fiction',))
        def plan(question):
            return {'reuse_view_id':None,'depends_on':[],'related_view_ids':[],
                'subquestions':[question],'answer_metadata_requests':[],'method':'model',
                'protocol':PLAN_PROTOCOL,'request':{'question':spec.query,'scopes':['fiction']}}
        original=self.store.transaction
        enabled=True
        @contextmanager
        def interleave(*,write=False):
            nonlocal enabled
            outer=write and enabled and not self.store._connection.in_transaction
            with original(write=write) as connection:yield connection
            if outer:
                enabled=False
                _publish(self.engine,spec,plan('Competing fictional question?'))
        with patch.object(self.store,'transaction',interleave):
            selected=_publish(self.engine,spec,plan('Original fictional question?'))
        specs=self.engine.list_views()
        self.assertEqual([selected['view_id']],[s.id for s in specs])
        from prototypes.assertion_memory.view_name_work import NameWorkQueue
        self.assertEqual([selected['view_id']],[r['view_id'] for r in NameWorkQueue(self.store).status()['entries']])
        self.assertEqual([],self.model.requests)

    def test_completed_name_does_not_reenqueue_or_call_provider(self):
        self.enqueue();worker=self.worker();worker.run_once();worker.run_once()
        self.enqueue();self.worker().run_once()
        self.assertEqual(2,len(self.model.requests))
        self.assertEqual(1,self.engine.capture_presentation_snapshot()['name_watermark'])

    def test_source_backlog_yields_between_writer_and_reviewer_without_starving_names(self):
        from prototypes.assertion_memory.capture import CaptureInput
        self.enqueue()
        for index in range(80):
            self.queue.capture(CaptureInput(assertion_id='fiction-source-'+str(index),
                text='Fictional record '+str(index),actor='reporter',source='fixture://record',
                episode='fixture',scope='fiction'))
        worker=self.worker();editorial_turns=[];source_turns=0
        for _ in range(24):
            before=len(self.model.requests)
            result=worker.run_once()
            self.assertLessEqual(len(self.model.requests)-before,1)
            if getattr(result,'kind',None)=='view_name':editorial_turns.append(source_turns)
            elif result is not None:source_turns+=1
            if self.engine.resolve_view_name(self.spec.id)['naming_status']=='accepted':break
        self.assertEqual('accepted',self.engine.resolve_view_name(self.spec.id)['naming_status'])
        self.assertEqual(2,len(editorial_turns))
        self.assertGreater(editorial_turns[1],editorial_turns[0])
        self.assertTrue(self.queue._ready_sources(with_model=False))

    def test_lease_loser_cannot_publish_and_new_owner_can_resume(self):
        self.enqueue();worker=self.worker();worker.run_once()
        normal=self.model
        def lose_ownership(request):
            output=normal(request)
            self.now+=timedelta(seconds=20)
            contender=worker.names.claim(self.engine,with_model=True)
            worker.names.release(contender)
            return output
        self.engine.naming_model=lose_ownership
        worker.run_once()
        self.assertEqual(0,self.engine.capture_presentation_snapshot()['name_watermark'])
        self.engine.naming_model=normal
        self.worker().run_once()
        self.assertEqual(1,self.engine.capture_presentation_snapshot()['name_watermark'])
        self.assertEqual('completed',worker.names.status()['entries'][0]['status'])

    def test_failure_after_append_rolls_back_name_and_resumes_cached_review(self):
        self.enqueue();worker=self.worker();worker.run_once()
        with self.store.transaction(write=True) as c:
            c.execute("CREATE TRIGGER fixture_crash BEFORE UPDATE ON memory_view_name_work WHEN new.status='completed' "
                "BEGIN SELECT RAISE(ABORT,'synthetic crash after name append'); END")
        worker.run_once()
        self.assertEqual(0,self.engine.capture_presentation_snapshot()['name_watermark'])
        with self.store.transaction(write=True) as c:c.execute('DROP TRIGGER fixture_crash')
        self.now+=timedelta(seconds=600)
        self.worker().run_once()
        self.assertEqual(1,self.engine.capture_presentation_snapshot()['name_watermark'])
        self.assertEqual(2,len(self.model.requests))

    def test_startup_reconciliation_is_bounded_and_leaves_explicit_custom_views_alone(self):
        import hashlib
        custom=ViewSpec('explicit-caller','Maintenance overview','Maintenance overview',scopes=('fiction',))
        self.engine.define_view(custom)
        question='Which fictional repair conditions apply?'
        key='question-'+hashlib.sha256(json.dumps([question,('fiction',)]).encode()).hexdigest()[:20]
        self.engine.define_view(ViewSpec(key,question,question,scopes=('fiction',)))
        worker=self.worker()
        with patch.object(self.engine,'list_views',side_effect=AssertionError('no full engine scan')):
            self.assertTrue(worker.names.reconcile_page(self.engine,limit=1))
            self.assertEqual(0,worker.names.status()['total'])
            worker.names.reconcile_page(self.engine,limit=1)
            worker.names.reconcile_page(self.engine,limit=1)
        self.assertEqual([key],[entry['view_id'] for entry in worker.names.status()['entries']])
        self.assertEqual([],self.model.requests)

    def test_status_reports_editorial_deadline_separately_from_source_health(self):
        from prototypes.assertion_memory.service import MemoryService
        self.enqueue()
        service=MemoryService(self.store,self.engine)
        page=service.status(scope='fiction',section='view_names',limit=100)
        payload=page['view_names']
        self.assertEqual({'pending':1},payload['counts'])
        self.assertEqual(self.spec.id,payload['entries'][0]['view_id'])
        self.assertEqual(0,service.status(scope='fiction')['pending_count'])
        other=service.status(scope='other',section='view_names')['view_names']
        self.assertEqual(0,other['total'])

    def test_stale_reviewer_context_is_reacquired_and_foreign_scope_is_never_read(self):
        self.enqueue();worker=self.worker();worker.run_once()
        with patch('prototypes.assertion_memory.view_name_work._context',return_value={'changed':'fixture'}):
            worker.run_once()
        self.assertEqual(0,self.engine.capture_presentation_snapshot()['name_watermark'])
        self.assertEqual('writer',worker.names.status()['entries'][0]['phase'])
        worker.run_once();worker.run_once()
        self.assertEqual('accepted',self.engine.resolve_view_name(self.spec.id)['naming_status'])
        foreign=ViewSpec('foreign-name','Internal','Other scope question?',scopes=('other',))
        self.engine.define_named_view(foreign,authorized_scopes=('fiction',),operation='unauthorized',automatic=True)
        self.assertNotIn(foreign.id,[entry['view_id'] for entry in worker.names.status()['entries']])

    def test_normal_mcp_query_wakes_existing_worker_launcher_for_durable_names(self):
        import asyncio
        import tempfile
        from pathlib import Path
        from prototypes.assertion_memory import mcp_server
        with tempfile.TemporaryDirectory() as directory:
            config=mcp_server.ServiceConfig(str(Path(directory)/'fiction.sqlite3'),'none')
            with patch.object(mcp_server.WorkerLauncher,'start',return_value={'scheduled':True}) as wake:
                server=mcp_server.create_server(config,background=True)
                tool=server._tool_manager.get_tool('memory_query')
                asyncio.run(tool.fn('Which fictional conditions apply?','fiction'))
                self.assertEqual(1,wake.call_count)

    def test_editorial_status_pages_fit_the_unchanged_full_mcp_envelope(self):
        from prototypes.assertion_memory.service import MemoryService,_fits
        for index in range(60):
            self.engine.define_named_view(ViewSpec('fiction-'+str(index),'Internal',
                'Which fictional condition '+str(index)+' applies?',scopes=('fiction',)),
                authorized_scopes=('fiction',),operation='envelope-fixture',automatic=True)
        with self.store.transaction(write=True) as c:
            c.execute('UPDATE memory_view_name_work SET error=?',('x'*107,))
        service=MemoryService(self.store,self.engine)
        page=service.status(scope='fiction',section='view_names',limit=100)
        keys=[]
        while True:
            self.assertTrue(_fits(page))
            keys.extend(row['view_id'] for row in page['view_names']['entries'])
            if page['continuation'] is None:break
            page=service.status(**page['continuation']['arguments'])
        self.assertEqual(60,len(set(keys)))
        self.assertEqual(60,len(keys))

    def test_planner_outage_persists_naming_intent_and_recovers_without_another_query(self):
        from prototypes.assertion_memory.question_routes import _request_spec
        tasks=[]
        def unavailable_planner(request):
            tasks.append(request.task)
            raise ModelError('synthetic planner outage')
        self.engine.model=unavailable_planner
        question='Which fictional valve conditions were reported?'
        with self.assertRaisesRegex(ModelError,'planner outage'):
            self.engine.prepare_question(question,scopes=('fiction',))
        spec,_=_request_spec(question,('fiction',),None,None)
        self.assertEqual(question,self.engine._spec(spec.id).query)
        self.assertEqual(['Plan memory retrieval for a question'],tasks)
        worker=self.worker()
        self.assertEqual([spec.id],[entry['view_id'] for entry in worker.names.status()['entries']])
        before=[tuple(row) for row in self.store._connection.execute('SELECT * FROM memory_view_checks')]
        worker.run_once();worker.run_once()
        self.assertEqual(self.model.title,self.engine.resolve_view_name(spec.id)['display_title'])
        self.assertEqual(before,[tuple(row) for row in self.store._connection.execute('SELECT * FROM memory_view_checks')])
        self.assertIsNone(self.engine.inspect_view(spec.id)['current_id'])
        self.assertEqual(0,len(self.store.list_assertions()))
        self.assertEqual(['Plan memory retrieval for a question'],tasks)

    def test_startup_recovers_preupgrade_current_32_key_without_an_existing_job(self):
        from prototypes.assertion_memory.question_routes import _request_spec
        spec,_=_request_spec('Which fictional valve conditions apply?',('fiction',),None,None)
        self.engine.define_view(spec)
        worker=self.worker();worker.run_once();worker.run_once()
        self.assertEqual('accepted',self.engine.resolve_view_name(spec.id)['naming_status'])
        self.assertEqual(2,len(self.model.requests))

    def test_startup_recovers_preupgrade_temporal_failure_keys_from_recorded_cutoffs(self):
        from prototypes.assertion_memory.question_routes import _request_spec
        specs=[]
        self.engine.model=lambda request:(_ for _ in ()).throw(ModelError('synthetic planner outage'))
        for valid,recorded in (('2025-01-01',None),(None,'2025-02-01'),('2025-03-01','2025-04-01')):
            question='Which fictional valve conditions apply at the requested cutoff?'
            with self.assertRaises(ModelError):
                self.engine.prepare_question(question,scopes=('fiction',),valid_as_of=valid,recorded_as_of=recorded)
            spec,_=_request_spec(question,('fiction',),valid,recorded)
            specs.append(spec)
        with self.store.transaction(write=True) as c:
            c.execute('DELETE FROM memory_view_name_work') # simulate the pre-upgrade failure producer
        before=[tuple(row) for row in self.store._connection.execute('SELECT * FROM memory_view_versions')]
        worker=self.worker()
        for _ in range(6):worker.run_once()
        self.assertEqual({'accepted'},{self.engine.resolve_view_name(spec.id)['naming_status'] for spec in specs})
        self.assertEqual(before,[tuple(row) for row in self.store._connection.execute('SELECT * FROM memory_view_versions')])
        self.assertTrue(all(self.engine.inspect_view(spec.id)['current_id'] is None for spec in specs))

    def test_startup_reconciliation_preserves_an_existing_durable_writer_checkpoint(self):
        from prototypes.assertion_memory.question_routes import _request_spec
        from prototypes.assertion_memory.view_name_work import run_turn
        spec,_=_request_spec('Which fictional valve conditions apply?',('fiction',),None,None)
        self.engine.define_named_view(spec,authorized_scopes=('fiction',),operation='existing-intent-v1',automatic=True)
        worker=self.worker()
        run_turn(worker) # previously saved writer; startup cursor has not processed this definition yet
        worker.names.reconcile_page(self.engine)
        worker.run_once()
        self.assertEqual(['Name memory view','Review memory view name'],[row.task for row in self.model.requests])
        self.assertEqual('accepted',self.engine.resolve_view_name(spec.id)['naming_status'])


if __name__=='__main__':unittest.main()
