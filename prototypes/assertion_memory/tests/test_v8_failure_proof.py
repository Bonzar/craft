"""Proof survives generation failures without promotion to an accepted answer."""
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.storage import MemoryStore


class FailureProofTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.add('a', 'Release requires a signed permit.')

    def add(self, key, text):
        self.store.append_assertion(assertion_id=key, text=text, actor='fixture', source='fixture://' + key,
            episode='proof', scope='s', recorded_at='2026-01-01')

    def test_acquired_proof_is_immutable_and_never_current_or_cache_answer(self):
        calls = []
        def model(request):
            calls.append(request)
            raise ModelError('writer failed')
        engine = MemoryEngine(self.store, model=model)
        engine.define_view(ViewSpec('v', 'Release', 'Release', scopes=('s',), root_ids=('a',)))
        snapshot = engine.capture_snapshot()
        with self.assertRaises(ModelError) as caught:
            engine.build_view('v', _snapshot=snapshot)
        failure = caught.exception.failure_version
        self.assertEqual(failure.summary, '')
        self.assertEqual([row['id'] for row in failure.evidence], ['a'])
        self.assertEqual(failure.overview_review['status'], 'unresolved')
        self.assertEqual(caught.exception.failure_check['query_snapshot'], snapshot.descriptor())
        self.assertIsNone(engine.inspect_view('v')['current_id'])
        saved = engine.get_version(failure.id)
        self.add('late', 'Release now has another prerequisite.')
        self.assertEqual(engine.get_version(failure.id), saved)
        with self.assertRaises(ModelError):
            engine.build_view('v', _snapshot=snapshot)
        self.assertEqual(len(calls), 2, 'failed proof must not satisfy cache')

    def test_failure_does_not_replace_existing_success_pointer(self):
        engine = MemoryEngine(self.store)
        engine.define_view(ViewSpec('v', 'Release', 'Release', scopes=('s',), root_ids=('a',)))
        accepted = engine.build_view('v')
        previous_check = engine.inspect_view('v')['current_check_id']
        engine.model = lambda request: (_ for _ in ()).throw(ModelError('downstream failure'))
        self.add('b', 'Release second condition.')
        with self.assertRaises(ModelError) as caught:
            engine.build_view('v')
        self.assertNotEqual(caught.exception.failure_version.id, accepted.id)
        self.assertEqual(engine.inspect_view('v')['current_id'], accepted.id)
        self.assertEqual(engine.inspect_view('v')['current_check_id'], previous_check)

    def test_first_planner_failure_has_deterministic_attempt_definition(self):
        engine = MemoryEngine(self.store, model=lambda request: (_ for _ in ()).throw(ModelError('planner failed')))
        with self.assertRaises(ModelError) as caught:
            engine.prepare_question('Release question', scopes=('s',))
        error = caught.exception
        self.assertTrue(error.failure_view_id.startswith('question-'))
        self.assertEqual(error.acquisition['phase'], 'planning')
        self.assertFalse(error.acquisition['selection_complete'])
        self.assertEqual(engine.question_history('Release question', scopes=('s',)), [])
        self.assertEqual(engine.get_version(error.failure_version.id).summary, '')

    def test_persistence_failure_keeps_acquired_inline_proof_without_fake_cursor(self):
        def model(request):
            self.store.close()
            return 'Permit required [a].'
        engine = MemoryEngine(self.store, model=model)
        engine.define_view(ViewSpec('v', 'Release', 'Release', scopes=('s',), root_ids=('a',)))
        with self.assertRaises(ModelError) as caught:
            engine.build_view('v')
        error = caught.exception
        self.assertIsNone(error.failure_version)
        self.assertIsNone(error.failure_check)
        self.assertIn('persistence_error', error.acquisition)
        self.assertEqual([row['id'] for row in error.acquisition['evidence']], ['a'])

    def test_evidence_acquired_by_reviewer_survives_next_writer_failure(self):
        import json
        self.add('b', 'Independent registry imposes an extra confirmation.')
        calls = []
        class NoSemanticHits:
            def rank(self, query, candidates, *, limit):
                return []
        def model(request):
            calls.append(request)
            if request.task.startswith('Review'):
                return json.dumps({'status': 'revise', 'coverage': 'all_claims', 'reason': 'Read the exact prerequisite.',
                    'issues': [], 'context_requests': [{'operation': 'read', 'source_ids': ['b']}], 'unknown_needs': []})
            if any(row.get('id') == 'b' for row in request.records):
                raise ModelError('writer after acquisition failed')
            return 'Permit needed [a].'
        engine = MemoryEngine(self.store, model=model, candidate_search=NoSemanticHits(), verify_overviews=True)
        engine.define_view(ViewSpec('v', 'Release', 'signed permit', scopes=('s',), root_ids=('a',)))
        with self.assertRaisesRegex(ModelError, 'writer after acquisition') as caught:
            engine.build_view('v')
        proof = caught.exception.failure_version
        self.assertEqual({row['id'] for row in proof.evidence}, {'a', 'b'})
        self.assertEqual(proof.summary, '')
        self.assertTrue(proof.overview_review['unresolved_obligations'])
        self.assertIsNone(engine.inspect_view('v')['current_id'])

    def test_failed_dependency_keeps_prior_sibling_lineage_and_parent_missing_roots(self):
        self.add('b', 'Second child condition.')
        def model(request):
            if request.task == 'Second child':
                raise ModelError('second child failed')
            return 'First child requires permit [a].'
        engine = MemoryEngine(self.store, model=model)
        engine.define_view(ViewSpec('first', 'First', 'First child', scopes=('s',), root_ids=('a',)))
        engine.define_view(ViewSpec('second', 'Second', 'Second child', scopes=('s',), root_ids=('b',)))
        engine.define_view(ViewSpec('parent', 'Parent', 'Parent', scopes=('s',), root_ids=('absent',), depends_on=('first', 'second')))
        with self.assertRaisesRegex(ModelError, 'second child failed') as caught:
            engine.build_view('parent')
        proof = caught.exception.failure_version
        self.assertEqual(proof.missing_roots, ['absent'])
        self.assertEqual(proof.upstream, {'first': engine.inspect_view('first')['current_id']})
        self.assertEqual({row['id'] for row in proof.evidence}, {'a', 'b'})
        self.assertEqual(proof.view_id, 'parent')

    def test_discovery_persistence_failure_keeps_every_scanned_source_inline(self):
        import json
        self.add('b', 'Another thematic qualification.')
        def model(request):
            if request.task == 'Discover reusable memory themes':
                ids = [row['id'] for row in request.records if row.get('role') == 'source']
                self.store.close()
                return json.dumps({'themes': [{'question': 'Independent release qualifications', 'source_ids': ids}],
                                   'unassigned_source_ids': []})
            return json.dumps({'reuse_view_id': None, 'related_view_ids': [], 'subquestions': [],
                               'reason': 'Corpus-wide coverage.', 'discover_topics': True})
        engine = MemoryEngine(self.store, model=model)
        with self.assertRaises(ModelError) as caught:
            engine.prepare_question('Whole picture', scopes=('s',))
        error = caught.exception
        self.assertIsNone(error.failure_version)
        self.assertIsNone(error.failure_check)
        self.assertEqual({row['id'] for row in error.acquisition['evidence']}, {'a', 'b'})
        self.assertIn('persistence_error', error.acquisition)

    def test_failed_planning_attempt_does_not_bypass_planner_on_retry(self):
        import json
        requests = []
        def model(request):
            requests.append(request)
            if len(requests) == 1:
                raise ModelError('initial planner failed')
            return json.dumps({'reuse_view_id': None, 'related_view_ids': [], 'subquestions': [],
                               'reason': 'Retry must actually plan.'})
        engine = MemoryEngine(self.store, model=model)
        with self.assertRaises(ModelError):
            engine.prepare_question('Release retry', scopes=('s',))
        result = engine.prepare_question('Release retry', scopes=('s',))
        self.assertEqual(result['plan']['method'], 'model')
        self.assertEqual(len(requests), 2)
