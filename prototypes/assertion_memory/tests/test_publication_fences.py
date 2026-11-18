"""Synthetic ownership races at real SQLite publication boundaries; no model IO."""
from contextvars import copy_context
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

from prototypes.assertion_memory.engine import MemoryEngine,ViewSpec,ViewVersion
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.publication_fences import bind_guard,check_guard,GuardLeaseLost


class PublicationFencesTest(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'fiction.sqlite3';self.store=MemoryStore(self.path);self.addCleanup(self.store.close)
        self.now=datetime(2099,1,1,tzinfo=timezone.utc)
        self.store.append_assertion(assertion_id='a',text='Synthetic permit is required.',actor='user',source='fixture://a',episode='fiction',scope='fiction',recorded_at='2026-01-01')
        self.engine=MemoryEngine(self.store);self.spec=ViewSpec('v','Synthetic permit','Synthetic permit?',scopes=('fiction',),root_ids=('a',));self.engine.define_view(self.spec)
        self.queue=CaptureQueue(self.store,lease_seconds=1,clock=lambda:self.now)
        self.raw=self.queue.capture_event('fiction-session','one','Synthetic user message.',source='fixture://message',scope='fiction')
        self.claim=self.queue._claim(with_model=True)

    def guard(self,db):
        self.assertIs(db,self.store._connection);self.assertTrue(db.in_transaction);self.assertTrue(self.store._writing)
        self.queue._assert_owned(db,self.claim)

    def published_state(self):
        tables=('memory_views','memory_view_versions','memory_view_checks','memory_historical_checks','memory_view_names','memory_view_name_work')
        with self.store.transaction() as db:
            return {table:[tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY 1')] for table in tables}

    def publish(self,summary='Synthetic accepted view.',snapshot=None):
        snapshot=snapshot or self.engine.capture_snapshot()
        version=ViewVersion(0,'v',summary,[],[],snapshot.source_version,'2026-01-02T00:00:00+00:00',
            snapshot.requested_valid_as_of,snapshot.requested_recorded_as_of,{},[],self.engine.pipeline_key)
        fingerprint=hashlib.sha256((summary+str(snapshot.source_version)).encode()).hexdigest()
        return self.engine._publish(self.spec,version,fingerprint,snapshot.source_version,snapshot.coverage(self.engine,self.spec),
            coverage_version=snapshot.coverage(self.engine,self.spec),review=None,snapshot=snapshot,receipts={})

    def test_expired_and_replaced_claim_write_no_view_check_name_current_or_failure(self):
        before=self.published_state();self.now+=timedelta(seconds=2)
        with bind_guard(self.engine,self.guard),self.assertRaises(GuardLeaseLost):self.publish()
        self.assertEqual(self.published_state(),before)
        self.now-=timedelta(seconds=2)
        with self.store.transaction(write=True) as db:db.execute('UPDATE memory_capture_jobs SET lease_token=? WHERE id=?',('replacement',self.raw.id))
        with bind_guard(self.engine,self.guard),self.assertRaises(GuardLeaseLost):self.publish()
        self.assertEqual(self.published_state(),before)

    def test_valid_claim_guard_runs_inside_owned_write_transaction_and_legacy_unbound_works(self):
        with bind_guard(self.engine,self.guard):version=self.publish()
        self.assertEqual(self.engine.inspect_view('v')['current_id'],version.id)
        self.now+=timedelta(seconds=2)
        unbound=self.publish('Unbound legacy query.')
        self.assertEqual(unbound.summary,'Unbound legacy query.')

    def test_revoked_mid_generation_cannot_publish_or_convert_to_failure_proof(self):
        def model(_request):
            self.now+=timedelta(seconds=2)
            return 'Synthetic permit is required [a].'
        self.engine.model=model;before=self.published_state()
        with bind_guard(self.engine,self.guard),self.assertRaises(GuardLeaseLost) as caught:self.engine.build_view('v')
        self.assertFalse(hasattr(caught.exception,'failure_version'))
        self.assertEqual(self.published_state(),before)

    def test_model_error_after_revocation_cannot_publish_negative_proof_or_error_pointer(self):
        def model(_request):
            self.now+=timedelta(seconds=2)
            raise ModelError('Synthetic writer error after token expiry')
        self.engine.model=model;before=self.published_state()
        with bind_guard(self.engine,self.guard),self.assertRaises(GuardLeaseLost):self.engine.build_view('v')
        self.assertEqual(self.published_state(),before)

    def test_direct_failure_writer_has_no_unguarded_publication_path(self):
        from prototypes.assertion_memory.build_proof import persist_failure,acquisition_state
        snapshot=self.engine.capture_snapshot();state=acquisition_state(self.engine,self.spec,snapshot);before=self.published_state();self.now+=timedelta(seconds=2)
        with bind_guard(self.engine,self.guard),self.assertRaises(GuardLeaseLost):
            persist_failure(self.engine,self.spec,snapshot,ModelError('Synthetic failed attempt'),state)
        self.assertEqual(self.published_state(),before)

    def test_nested_guards_compose_for_child_engine_sharing_same_store_and_reset(self):
        child=MemoryEngine(self.store);seen=[]
        with bind_guard(self.engine,lambda db:seen.append('parent')):
            with bind_guard(child,lambda db:seen.append('child')):
                with self.store.transaction(write=True) as db:check_guard(child,db)
            with self.store.transaction(write=True) as db:check_guard(child,db)
        self.assertEqual(seen,['parent','child','parent'])
        with bind_guard(self.engine,lambda db:False),bind_guard(child,lambda db:True):
            with self.assertRaises(GuardLeaseLost):self.publish()
        self.publish('Guards reset after exceptional nested exit.')

    def test_wrong_connection_or_read_only_transaction_cannot_validate_an_active_guard(self):
        other=MemoryStore(':memory:');self.addCleanup(other.close)
        with bind_guard(self.engine,lambda db:True):
            with other.transaction(write=True) as db:
                with self.assertRaises(GuardLeaseLost):check_guard(self.engine,db)
            with self.store.transaction() as db:
                with self.assertRaises(GuardLeaseLost):check_guard(self.engine,db)

    def test_owned_old_frozen_snapshot_can_publish_history_without_overwriting_new_current(self):
        older=self.engine.capture_snapshot()
        self.store.append_assertion(assertion_id='b',text='New synthetic condition.',actor='user',source='fixture://b',episode='fiction',scope='fiction',recorded_at='2026-01-02')
        with bind_guard(self.engine,self.guard):
            current=self.publish('New current view.')
            historical=self.publish('Owned old frozen view.',snapshot=older)
        self.assertNotEqual(current.id,historical.id)
        self.assertEqual(self.engine.inspect_view('v')['current_id'],current.id)
        self.assertEqual(len(self.engine.view_history('v')),2)

    def test_copied_thread_context_does_not_apply_another_store_guard(self):
        errors=[];published=[]
        with bind_guard(self.engine,lambda db:False):context=copy_context()
        def run():
            store=MemoryStore(self.path)
            try:
                engine=MemoryEngine(store)
                with bind_guard(engine,lambda db: db is store._connection and store._writing):
                    published.append(engine.build_view('v').id)
            except BaseException as error:errors.append(error)
            finally:store.close()
        thread=threading.Thread(target=lambda:context.run(run));thread.start();thread.join(timeout=10)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(len(published),1)
        self.assertEqual(self.engine.inspect_view('v')['current_id'],published[0])

    def test_direct_name_append_and_new_definition_are_guarded(self):
        def naming(request):
            if request.task=='Name memory view':return json.dumps({'title':'Synthetic permit conditions',
                'reason':'Fixture naming.'})
            return json.dumps({'accepted':True,'reason':'Fixture review.'})
        self.engine.model=naming
        candidate=self.engine.prepare_view_name('v',authorized_scopes=('fiction',),operation='fixture')
        approved=self.engine.review_view_name(candidate);before=self.published_state();self.now+=timedelta(seconds=2)
        from prototypes.assertion_memory.view_names import append
        with bind_guard(self.engine,self.guard),self.assertRaises(GuardLeaseLost):
            append(self.engine,approved,expected_definition_sha=approved['definition_sha256'],expected_previous_name_id=approved['previous_name_id'])
        self.assertEqual(self.published_state(),before)
        with bind_guard(self.engine,self.guard),self.assertRaises(GuardLeaseLost):
            self.engine.define_view(ViewSpec('unowned','Unowned definition','Unowned question?',scopes=('fiction',)))
        self.assertEqual(self.published_state(),before)
        # The exact candidate is genuinely valid: only lost execution ownership
        # prevented the append above, rather than a malformed fixture review.
        record=append(self.engine,approved,expected_definition_sha=approved['definition_sha256'],
            expected_previous_name_id=approved['previous_name_id'])
        self.assertEqual(record['title'],'Synthetic permit conditions')
        self.assertEqual(len(self.engine.view_checks('v')),0)

    def test_valid_owned_model_failure_keeps_ordinary_unaccepted_proof(self):
        def model(_request):raise ModelError('Synthetic legitimate writer failure')
        self.engine.model=model
        with bind_guard(self.engine,self.guard),self.assertRaises(ModelError) as caught:self.engine.build_view('v')
        self.assertNotIsInstance(caught.exception,GuardLeaseLost)
        self.assertTrue(caught.exception.failure_version.overview_review['failure_proof'])
        self.assertIsNone(self.engine.inspect_view('v')['current_id'])
        self.assertEqual(len(self.engine.view_history('v')),1)

    def test_a_guard_check_error_is_operational_lease_loss_not_semantic_failure(self):
        def invalid_check(db):raise ModelError('Synthetic guard failure, not a domain verdict')
        before=self.published_state()
        with bind_guard(self.engine,invalid_check),self.assertRaises(GuardLeaseLost) as caught:self.engine.build_view('v')
        self.assertIsInstance(caught.exception.__cause__,ModelError)
        self.assertEqual(self.published_state(),before)

    def test_reused_route_has_no_definition_path_that_bypasses_guard(self):
        from prototypes.assertion_memory.question_routes import _publish,_request_spec
        spec,_=_request_spec('Synthetic alias request',('fiction',),None,None)
        self.engine.question_history(spec.query,scopes=spec.scopes)
        self.now+=timedelta(seconds=2)
        plan={'reuse_view_id':'v','related_view_ids':[],'subquestions':[],'reason':'Fixture alias','method':'model','protocol':'fixture'}
        with bind_guard(self.engine,self.guard),self.assertRaises(GuardLeaseLost):_publish(self.engine,spec,plan)
        self.assertEqual(self.engine.question_history(spec.query,scopes=spec.scopes),[])

    def test_fixed_answer_check_revoked_during_review_has_no_independent_writer_bypass(self):
        from prototypes.assertion_memory.tests import test_answer_applicability as fixture
        case=fixture.ApplicabilityTest('test_alias_receipt_same_body_original_pointer_warm_zero_calls')
        case.setUp();self.addCleanup(case.doCleanups);case.build()
        before=case.engine.view_checks(case.spec.id);provider=case.provider;revoked={'value':False}
        def review(request):
            result=provider(request)
            if any(row.get('role')=='fixed_answer_applicability' for row in request.records):revoked['value']=True
            return result
        case.provider=review
        with bind_guard(case.engine,lambda db:not revoked['value']),self.assertRaises(GuardLeaseLost):case.alias()
        self.assertTrue(revoked['value'])
        self.assertEqual(case.engine.view_checks(case.spec.id),before)

    def navigation_plan(self):
        from prototypes.assertion_memory import background_overviews as background
        from prototypes.assertion_memory.discovery_frontier import corpus_state
        from prototypes.assertion_memory.question_routes import PLAN_PROTOCOL,_ensure_schema
        snapshot=self.engine.capture_snapshot();spec,request,_=background._identity(self.engine,'fiction',snapshot)
        _,_,fingerprints,revision=corpus_state(spec,snapshot)
        manifest={'request':request,'pipeline_key':self.engine.pipeline_key,'snapshot':snapshot.descriptor(),
            'corpus_revision':revision,'source_fingerprints':fingerprints,'unassigned_source_ids':[]}
        plan={'reuse_view_id':None,'related_view_ids':[],'subquestions':[],'answer_metadata_requests':[],
            'depends_on':[],'discover_topics':True,'reason':'Synthetic owned navigation plan','method':'model',
            'protocol':PLAN_PROTOCOL,'request':request,'discovery':manifest,
            'automatic':{'policy':background.POLICY,'scope':'fiction','root_request_id':spec.id}}
        with self.store.transaction(write=True) as db:_ensure_schema(db)
        return spec,request,snapshot,plan

    def test_navigation_registry_revoked_guard_has_zero_routes_or_semantic_publications(self):
        from prototypes.assertion_memory.background_overviews import publish_navigation_registry
        spec,_,_,plan=self.navigation_plan();before=self.published_state()
        with bind_guard(self.engine,lambda db:False),self.assertRaises(GuardLeaseLost):
            publish_navigation_registry(self.engine,spec,plan)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_question_routes').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_question_route_versions').fetchone()[0],0)
        self.assertEqual(self.published_state(),before)

    def test_navigation_registry_valid_guard_publishes_normally(self):
        from prototypes.assertion_memory.background_overviews import publish_navigation_registry
        spec,_,_,plan=self.navigation_plan()
        with bind_guard(self.engine,self.guard):route=publish_navigation_registry(self.engine,spec,plan)
        self.assertTrue(route['view_id'].startswith('navigation-registry-'))
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_question_routes').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_question_route_versions').fetchone()[0],1)
        self.assertEqual(self.engine.view_history('v'),[])

    def test_navigation_registry_idempotent_return_cannot_advance_unowned_checkpoint(self):
        from prototypes.assertion_memory.background_overviews import publish_navigation_registry,_route_for_generation
        spec,request,snapshot,plan=self.navigation_plan();publish_navigation_registry(self.engine,spec,plan)
        state={'stage':'discovery'};checkpoint={'overview':state};saved=[]
        with bind_guard(self.engine,lambda db:False),self.assertRaises(GuardLeaseLost):
            _route_for_generation(self.engine,spec,request,snapshot,state,checkpoint,lambda value:saved.append(dict(value)))
        self.assertEqual(state,{'stage':'discovery'});self.assertEqual(checkpoint,{'overview':{'stage':'discovery'}})
        self.assertEqual(saved,[])
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_question_route_versions').fetchone()[0],1)
