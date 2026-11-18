"""Source facets never become semantic dependencies or unconditional cache anchors."""
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import Candidate
from prototypes.assertion_memory.storage import MemoryStore


class ExactRanker:
    def rank(self, query, candidates, *, limit):
        return [Candidate(key, 1, 'exact fixture') for key, text in candidates.items()
                if query.lower() in text.lower()][:limit]


class V8RoutesTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)

    def add(self, key, text):
        self.store.append_assertion(assertion_id=key, text=text, actor='fixture',
            source='fixture://' + key, episode='fixture', scope='s', recorded_at='2026-01-01')

    def test_facets_retrieve_without_materializing_child_versions(self):
        self.add('local', 'Root local condition')
        self.add('facet', 'Remote qualification')
        requests = []
        def model(request):
            requests.append(request)
            if request.task == 'Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id': None, 'related_view_ids': [],
                    'subquestions': [{'question': 'Remote qualification', 'purpose': 'domain_evidence'}],
                    'reason': 'A source facet only.'})
            return 'Qualified [local] [facet].'
        engine = MemoryEngine(self.store, model=model, candidate_search=ExactRanker())
        route = engine.prepare_question('Root local condition', scopes=('s',))
        spec = engine._spec(route['view_id'])
        self.assertEqual(spec.depends_on, ())
        version = engine.build_view(spec.id)
        self.assertEqual({row['id'] for row in version.evidence}, {'local', 'facet'})
        with self.store.transaction() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM memory_view_versions').fetchone()[0], 1)
        self.assertEqual(len(requests), 2)

    def test_source_index_reranks_cached_history_for_current_question(self):
        self.add('wanted', 'Delivery release qualification')
        self.add('noise', 'Old unrelated financial history')
        engine = MemoryEngine(self.store, candidate_search=ExactRanker())
        engine.define_view(ViewSpec('index', 'Index', 'Delivery release qualification history index', scopes=('s',), root_ids=('noise',)))
        engine.build_view('index')
        def planner(request):
            if request.task == 'Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id': None, 'related_view_ids': ['index'],
                    'subquestions': [], 'reason': 'Index offers candidates only.'})
            return 'Delivery [wanted].'
        engine.model = planner
        route = engine.prepare_question('Delivery release qualification', scopes=('s',))
        version = engine.build_view(route['view_id'])
        self.assertEqual({row['id'] for row in version.evidence}, {'wanted'})

    def test_manual_dependency_failure_still_blocks_parent(self):
        from prototypes.assertion_memory.model import ModelError
        self.add('fact', 'Child fact')
        engine = MemoryEngine(self.store, model=lambda request: (_ for _ in ()).throw(ModelError('blocked child')))
        engine.define_view(ViewSpec('child', 'Child', 'Child fact', root_ids=('fact',)))
        engine.define_view(ViewSpec('parent', 'Parent', 'Parent', depends_on=('child',)))
        with self.assertRaisesRegex(ModelError, 'blocked child'):
            engine.build_view('parent')
        self.assertIsNone(engine.inspect_view('parent')['current_id'])

    def test_absent_optional_index_anchor_is_not_a_missing_answer_prerequisite(self):
        self.add('wanted', 'Delivery release qualification')
        engine = MemoryEngine(self.store, candidate_search=ExactRanker())
        engine.define_view(ViewSpec('index', 'Index', 'Delivery release qualification history', scopes=('s',), root_ids=('absent-index-source',)))
        def planner(request):
            if request.task == 'Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id': None, 'related_view_ids': ['index'],
                    'subquestions': [], 'reason': 'Optional related source candidates.'})
            return 'Delivery [wanted].'
        engine.model = planner
        route = engine.prepare_question('Delivery release qualification', scopes=('s',))
        version = engine.build_view(route['view_id'])
        self.assertEqual(version.missing_roots, [])
        lineage = engine.latest_check(version)['freshness']['retrieval_dependencies']
        self.assertEqual(lineage[0]['missing_roots'], ['absent-index-source'])

    def test_four_narrow_facets_have_one_root_writer_review_among_nine_thousand_rows(self):
        for index in range(8994):
            self.add(f'noise-{index:05}', 'Unrelated botanical inventory ' + str(index))
        self.add('root', 'Narrow local question')
        for index in range(4):
            self.add(f'facet-{index}', f'Qualification facet {index}')
        self.add('remote', 'Independent registry gate lacking all query keywords')
        self.store.append_edge(edge_id='required', from_id='facet-0', to_id='remote', kind='depends_on',
            recorded_at='2026-01-02', metadata={'condition': 'Only after the independent registry confirmation.'})
        requests = []
        def model(request):
            requests.append(request)
            if request.task == 'Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id': None, 'related_view_ids': [],
                    'subquestions': [{'question': f'Qualification facet {index}', 'purpose': 'domain_evidence'}
                                     for index in range(4)], 'reason': 'Four source facets, one answer.'})
            if request.task.startswith('Review'):
                return json.dumps({'status': 'accepted', 'coverage': 'all_claims', 'reason': 'All exact qualifications retained.',
                    'issues': [], 'context_requests': [], 'unknown_needs': []})
            return 'Independent registry confirmation is required [root, facet-0, facet-1, facet-2, facet-3, remote].'
        engine = MemoryEngine(self.store, model=model, candidate_search=ExactRanker(), verify_overviews=True)
        route = engine.prepare_question('Narrow local question', scopes=('s',))
        version = engine.build_view(route['view_id'])
        self.assertEqual(len(requests), 3, 'only the planner and one root writer/reviewer pair are needed')
        self.assertEqual({row['id'] for row in version.evidence}, {'root', 'remote', 'facet-0', 'facet-1', 'facet-2', 'facet-3'})
        self.assertEqual(version.upstream, {})
        self.assertEqual(len(engine.view_history(version.view_id)), 1)
        self.assertEqual(len(engine.list_views()), 1)
        self.assertEqual([row['id'] for row in version.edges], ['required'])
