"""Explicit broad coverage discovers reusable themes from every frozen source batch."""
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import ModelError, format_prompt
from prototypes.assertion_memory.storage import MemoryStore


def plan(**changes):
    return json.dumps({'reuse_view_id': None, 'related_view_ids': [], 'subquestions': [],
                       'reason': 'Explicit corpus-wide intent.', **changes})


class TopicDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.requests = []
        self.scanned = set()

    def add(self, index):
        key = f'source-{index:05}'
        self.store.append_assertion(assertion_id=key, text=f'Theme {index % 3} qualification {index}.',
            actor=f'contributor-{index % 3}', source='fixture://' + key, episode='fixture', scope='s',
            recorded_at='2026-01-01', valid_at='2026-01-01')

    def model(self, request):
        self.requests.append(request)
        self.assertLessEqual(len(format_prompt(request)), 60000)
        if request.task == 'Discover reusable memory themes':
            sources = [row for row in request.records if row.get('role') == 'source']
            self.scanned.update(row['id'] for row in sources)
            themes = []
            for number in range(3):
                ids = [row['id'] for row in sources if row['text'].startswith(f'Theme {number} ')]
                if ids:
                    themes.append({'question': f'What does theme {number} require?', 'source_ids': ids})
            return json.dumps({'themes': themes, 'unassigned_source_ids': []})
        if request.task == 'Review thematic source membership':
            question = request.records[0]['question']
            number = next(index for index in range(3) if f'theme {index} ' in question)
            selected = [row['id'] for row in request.records if row.get('role') == 'candidate'
                        and row['text'].startswith(f'Theme {number} ')]
            return json.dumps({'source_ids': selected, 'unresolved_source_ids': [],
                               'reason': 'Only the independently scoped matching fixture theme belongs.'})
        if request.task == 'Plan memory retrieval for a question':
            question = request.records[0]['question']
            candidates = [row for row in request.records if row.get('role') == 'view']
            if question == 'Manager whole picture':
                return plan(discover_topics=True)
            equivalent = next((row['id'] for row in candidates if row['question'] == question), None)
            return plan(reuse_view_id=equivalent)
        self.fail('Planning must not write semantic summaries')

    def test_unqueried_nine_thousand_source_corpus_gets_real_thematic_definitions(self):
        for index in range(9000):
            self.add(index)
        engine = MemoryEngine(self.store, model=self.model)
        route = engine.prepare_question('Manager whole picture', scopes=('s',))
        self.assertTrue(self.scanned == {f'source-{i:05}' for i in range(9000)}, 'all 9000 sources must be scanned')
        spec = engine._spec(route['view_id'])
        children = [engine._spec(key) for key in spec.depends_on]
        self.assertEqual(len(children), 3, 'batch boundaries must not become semantic branches')
        self.assertEqual({key for child in children for key in child.root_ids}, self.scanned)
        self.assertEqual(route['plan']['discovery']['unassigned_source_ids'], [])
        self.assertEqual(route['plan']['discovery']['source_count'], 9000)
        first_history = engine.question_history('Manager whole picture', scopes=('s',))
        before = len(self.requests)
        self.assertEqual(engine.prepare_question('Manager whole picture', scopes=('s',)), route)
        self.assertEqual(len(self.requests), before)
        self.scanned.clear()
        self.add(9000)
        updated = engine.prepare_question('Manager whole picture', scopes=('s',))
        self.assertNotEqual(updated['view_id'], route['view_id'])
        self.assertTrue(self.scanned == {'source-09000'}, 'incremental discovery must not replay the previous 9000 source bodies')
        self.assertEqual(engine.question_history('Manager whole picture', scopes=('s',))[0], first_history[0])
        self.assertEqual({key for child in children for key in child.root_ids}, {f'source-{i:05}' for i in range(9000)})

    def test_discovery_flag_is_strict_and_zero_dependencies_has_no_hidden_meaning(self):
        self.add(0)
        for value in ('true', 1, [], None):
            with self.subTest(value=value):
                engine = MemoryEngine(self.store, model=lambda request: plan(discover_topics=value))
                with self.assertRaises(ModelError):
                    engine.prepare_question('Invalid ' + str(value), scopes=('s',))
        requests = []
        engine = MemoryEngine(self.store, model=lambda request: requests.append(request) or plan(depends_on=[]))
        route = engine.prepare_question('Narrow condition', scopes=('s',))
        self.assertEqual(engine._spec(route['view_id']).depends_on, ())
        self.assertEqual(len(requests), 1)
        self.assertFalse(route['plan']['discover_topics'])

    def test_discovery_cannot_assign_sources_outside_its_batch(self):
        self.add(0)
        def invalid(request):
            if request.task == 'Discover reusable memory themes':
                return json.dumps({'themes': [{'question': 'Private question', 'source_ids': ['outside']}],
                                   'unassigned_source_ids': []})
            return plan(discover_topics=True)
        engine = MemoryEngine(self.store, model=invalid)
        with self.assertRaises(ModelError):
            engine.prepare_question('Manager whole picture', scopes=('s',))
        self.assertFalse(any(view.root_ids for view in engine.list_views()))

    def test_large_thematic_catalog_builds_real_reusable_grouping_definitions(self):
        for index in range(20):
            self.store.append_assertion(assertion_id=f'object-{index:02}',
                text=f'Object {index} has independently scoped authorization requirements. ' + 'Supporting context. ' * 650,
                actor='contributor', source=f'fixture://{index}', episode='fixture', scope='s', recorded_at='2026-01-01')
        grouping_calls = []
        def model(request):
            self.assertLessEqual(len(format_prompt(request)), 60000)
            if request.task == 'Discover reusable memory themes':
                rows = [row for row in request.records if row.get('role') == 'source']
                return json.dumps({'themes': [{'question': 'What authorization applies to ' + row['id'] + '? ' +
                    ('Preserve the independently scoped authority and interface purpose. ' * 20),
                    'source_ids': [row['id']]} for row in rows], 'unassigned_source_ids': []})
            if request.task == 'Plan reusable memory theme hierarchy':
                grouping_calls.append(request)
                children = [row['id'] for row in request.records if row.get('role') == 'view']
                return json.dumps({'groups': [{'question': 'What shared authorization interfaces govern these objects?',
                                               'depends_on': children}]})
            if request.task == 'Review grounded memory contribution':
                return json.dumps({'grounding_status': 'accepted', 'coverage': 'packet_claims',
                    'global_coverage': 'not_assessed', 'reason': 'Grounded packet.',
                    'issues': [], 'context_requests': [], 'global_requests': []})
            if request.task.startswith('Review'):
                return json.dumps({'status': 'accepted', 'coverage': 'all_claims', 'reason': 'Grounded.',
                    'issues': [], 'context_requests': [], 'unknown_needs': []})
            if request.task != 'Plan memory retrieval for a question':
                semantic = [row.get('summary', row.get('text', '')) for row in request.records
                            if row.get('role') in ('verified_view', 'verified_contribution')]
                raw = [row['id'] for row in request.records if row.get('id') and row.get('text')]
                return '\n'.join(semantic) if semantic else 'Scoped authority [' + raw[0] + '].'
            question = request.records[0]['question']
            candidate = next((row['id'] for row in request.records if row.get('role') == 'view' and row['question'] == question), None)
            return plan(reuse_view_id=candidate, discover_topics=question == 'Manager whole picture')
        engine = MemoryEngine(self.store, model=model, verify_overviews=True)
        route = engine.prepare_question('Manager whole picture', scopes=('s',))
        root = engine._spec(route['view_id'])
        self.assertTrue(grouping_calls, 'large thematic catalogs need deliberate semantic grouping')
        self.assertEqual(len(root.depends_on), 1)
        group = engine._spec(root.depends_on[0])
        self.assertEqual(group.query, 'What shared authorization interfaces govern these objects?')
        self.assertEqual(len(group.depends_on), 20)
        self.assertEqual({key for child in group.depends_on for key in engine._spec(child).root_ids},
                         {f'object-{index:02}' for index in range(20)})
        self.assertEqual(group.root_ids, ())
        version = engine.build_view(root.id)
        grouped = engine.get_version(version.upstream[group.id])
        self.assertEqual(grouped.overview_review['status'], 'accepted')
        self.assertEqual(len(grouped.upstream), 20)
        self.assertEqual(len(version.evidence), 20)
        self.assertEqual(version.summary, grouped.summary)

    def test_hierarchy_cannot_reference_unseen_children_or_discard_ungrouped_work(self):
        from prototypes.assertion_memory.engine import ViewSpec
        from prototypes.assertion_memory.topic_hierarchy import group_definitions
        engine = MemoryEngine(self.store)
        children = []
        for index in range(20):
            key = f'theme-{index}'
            engine.define_view(ViewSpec(key, key, 'Independent question ' + str(index) + ' detail' * 220, scopes=('s',)))
            children.append(key)
        parent = ViewSpec('parent', 'Parent', 'Whole picture', scopes=('s',))
        for response in ({'groups': [{'question': 'Injected parent', 'depends_on': ['parent']}]}, {'groups': []}):
            with self.subTest(response=response):
                engine.model = lambda request: json.dumps(response)
                with self.assertRaises(ModelError):
                    group_definitions(engine, parent, {'question': parent.query}, children)
                self.assertEqual({view.id for view in engine.list_views()}, set(children))

    def test_incremental_edge_change_reclassifies_only_affected_endpoints_and_versions_plan(self):
        for index in range(9):
            self.add(index)
        engine = MemoryEngine(self.store, model=self.model)
        first = engine.prepare_question('Manager whole picture', scopes=('s',))
        previous = engine.question_history('Manager whole picture', scopes=('s',))[0]
        old = {engine._spec(key).query: key for key in engine._spec(first['view_id']).depends_on}
        self.scanned.clear()
        self.store.append_edge(edge_id='interface', from_id='source-00000', to_id='source-00001', kind='depends_on',
                               recorded_at='2026-01-01', metadata={'condition': 'Only with shared confirmation.'})
        updated = engine.prepare_question('Manager whole picture', scopes=('s',))
        self.assertEqual(self.scanned, {'source-00000', 'source-00001'})
        self.assertEqual(updated['plan']['discovery']['source_count'], 9)
        self.assertNotEqual(updated['view_id'], first['view_id'], 'edge-only changes need immutable materialization plans')
        self.assertEqual({engine._spec(key).query: key for key in engine._spec(updated['view_id']).depends_on}, old)
        self.assertEqual(engine.question_history('Manager whole picture', scopes=('s',))[0], previous)

    def test_changed_discovery_pipeline_does_not_reuse_old_classification_proof(self):
        for index in range(9):
            self.add(index)
        first_engine = MemoryEngine(self.store, model=self.model, model_key='first-model')
        snapshot = first_engine.capture_snapshot()
        first = first_engine.prepare_question('Manager whole picture', scopes=('s',), _snapshot=snapshot)
        self.scanned.clear()
        replacement = MemoryEngine(self.store, model=self.model, model_key='replacement-model')
        updated = replacement.prepare_question('Manager whole picture', scopes=('s',), _snapshot=snapshot)
        self.assertEqual(len(self.scanned), 9)
        self.assertNotEqual(updated['view_id'], first['view_id'])
        self.assertEqual(updated['plan']['discovery']['retained_source_count'], 0)

    def test_historical_request_does_not_reuse_future_whole_corpus_membership(self):
        for index in range(9):
            self.add(index)
        engine = MemoryEngine(self.store, model=self.model)
        current = engine.prepare_question('Manager whole picture', scopes=('s',))
        self.scanned.clear()
        historical = engine.prepare_question('Manager whole picture', scopes=('s',),
                                             valid_as_of='2025-01-01', recorded_as_of='2025-01-01')
        self.assertNotEqual(historical['view_id'], current['view_id'])
        self.assertEqual(historical['plan']['discovery']['source_count'], 0)
        self.assertEqual(engine._spec(historical['view_id']).depends_on, ())
        self.assertEqual(self.scanned, set())

    def test_current_complete_membership_keeps_independent_themes_separate_with_full_closure(self):
        from prototypes.assertion_memory.model import Candidate, CandidateSearch
        for index in range(9):
            self.add(index)
        self.store.append_edge(edge_id='shared', from_id='source-00000', to_id='source-00001', kind='depends_on',
                               recorded_at='2026-01-01', metadata={'condition': 'Only with the independent confirmation.'})
        class NoisyRanking:
            def rank(self, query, candidates, *, limit):
                if 'material-late' in candidates:
                    return [Candidate('material-late', 1, 'fresh material candidate')]
                if 'source-00000' in candidates:
                    return [Candidate('source-00000', 1, 'deliberate retrieval distractor')]
                return CandidateSearch().rank(query, candidates, limit=limit)
        def model(request):
            if request.task in ('Plan memory retrieval for a question', 'Discover reusable memory themes'):
                return self.model(request)
            if request.task.startswith('Review'):
                return json.dumps({'status': 'accepted', 'coverage': 'all_claims', 'reason': 'Grounded.',
                    'issues': [], 'context_requests': [], 'unknown_needs': []})
            return 'Scoped finding ' + ' '.join('[' + row['id'] + ']' for row in request.records if row.get('id'))
        engine = MemoryEngine(self.store, model=model, candidate_search=NoisyRanking(), verify_overviews=True)
        route = engine.prepare_question('Manager whole picture', scopes=('s',))
        themes = {engine._spec(key).query: key for key in engine._spec(route['view_id']).depends_on}
        independent = engine.build_view(themes['What does theme 1 require?'])
        self.assertEqual({row['id'] for row in independent.evidence}, {'source-00001', 'source-00004', 'source-00007'})
        dependent = engine.build_view(themes['What does theme 0 require?'])
        self.assertEqual({row['id'] for row in dependent.evidence},
                         {'source-00000', 'source-00003', 'source-00006', 'source-00001'})
        self.assertEqual([row['id'] for row in dependent.edges], ['shared'])
        admission = independent.overview_review['selection_index']
        self.assertEqual(admission['method'], 'complete_thematic_membership')
        self.assertEqual(admission['route_view_id'], route['view_id'])
        self.assertEqual(admission['eligible_source_count'], 9)
        self.store.append_assertion(assertion_id='material-late', text='New material thematic condition.', actor='authority',
            source='fixture://late', episode='fixture', scope='s', recorded_at='2026-01-02')
        refreshed = engine.build_view(independent.view_id)
        self.assertIn('material-late', {row['id'] for row in refreshed.evidence})
        self.assertNotIn('selection_index', refreshed.overview_review, 'stale membership must fall back to fresh relevance selection')

    def test_unassigned_sources_remain_root_anchors_and_disable_complete_theme_admission(self):
        from prototypes.assertion_memory.model import Candidate, CandidateSearch
        for index in range(9):
            self.add(index)
        class PreferUnassigned:
            def rank(self, query, candidates, *, limit):
                return ([Candidate('source-00000', 1, 'unresolved membership')] if 'source-00000' in candidates
                        else CandidateSearch().rank(query, candidates, limit=limit))
        def model(request):
            if request.task == 'Discover reusable memory themes':
                value = json.loads(self.model(request))
                value['themes'][0]['source_ids'].remove('source-00000')
                value['unassigned_source_ids'] = ['source-00000']
                return json.dumps(value)
            if request.task == 'Plan memory retrieval for a question':
                return self.model(request)
            if request.task.startswith('Review'):
                return json.dumps({'status': 'accepted', 'coverage': 'all_claims', 'reason': 'Grounded.',
                    'issues': [], 'context_requests': [], 'unknown_needs': []})
            return 'Scoped findings ' + ' '.join('[' + row['id'] + ']' for row in request.records if row.get('id'))
        engine = MemoryEngine(self.store, model=model, candidate_search=PreferUnassigned(), verify_overviews=True)
        route = engine.prepare_question('Manager whole picture', scopes=('s',))
        root = engine._spec(route['view_id'])
        self.assertEqual(root.root_ids, ('source-00000',))
        child = next(key for key in root.depends_on if engine._spec(key).query == 'What does theme 1 require?')
        version = engine.build_view(child)
        self.assertIn('source-00000', {row['id'] for row in version.evidence})
        self.assertNotIn('selection_index', version.overview_review)
