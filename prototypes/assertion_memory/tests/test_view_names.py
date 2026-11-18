"""Synthetic presentation names never recertify semantic knowledge."""
import json
import sqlite3
import unittest
from dataclasses import asdict
from unittest.mock import patch

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.storage import MemoryStore


class NamingModel:
    def __init__(self, title='Valve inspection conditions', accept=True):
        self.title, self.accept, self.requests = title, accept, []

    def __call__(self, request):
        self.requests.append(request)
        data = request.records[0]
        if request.task == 'Name memory view':
            return json.dumps({'title': self.title, 'reason': 'Names only the supplied question.',
                               'reference_ids': data['reference_ids']})
        if request.task == 'Review memory view name':
            return json.dumps({'accepted': self.accept, 'reason': 'Faithful nominal phrase.',
                'title': data['title'], 'input_fingerprint': data['input_fingerprint'],
                'context_sha256': data['context_sha256'], 'reference_ids': data['reference_ids']})
        raise AssertionError(request.task)


class ViewNamesTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.model = NamingModel()
        self.engine = MemoryEngine(self.store, model=self.model)
        self.spec = ViewSpec('fixture-view', 'Shared scope overview',
            'Under which reported conditions may the fictional valve be inspected?', scopes=('fiction',))
        self.engine.define_view(self.spec)

    def prepare(self, operation='test-v1', spec=None):
        return self.engine.prepare_view_name(spec or self.spec.id, authorized_scopes=('fiction',), operation=operation)

    def accepted(self, operation='test-v1', spec=None):
        return self.engine.review_view_name(self.prepare(operation, spec=spec))

    def append(self, approved):
        return self.engine.append_view_name(approved,
            expected_definition_sha=approved['definition_sha256'],
            expected_previous_name_id=approved['previous_name_id'])

    def semantic_bytes(self):
        with self.store.transaction() as c:
            tables = ['memory_views','memory_view_versions','memory_view_checks','memory_state']
            return {t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in tables}

    def test_legacy_correction_preserves_definition_and_all_semantic_state(self):
        before, pipeline = self.semantic_bytes(), self.engine.pipeline_key
        record = self.append(self.accepted())
        self.assertEqual(before, self.semantic_bytes())
        self.assertEqual(pipeline, self.engine.pipeline_key)
        self.assertEqual(self.spec, self.engine._spec(self.spec.id))
        name = self.engine.resolve_view_name(self.spec.id)
        self.assertEqual('Valve inspection conditions', name['display_title'])
        self.assertEqual(record['id'], name['name_record_id'])
        self.assertEqual('Shared scope overview', name['definition_title'])
        self.assertEqual([], self.engine.view_checks(self.spec.id))

    def test_name_needs_no_semantic_version_and_has_independent_review(self):
        approved = self.accepted()
        self.append(approved)
        self.assertEqual(['Name memory view','Review memory view name'], [r.task for r in self.model.requests])
        self.assertEqual([], self.engine.view_history(self.spec.id))

    def test_pinned_watermark_and_recording_epoch_do_not_leak_new_name(self):
        with patch('prototypes.assertion_memory.view_names._now', return_value='2026-05-01T00:00:00.000000+00:00'):
            pinned = self.engine.capture_presentation_snapshot()
            record = self.append(self.accepted())
        self.assertEqual('Shared scope overview', self.engine.resolve_view_name(self.spec.id, **pinned)['display_title'])
        self.assertEqual('Shared scope overview', self.engine.resolve_view_name(self.spec.id,
            presentation_cutoff='2026-04-30')['display_title'])
        self.assertEqual(record['title'], self.engine.resolve_view_name(self.spec.id,
            presentation_cutoff=record['recorded_at'])['display_title'])

    def test_idempotent_competing_writer_returns_saved_winner(self):
        first = self.accepted()
        self.model.title = 'Inspection constraints for the valve'
        competitor = self.accepted()
        one, two = self.append(first), self.append(competitor)
        self.assertEqual(one, two)
        self.assertEqual('Valve inspection conditions', two['title'])

    def test_stale_predecessor_cannot_overwrite_editorial_correction(self):
        one = self.accepted()
        stale = self.accepted('separate-editorial-revision')
        self.append(one)
        with self.assertRaisesRegex(ValueError, 'predecessor'):
            self.append(stale)
        self.model.title = 'Reported valve inspection conditions'
        self.append(self.accepted('separate-editorial-revision'))
        self.assertEqual(self.model.title, self.engine.resolve_view_name(self.spec.id)['display_title'])

    def test_review_must_bind_exact_title_and_context(self):
        approved = self.accepted()
        approved['title'] = 'Different valve conclusion'
        with self.assertRaisesRegex(ValueError, 'review'):
            self.append(approved)

    def test_rejection_and_provider_failure_accept_no_placeholder(self):
        self.model.accept = False
        with self.assertRaises(ModelError):
            self.accepted()
        self.assertEqual('legacy_unreviewed', self.engine.resolve_view_name(self.spec.id)['naming_status'])
        self.assertEqual(0, self.engine.capture_presentation_snapshot()['name_watermark'])

    def test_forbidden_operator_names_do_not_bypass_semantic_reviewer(self):
        for title in ('', 'Untitled', 'Shared scope overview', 'Без названия', 'Overview', self.spec.query[:20]):
            with self.subTest(title=title):
                self.model.title = title
                with self.assertRaises(ModelError):
                    self.accepted()

    def test_cross_scope_member_is_rejected_before_model_call(self):
        self.engine.define_view(ViewSpec('foreign', 'Private subject', 'Secret?', scopes=('other',)))
        unsafe = ViewSpec('bad', 'A title', 'What is related?', scopes=('fiction',), depends_on=('foreign',))
        with self.assertRaisesRegex(ValueError, 'scope'):
            self.prepare(spec=unsafe)
        self.assertEqual([], self.model.requests)

    def test_bounded_large_manifest_is_explicitly_unresolved(self):
        for i in range(50):
            self.engine.define_view(ViewSpec('child-'+str(i), 'Child topic', ('Long condition '*140), scopes=('fiction',)))
        spec = ViewSpec('large', 'Legacy index', 'Catalog?', scopes=('fiction',), depends_on=tuple('child-'+str(i) for i in range(50)))
        with self.assertRaisesRegex(ModelError, 'budget'):
            self.prepare(spec=spec)
        self.assertEqual([], self.model.requests)

    def test_new_definition_has_reviewed_title_and_rerun_keeps_stable_identity(self):
        spec = ViewSpec('new-fiction', 'internal request', self.spec.query, scopes=('fiction',))
        one = self.engine.define_named_view(spec, authorized_scopes=('fiction',), operation='new-v1')
        self.model.title = 'Another faithful inspection title'
        two = self.engine.define_named_view(spec, authorized_scopes=('fiction',), operation='new-v1')
        self.assertEqual(one, two)
        self.assertEqual('Valve inspection conditions', self.engine._spec(spec.id).title)
        self.assertEqual(self.spec.query, self.engine._spec(spec.id).query)
        self.assertEqual(2, len(self.model.requests))

    def test_history_metadata_uses_publication_cutoff_and_current_name_is_separate(self):
        from prototypes.assertion_memory.service import MemoryService
        self.engine.model = None
        version = self.engine.build_view(self.spec.id)
        self.engine.model = self.model
        self.append(self.accepted())
        page = MemoryService(self.store, self.engine).history(self.spec.id, version_id=version.id, section='metadata')
        data = json.loads(page['content'])
        self.assertEqual('Shared scope overview', data['presentation']['display_title'])
        self.assertEqual('Valve inspection conditions', data['current_presentation']['display_title'])

    def test_migration_dry_run_apply_resume_accounts_for_every_target(self):
        from prototypes.assertion_memory.view_names import migration_plan, apply_migration
        self.engine.define_view(ViewSpec('another','Untitled','When is a fictional valve checked?',scopes=('fiction',)))
        before = self.semantic_bytes()
        manifest = migration_plan(self.engine, scope='fiction', operation='migration-v1')
        self.assertEqual(before, self.semantic_bytes())
        self.assertEqual(2, manifest['target_count'])
        first = apply_migration(self.engine, manifest, scope='fiction')
        second = apply_migration(self.engine, manifest, scope='fiction')
        self.assertEqual(2, first['inserted_count'])
        self.assertEqual(2, second['already_applied_count'])
        self.assertEqual(2,len(second['counter_map']))
        self.assertEqual(before, self.semantic_bytes())

    def test_query_and_search_publish_additive_current_name_without_mutating_raw_title(self):
        self.engine.model=None
        version=self.engine.build_view(self.spec.id)
        self.engine.model=self.model
        self.append(self.accepted())
        from prototypes.assertion_memory.service import MemoryService
        service=MemoryService(self.store,self.engine)
        check=self.engine.latest_check(version)
        state=service.status(scope='fiction',limit=1)
        result=service._query_result(version,{'request':{'question':self.spec.query}},state,check,'fiction',None)
        self.assertEqual('Valve inspection conditions',result['presentation']['display_title'])
        found=self.engine.find_views('valve',scopes=('fiction',))
        self.assertEqual('Shared scope overview',found[0]['title'])
        self.assertEqual('Valve inspection conditions',found[0]['display_title'])

    def test_pending_definition_can_receive_name_later_without_redefinition(self):
        spec=ViewSpec('pending','Full fictional question?','Full fictional question?',scopes=('fiction',))
        self.engine.define_named_view(spec,authorized_scopes=('fiction',),operation='retry-v1',automatic=True)
        before=asdict(self.engine._spec(spec.id))
        self.engine.naming_model=self.model
        self.engine.define_named_view(spec,authorized_scopes=('fiction',),operation='retry-v1',automatic=True)
        self.assertEqual(before,asdict(self.engine._spec(spec.id)))
        self.assertEqual([],self.model.requests)
        from prototypes.assertion_memory.queue import CaptureQueue
        from prototypes.assertion_memory.worker import LinkWorker
        worker=LinkWorker(CaptureQueue(self.store),self.engine)
        worker.run_once();worker.run_once()
        self.assertEqual('accepted',self.engine.resolve_view_name(spec.id)['naming_status'])

    def test_custom_provider_exception_is_pending_without_failed_semantic_check(self):
        def broken(request):
            raise OSError('fictional provider unavailable')
        self.engine.naming_model=broken
        spec=ViewSpec('outage','internal','Which fictional maintenance conditions apply?',scopes=('fiction',))
        saved=self.engine.define_named_view(spec,authorized_scopes=('fiction',),operation='outage-v1',automatic=True)
        self.assertEqual([],self.engine.view_checks(saved.id))
        self.assertEqual('naming_pending',self.engine.resolve_view_name(saved.id)['naming_status'])

    def test_source_provenance_keeps_actor_and_current_eligibility_without_foreign_context(self):
        self.store.append_assertion(assertion_id='eligible',text='Fictional inspection condition.',actor='fictional-reporter',
            source='fixture://report',episode='fixture',scope='fiction',recorded_at='2026-01-01',valid_at='2026-01-01')
        self.store.append_assertion(assertion_id='future',text='Future fictional inspection condition.',actor='other-reporter',
            source='fixture://future',episode='fixture',scope='fiction',recorded_at='2026-01-01',valid_at='2100-01-01')
        spec=ViewSpec('grounded','Internal',self.spec.query,scopes=('fiction',),root_ids=('eligible',))
        approved=self.accepted(spec=spec)
        self.assertEqual('fictional-reporter',approved['context']['references'][0]['actor'])
        self.engine.define_view(spec)
        record=self.append(approved)
        self.assertEqual('fictional-reporter',record['provenance']['references'][0]['actor'])
        with self.assertRaisesRegex(ValueError,'eligible'):
            self.prepare(spec=ViewSpec('future-view','Internal',self.spec.query,scopes=('fiction',),root_ids=('future',)))

    def test_actual_migration_renames_old_current_version_but_not_known_then(self):
        from prototypes.assertion_memory.view_names import migration_plan,apply_migration,presentation
        self.engine.model=None
        version=self.engine.build_view(self.spec.id)
        old_check=self.engine.latest_check(version)
        before=self.semantic_bytes()
        self.engine.model=self.model
        manifest=migration_plan(self.engine,scope='fiction',operation='dated-rollout-v1')
        result=apply_migration(self.engine,manifest,scope='fiction')
        self.assertEqual(1,result['inserted_count'])
        current,_=presentation(self.engine,self.spec.id)
        known_then,_=presentation(self.engine,self.spec.id,recorded_as_of=version.created_at)
        saved_then,_=presentation(self.engine,self.spec.id,published_at=version.created_at)
        self.assertEqual('Valve inspection conditions',current['display_title'])
        self.assertGreater(current['name_recorded_at'],version.created_at)
        self.assertEqual('Shared scope overview',known_then['display_title'])
        self.assertEqual('Shared scope overview',saved_then['display_title'])
        self.assertEqual(before,self.semantic_bytes())
        self.assertEqual(version.id,self.engine.inspect_view(self.spec.id)['current_id'])
        self.assertEqual(old_check,self.engine.latest_check(version))

    def test_new_definition_canonical_name_does_not_leak_before_its_name_record(self):
        spec=ViewSpec('new-dated',self.spec.query,self.spec.query,scopes=('fiction',))
        self.engine.define_named_view(spec,authorized_scopes=('fiction',),operation='new-dated-v1')
        self.assertEqual('Valve inspection conditions',self.engine._spec(spec.id).title)
        for bounds in ({'presentation_cutoff':'2000-01-01'},{'name_watermark':0}):
            with self.subTest(bounds=bounds):
                name=self.engine.resolve_view_name(spec.id,**bounds)
                self.assertIsNone(name['display_title'])
                self.assertEqual('naming_pending',name['naming_status'])

    def test_prepare_existing_id_uses_immutable_definition_even_when_given_a_spec(self):
        changed_title=ViewSpec(self.spec.id,'Provisional alternate title',self.spec.query,scopes=('fiction',))
        candidate=self.prepare(spec=changed_title)
        self.assertEqual(self.spec.title,candidate['input_spec']['title'])
        changed_question=ViewSpec(self.spec.id,'Internal','A different fictional question?',scopes=('fiction',))
        with self.assertRaisesRegex(ValueError,'semantic definition'):
            self.prepare(spec=changed_question)

    def test_migration_rejection_retains_exact_candidate_and_review_outcome(self):
        from prototypes.assertion_memory.view_names import migration_plan
        self.model.accept=False
        manifest=migration_plan(self.engine,scope='fiction',operation='rejected-migration-v1')
        target=manifest['targets'][0]
        self.assertEqual('unresolved',target['outcome'])
        self.assertEqual('Valve inspection conditions',target['candidate']['title'])
        self.assertEqual('rejected',target['review_outcome'])
        self.assertTrue(target['candidate']['input_fingerprint'])
        self.assertEqual([],self.engine.view_checks(self.spec.id))

    def test_name_table_is_append_only_even_for_direct_sql(self):
        self.append(self.accepted())
        for sql in ('UPDATE memory_view_names SET title="wrong"', 'DELETE FROM memory_view_names'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction(write=True) as c:
                    c.execute(sql)


class MigrationCommandTest(unittest.TestCase):
    def test_readonly_plan_and_apply_use_exact_manifest_without_model_at_apply(self):
        import hashlib
        import tempfile
        from pathlib import Path
        from prototypes.assertion_memory.mcp_server import ServiceConfig
        from prototypes.assertion_memory.view_name_migration import run
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'synthetic.sqlite3'
            manifest=Path(directory)/'plan.json'
            backup=Path(directory)/'before-names.sqlite3'
            with MemoryStore(db) as store:
                engine=MemoryEngine(store,model=NamingModel())
                engine.define_view(ViewSpec('synthetic','Untitled','Which fictional valve conditions apply?',scopes=('fiction',)))
            before=hashlib.sha256(db.read_bytes()).hexdigest()
            pending=run(ServiceConfig(str(db),'none'),scope='fiction',operation='migration-fixture',manifest_path=manifest)
            self.assertEqual(before,hashlib.sha256(db.read_bytes()).hexdigest())
            self.assertEqual(1,pending['unresolved_count'])
            from prototypes.assertion_memory.view_names import migration_plan
            with MemoryStore(db) as store:
                engine=MemoryEngine(store,model=NamingModel())
                approved=migration_plan(engine,scope='fiction',operation='migration-fixture')
            manifest.write_text(json.dumps(approved))
            result=run(ServiceConfig(str(db),'none'),scope='fiction',manifest_path=manifest,apply=True,backup_path=backup)
            self.assertEqual(1,result['inserted_count'])
            self.assertTrue(backup.exists())
            with MemoryStore(db) as store:
                self.assertEqual('Valve inspection conditions',MemoryEngine(store).resolve_view_name('synthetic')['display_title'])


class ProducerNamesTest(unittest.TestCase):
    def test_automatic_leaf_uses_separate_reviewed_name_and_whole_question(self):
        from prototypes.assertion_memory.topic_discovery import _definitions
        with MemoryStore(':memory:') as store:
            store.append_assertion(assertion_id='synthetic-root',text='The fictional valve needs inspection.',
                actor='fictional-reporter',source='fixture://report',episode='fixture',scope='fiction',
                recorded_at='2026-01-01',valid_at='2026-01-01')
            names = NamingModel()
            engine = MemoryEngine(store,naming_model=names)
            question = 'Under which conditions ' + 'may the fictional inspection proceed ' * 12 + '?'
            theme = {'question':question,'source_ids':['synthetic-root']}
            key = _definitions(engine,ViewSpec('parent','Internal','Whole picture',scopes=('fiction',)),[theme])[0]
            self.assertEqual(question,engine._spec(key).query)
            self.assertEqual(question,engine._spec(key).title)
            self.assertEqual([],names.requests)
            from prototypes.assertion_memory.queue import CaptureQueue
            from prototypes.assertion_memory.worker import LinkWorker
            worker=LinkWorker(CaptureQueue(store),engine)
            worker.run_once();worker.run_once()
            self.assertEqual(names.title,engine.resolve_view_name(key)['display_title'])
            self.assertEqual('accepted',engine.resolve_view_name(key)['naming_status'])
            _definitions(engine,ViewSpec('parent','Internal','Whole picture',scopes=('fiction',)),[theme])
            self.assertEqual(2,len(names.requests))

    def test_unknown_naming_response_keeps_semantic_build_independent(self):
        from prototypes.assertion_memory.topic_discovery import _definitions
        with MemoryStore(':memory:') as store:
            engine = MemoryEngine(store,naming_model=lambda request:'{"unrelated":"response"}')
            question = 'Which inspection conditions were reported?'
            key = _definitions(engine,ViewSpec('parent','Internal','Whole picture',scopes=('fiction',)),
                [{'question':question,'source_ids':[]}])[0]
            version = engine.build_view(key)
            self.assertEqual('naming_pending',engine.resolve_view_name(key)['naming_status'])
            self.assertIsNone(engine.resolve_view_name(key)['display_title'])
            self.assertIsNotNone(version.id)
            self.assertNotEqual('unresolved',(version.overview_review or {}).get('status'))
            from prototypes.assertion_memory.queue import CaptureQueue
            from prototypes.assertion_memory.worker import LinkWorker
            worker=LinkWorker(CaptureQueue(store),engine)
            worker.run_once();worker.run_once()
            self.assertEqual('unresolved',worker.names.status()['entries'][0]['status'])
            self.assertIsNone(engine.resolve_view_name(key)['display_title'])

    def test_legacy_request_root_is_named_from_its_registered_member_questions(self):
        from prototypes.assertion_memory.tests.test_navigation_indexes import NavigationIndexTest
        from prototypes.assertion_memory.background_overviews import _identity
        fixture=NavigationIndexTest('test_root_is_exact_question_directory_without_domain_writer')
        fixture.setUp()
        try:
            engine,snapshot,checkpoint,result,root=fixture.build_directory()
            legacy,_,_=_identity(engine,'s',snapshot)
            engine.define_view(legacy)
            names=NamingModel('Voltage, gate and interface topics')
            candidate=engine.prepare_view_name(legacy.id,authorized_scopes=('s',),operation='legacy-root-v1',model=names)
            self.assertEqual('navigation_index',candidate['context']['purpose'])
            self.assertEqual(set(root.upstream),{m['id'] for m in candidate['context']['members']})
            self.assertTrue(any(r['kind']=='route' for r in candidate['context']['references']))
            self.assertEqual(legacy,engine._spec(legacy.id))
        finally:
            fixture.doCleanups()

    def test_catalog_name_does_not_change_membership_or_domain_proof(self):
        from prototypes.assertion_memory.tests.test_navigation_indexes import NavigationIndexTest
        from prototypes.assertion_memory.background_overviews import navigation_context
        from prototypes.assertion_memory.navigation_index import validate_publication
        from prototypes.assertion_memory.query_snapshot import receipt, published_winner
        from prototypes.assertion_memory.recorded_history import authorization
        fixture=NavigationIndexTest('test_root_is_exact_question_directory_without_domain_writer')
        fixture.setUp()
        try:
            engine,snapshot,checkpoint,result,root=fixture.build_directory()
            context=navigation_context(engine,engine._spec(root.view_id),snapshot)
            old_receipt=receipt(engine,result['check_id'])
            old_auth=authorization(engine,engine._spec(root.view_id))
            tables=['memory_views','memory_view_versions','memory_view_checks','memory_question_route_versions','memory_question_routes','memory_assertions','memory_edges','memory_state']
            def frozen():
                return {t:[tuple(r) for r in engine.store._connection.execute('SELECT * FROM '+t)] for t in tables}
            before=frozen()
            names=NamingModel('Voltage, gate and interface topics')
            for key in (root.view_id,*root.upstream):
                candidate=engine.prepare_view_name(key,authorized_scopes=('s',),operation='nav-migration',model=names)
                accepted=engine.review_view_name(candidate,model=names)
                engine.append_view_name(accepted,expected_definition_sha=accepted['definition_sha256'],expected_previous_name_id=None)
            self.assertEqual(before,frozen())
            self.assertTrue(validate_publication(engine,snapshot,context,root,old_receipt))
            self.assertEqual(old_receipt,receipt(engine,result['check_id']))
            self.assertEqual(old_auth,authorization(engine,engine._spec(root.view_id)))
            self.assertEqual('navigation_index',root.overview_review['assembly_coverage'])
            self.assertEqual('not_assessed',root.overview_review['domain_coverage'])
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
