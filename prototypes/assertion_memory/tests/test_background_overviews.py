"""System-owned scope discovery, reviewed materialization and resumable progress."""
import copy
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import format_prompt
from prototypes.assertion_memory.storage import MemoryStore


class BackgroundOverviewTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.requests = []
        for key, text, actor, session in (
            ('voltage', 'Voltage selection requires independent confirmation.', 'Ada', 'one'),
            ('gate', 'Gate opening requires the registry check.', 'Ben', 'two'),
            ('interface', 'Voltage gate interface requires both confirmations.', 'Cy', 'one')):
            self.store.append_assertion(assertion_id=key, text=text, actor=actor, source='fixture://' + key,
                episode=session, scope='s', recorded_at='2026-01-01', metadata={'kind': 'assertion'})

    def model(self, request):
        self.requests.append(request)
        if request.task == 'Plan reusable memory theme hierarchy':
            return json.dumps({'groups': []})
        if request.task == 'Write attributed thematic map':
            prose = [row.get('summary', row.get('text', '')) for row in request.records
                     if row.get('role') in ('source', 'verified_navigation_contribution')]
            return json.dumps({'summary': '\n'.join(prose)})
        if request.task == 'Review attributed thematic map':
            return json.dumps({'status': 'accepted', 'reason': 'All supplied qualified meanings retained.',
                'issues': [], 'faithfulness': True, 'navigation_coverage': 'complete',
                'qualifications_preserved': True})
        if request.task == 'Discover reusable memory themes':
            rows = [row for row in request.records if row.get('role') == 'source']
            themes = []
            for question, word in (('What voltage conditions are reported?', 'Voltage'),
                                   ('What gate conditions are reported?', 'gate')):
                ids = [row['id'] for row in rows if word.casefold() in row['text'].casefold()]
                if ids:
                    themes.append({'question': question, 'source_ids': ids})
            return json.dumps({'themes': themes, 'unassigned_source_ids': []})
        if request.task == 'Plan memory retrieval for a question':
            question = request.records[0]['question']
            views = [row for row in request.records if row.get('role') == 'view']
            match = next((row['id'] for row in views if row['question'] == question), None)
            return json.dumps({'reuse_view_id': match, 'related_view_ids': [], 'subquestions': [], 'reason': 'Equivalent theme only.'})
        if request.task == 'Review grounded memory contribution':
            return json.dumps({'grounding_status': 'accepted', 'coverage': 'packet_claims', 'global_coverage': 'not_assessed',
                'reason': 'Grounded fixture.', 'issues': [], 'context_requests': [], 'global_requests': []})
        if request.task.startswith('Review'):
            return json.dumps({'status': 'accepted', 'coverage': 'all_claims', 'reason': 'Grounded fixture.',
                'issues': [], 'context_requests': [], 'unknown_needs': []})
        semantic = [row.get('summary', row.get('text', '')) for row in request.records
                    if row.get('role') in ('verified_view', 'verified_contribution')]
        raw = [f"{row['text']} [source:{row['id']}]" for row in request.records if row.get('id') and row.get('text')]
        return '\n'.join([*semantic, *raw]) or 'No eligible source establishes an answer.'

    def run_builder(self, engine, snapshot, checkpoint, save):
        from prototypes.assertion_memory.background_overviews import run_background_scope
        return run_background_scope(engine, 's', snapshot, checkpoint, save)

    def test_system_bootstrap_publishes_shared_themes_and_reviewed_root(self):
        engine = MemoryEngine(self.store, model=self.model, verify_overviews=True)
        checkpoint, saved = {'_scheduler': {'sentinel': 'preserve'}}, []
        result = self.run_builder(engine, engine.capture_snapshot(), checkpoint, lambda value: saved.append(copy.deepcopy(value)))
        self.assertEqual(result['state'], 'idle')
        root = engine.get_version(result['version_id'])
        self.assertEqual(root.view_id, result['root_view_id'])
        self.assertEqual(root.overview_review['status'], 'accepted')
        self.assertEqual({row['id'] for row in root.evidence}, {'voltage', 'gate', 'interface'})
        children = [engine.get_version(key) for key in root.upstream.values()]
        self.assertEqual(len(children), 2)
        self.assertEqual(sum('interface' in {row['id'] for row in child.evidence} for child in children), 2)
        self.assertEqual(root.overview_review['assembly_coverage'], 'navigation_index')
        self.assertFalse(any(request.task == engine._spec(root.view_id).query for request in self.requests))
        self.assertTrue(all(child.overview_review['status'] == 'accepted' for child in children))
        self.assertEqual(saved[-1]['_scheduler'], {'sentinel': 'preserve'})
        self.assertNotIn('independent confirmation', json.dumps(saved[-1]))

    def test_no_reviewed_model_waits_without_creating_fake_overviews(self):
        for model, reviewed in ((None, True), (self.model, False)):
            with self.subTest(model=model, reviewed=reviewed):
                engine = MemoryEngine(self.store, model=model, verify_overviews=reviewed)
                result = self.run_builder(engine, engine.capture_snapshot(), {}, lambda value: None)
                self.assertEqual(result['state'], 'waiting_model')
                self.assertIsNone(result.get('version_id'))
                self.assertEqual(engine.list_views(), [])
        self.assertEqual(self.requests, [])

    def test_completed_frozen_checkpoint_returns_exact_receipt_without_republication(self):
        engine = MemoryEngine(self.store, model=self.model, verify_overviews=True)
        snapshot, checkpoint = engine.capture_snapshot(), {}
        first = self.run_builder(engine, snapshot, checkpoint, lambda value: None)
        calls = len(self.requests)
        checks = engine.view_checks(first['root_view_id'])
        second = self.run_builder(engine, snapshot, checkpoint, lambda value: None)
        self.assertEqual(first, second)
        self.assertEqual(len(self.requests), calls)
        self.assertEqual(engine.view_checks(first['root_view_id']), checks)

    def test_cooperative_yield_resumes_exact_calls_without_source_body_checkpoints(self):
        class Yield(BaseException):
            pass
        cache = {}
        def cooperative(request):
            key = (format_prompt(request), request.cache_context)
            if key in cache:
                return cache[key]
            cache[key] = self.model(request)
            raise Yield()
        engine = MemoryEngine(self.store, model=cooperative, model_key='stable-provider', verify_overviews=True)
        snapshot, checkpoint, completed = engine.capture_snapshot(), {'_scheduler': {'sentinel': 1}}, None
        saved = []
        for _ in range(30):
            try:
                completed = self.run_builder(engine, snapshot, checkpoint, lambda value: saved.append(copy.deepcopy(value)))
                break
            except Yield:
                checkpoint = copy.deepcopy(saved[-1])
        self.assertIsNotNone(completed)
        self.assertEqual(len(self.requests), len(cache))
        self.assertEqual(completed['state'], 'idle')
        self.assertNotIn('Voltage selection requires independent confirmation.', json.dumps(checkpoint))
        self.assertEqual(checkpoint['_scheduler'], {'sentinel': 1})

    def test_source_refresh_defers_managed_graph_but_keeps_ordinary_views(self):
        from prototypes.assertion_memory.engine import ViewSpec
        from prototypes.assertion_memory.background_overviews import managed_view_ids
        engine = MemoryEngine(self.store, model=self.model, verify_overviews=True)
        result = self.run_builder(engine, engine.capture_snapshot(), {}, lambda value: None)
        engine.define_view(ViewSpec('manual', 'Manual', 'Voltage user slice', scopes=('s',), root_ids=('voltage',)))
        engine.define_view(ViewSpec('dependent', 'Dependent', 'User semantic slice', scopes=('s',), depends_on=(result['theme_ids'][0],)))
        previous = engine.build_view('dependent')
        managed = managed_view_ids(engine)
        self.assertIn(result['root_view_id'], managed)
        self.assertTrue(set(result['theme_ids']) <= managed)
        self.assertNotIn('manual', managed)
        self.store.append_assertion(assertion_id='revision', text='Voltage requires a second independent confirmation.',
            actor='Cy', source='fixture://revision', episode='three', scope='s', recorded_at='2026-01-02')
        self.store.append_edge(edge_id='revision', from_id='revision', to_id='voltage', kind='exception_to',
                               recorded_at='2026-01-02', metadata={'condition': 'a second independent confirmation'})
        self.requests.clear()
        refreshed = engine.refresh_from('revision', exclude_view_ids=managed)
        self.assertEqual([view.view_id for view in refreshed], ['manual'])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(engine.inspect_view('dependent')['current_id'], previous.id)
        self.assertEqual(engine.inspect_view('dependent')['status'], 'stale')
        self.assertNotEqual(engine.build_view('dependent').id, previous.id, 'explicit foreground builds remain available')

    def test_multiple_discovery_batches_resume_without_rehashing_the_corpus(self):
        import sys
        class Yield(BaseException):
            pass
        class BeforeWriter(BaseException):
            pass
        for index in range(5):
            self.store.append_assertion(assertion_id=f'large-{index}', text='Voltage body CANONICAL_BODY ' * 500,
                actor='Ada', source=f'fixture://large-{index}', episode='one', scope='s', recorded_at='2026-01-01')
        cache, saved, preparations = {}, [], []
        def cooperative(request):
            if request.task not in ('Discover reusable memory themes', 'Plan memory retrieval for a question'):
                raise BeforeWriter()
            key = (format_prompt(request), request.cache_context)
            if key not in cache:
                cache[key] = self.model(request)
                raise Yield()
            return cache[key]
        def profile(frame, event, arg):
            if (event == 'call' and frame.f_code.co_name == 'corpus_state' and frame.f_back is not None
                    and frame.f_back.f_code.co_filename.endswith('/discovery_progress.py')):
                preparations.append(1)
        engine = MemoryEngine(self.store, model=cooperative, model_key='stable', verify_overviews=True)
        snapshot, checkpoint = engine.capture_snapshot(), {}
        old_profile = sys.getprofile()
        try:
            sys.setprofile(profile)
            for _ in range(40):
                try:
                    self.run_builder(engine, snapshot, checkpoint, lambda value: saved.append(copy.deepcopy(value)))
                except Yield:
                    checkpoint = copy.deepcopy(saved[-1])
                    continue
                except BeforeWriter:
                    break
                self.fail('writer boundary must be reached after discovery')
        finally:
            sys.setprofile(old_profile)
        self.assertEqual(len(preparations), 1)
        discovery = [request for request in self.requests if request.task == 'Discover reusable memory themes']
        self.assertGreater(len(discovery), 1)
        self.assertEqual(sum(len([row for row in request.records if row.get('role') == 'source']) for request in discovery), 8)
        self.assertTrue(all('CANONICAL_BODY' not in json.dumps(value) for value in saved))

    def test_scope_without_eligible_canonical_sources_does_not_generate_an_overview(self):
        engine = MemoryEngine(self.store, model=self.model, verify_overviews=True)
        snapshot = engine.capture_snapshot()
        from prototypes.assertion_memory.background_overviews import run_background_scope
        result = run_background_scope(engine, 'empty-scope', snapshot, {}, lambda value: None)
        self.assertEqual(result['state'], 'idle')
        self.assertTrue(result['empty_scope'])
        self.assertIsNone(result['root_view_id'])
        self.assertEqual(engine.list_views(), [])
        self.assertEqual(self.requests, [])

    def test_discovery_failure_retains_proof_and_retry_does_not_promote_failed_summary(self):
        from prototypes.assertion_memory.model import ModelError
        fail = [True]
        def model(request):
            if request.task == 'Discover reusable memory themes' and fail[0]:
                raise ModelError('scope discovery unavailable')
            return self.model(request)
        engine = MemoryEngine(self.store, model=model, model_key='stable', verify_overviews=True)
        snapshot, checkpoint = engine.capture_snapshot(), {}
        with self.assertRaisesRegex(ModelError, 'scope discovery unavailable') as caught:
            self.run_builder(engine, snapshot, checkpoint, lambda value: None)
        proof = caught.exception.failure_version
        self.assertEqual(proof.summary, '')
        self.assertEqual({row['id'] for row in proof.evidence}, {'voltage', 'gate', 'interface'})
        self.assertEqual(proof.overview_review['status'], 'unresolved')
        self.assertIsNone(engine.inspect_view(proof.view_id)['current_id'])
        self.assertEqual(checkpoint['overview']['failure']['version_id'], proof.id)
        fail[0] = False
        result = self.run_builder(engine, snapshot, checkpoint, lambda value: None)
        self.assertEqual(result['state'], 'idle')
        self.assertNotEqual(result['version_id'], proof.id)
        self.assertEqual(engine.get_version(proof.id), proof)
