"""Semantic dependencies contribute accepted prose and exact immutable lineage."""
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import format_prompt
from prototypes.assertion_memory.storage import MemoryStore


class SemanticViewsTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.store.append_assertion(assertion_id='a', text='A signed permit is required before release.', actor='authority',
            source='fixture://a', episode='fixture', scope='s', recorded_at='2026-01-01', valid_at='2026-01-01')

    def test_accepted_summary_is_consumed_once_with_version_and_source_lineage(self):
        requests = []
        def model(request):
            requests.append(request)
            if request.task.startswith('Review'):
                return json.dumps({'status': 'accepted', 'coverage': 'all_claims', 'reason': 'Grounded.',
                    'issues': [], 'context_requests': [], 'unknown_needs': []})
            if any(row.get('role') == 'verified_view' for row in request.records):
                return 'Parent consumed the accepted branch: signed permit required [a].'
            return 'Accepted child interpretation: signed permit required before release [a].'
        engine = MemoryEngine(self.store, model=model, verify_overviews=True)
        engine.define_view(ViewSpec('child', 'Permit', 'What release authorization is required?', scopes=('s',), root_ids=('a',)))
        engine.define_view(ViewSpec('parent', 'Release', 'What are the release interfaces?', scopes=('s',), depends_on=('child',)))
        version = engine.build_view('parent')
        child = engine.get_version(version.upstream['child'])
        parent_writers = [r for r in requests if r.task == 'What are the release interfaces?']
        self.assertTrue(parent_writers)
        contribution = next(row for row in parent_writers[0].records if row.get('role') == 'verified_view')
        self.assertEqual(contribution['summary'], child.summary)
        self.assertEqual(contribution['version_id'], child.id)
        self.assertEqual(contribution['source_ids'], ['a'])
        self.assertIsInstance(contribution['check_id'], int)
        self.assertEqual(format_prompt(parent_writers[0]).count(child.summary), 1)
        self.assertFalse(any(row.get('id') == 'a' and row.get('text') for row in parent_writers[0].records),
                         'the child proof pool must not be replayed beside its accepted summary')
        self.assertIn('Parent consumed', version.summary)
        self.assertEqual({row['id'] for row in version.evidence}, {'a'})
        exported = engine.model_requests(version)
        self.assertEqual([format_prompt(item) for item in exported],
                         [format_prompt(item) for item in parent_writers])

    def test_future_child_version_cannot_supply_historical_parent(self):
        engine = MemoryEngine(self.store)
        engine.define_view(ViewSpec('child', 'Permit', 'Permit', scopes=('s',), root_ids=('a',)))
        engine.define_view(ViewSpec('parent', 'Release', 'Release', scopes=('s',), depends_on=('child',)))
        current = engine.build_view('child')
        historical = engine.build_view('parent', valid_as_of='2025-01-01', recorded_as_of='2025-01-01')
        self.assertNotEqual(historical.upstream['child'], current.id)
        self.assertEqual(historical.evidence, [])


class AutomaticSemanticLifecycleTest(unittest.TestCase):
    def test_discovered_themes_materialize_reuse_and_refresh_without_hindsight(self):
        import re
        from prototypes.assertion_memory.question_routes import get_history
        store = MemoryStore(':memory:')
        self.addCleanup(store.close)
        calls, visible_summaries = [], []
        def planner(**changes):
            return json.dumps({'reuse_view_id': None, 'related_view_ids': [], 'subquestions': [],
                               'reason': 'Explicit coverage and semantic reuse.', **changes})
        def model(request):
            calls.append(request)
            if request.task == 'Plan memory retrieval for a question':
                question = request.records[0]['question']
                views = [row for row in request.records if row.get('role') == 'view']
                if question == 'Manager whole picture':
                    return planner(discover_topics=True)
                match = next((row['id'] for row in views if row['question'] == question), None)
                return planner(reuse_view_id=match) if match else planner(
                    depends_on=[row['id'] for row in views if row['id'].startswith('theme-')][:3])
            if request.task == 'Discover reusable memory themes':
                rows = [row for row in request.records if row.get('role') == 'source']
                return json.dumps({'themes': [{'question': f'What does theme {number} require?',
                    'source_ids': [row['id'] for row in rows if row['text'].startswith(f'Theme {number} ')]}
                    for number in range(3) if any(row['text'].startswith(f'Theme {number} ') for row in rows)],
                    'unassigned_source_ids': []})
            if request.task == 'Review thematic source membership':
                number = next(index for index in range(3) if f'theme {index} ' in request.records[0]['question'])
                selected = [row['id'] for row in request.records if row.get('role') == 'candidate'
                            and row['text'].startswith(f'Theme {number} ')]
                return json.dumps({'source_ids': selected, 'unresolved_source_ids': [],
                                   'reason': 'Only matching independently scoped fixture theme sources belong.'})
            if request.task == 'Review grounded memory contribution':
                return json.dumps({'grounding_status': 'accepted', 'coverage': 'packet_claims',
                    'global_coverage': 'not_assessed', 'reason': 'Grounded packet.',
                    'issues': [], 'context_requests': [], 'global_requests': []})
            if request.task.startswith('Review'):
                return json.dumps({'status': 'accepted', 'coverage': 'all_claims', 'reason': 'Grounded.',
                    'issues': [], 'context_requests': [], 'unknown_needs': []})
            semantic = [row.get('summary', row.get('text', '')) for row in request.records
                        if row.get('role') in ('verified_view', 'verified_contribution')]
            visible_summaries.extend(semantic)
            raw = [row for row in request.records if row.get('id') and row.get('text')]
            clauses = [f"{token} [{row['id']}]" for row in raw
                       for token in re.findall(r'condition_[A-Z]+', row['text'])]
            return '\n'.join([*semantic, *clauses]) or 'Bounded source context [' + raw[0]['id'] + '].'
        for index in range(9000):
            text = f'Theme {index % 3} background {index}.'
            if index < 3:
                text += ' condition_' + ('ALPHA', 'BETA', 'GAMMA')[index]
            store.append_assertion(assertion_id=f's-{index:03}', text=text, actor=f'author-{index % 3}',
                source=f'fixture://{index}', episode='fixture', scope='s', recorded_at='2026-01-01', valid_at='2026-01-01')
        store.append_edge(edge_id='interface', from_id='s-000', to_id='s-001', kind='depends_on',
                          recorded_at='2026-01-01', metadata={'condition': 'The BETA condition gates ALPHA.'})
        engine = MemoryEngine(store, model=model, verify_overviews=True)
        snapshot = engine.capture_snapshot()
        route = engine.prepare_question('Manager whole picture', scopes=('s',), _snapshot=snapshot)
        first = engine.build_view(route['view_id'], _snapshot=snapshot)
        self.assertEqual(len(first.upstream), 3)
        self.assertEqual(len(first.evidence), 9000)
        self.assertEqual(len({row['id'] for row in first.evidence}), 9000)
        for token in ('condition_ALPHA', 'condition_BETA', 'condition_GAMMA'):
            self.assertIn(token, first.summary)
        self.assertIn('interface', {row['id'] for row in first.edges})
        self.assertTrue(all(engine.get_version(key).overview_review['status'] == 'accepted' for key in first.upstream.values()))
        self.assertTrue(visible_summaries)
        frozen = engine.get_version(first.id)
        before = len(calls)
        self.assertEqual(engine.build_view(first.view_id).id, first.id)
        self.assertEqual(len(calls), before)
        related = engine.prepare_question('Manager theme interface conditions', scopes=('s',))
        second = engine.build_view(related['view_id'])
        self.assertEqual(set(second.upstream.values()), set(first.upstream.values()))
        before_refresh = len(calls)
        store.append_assertion(assertion_id='revision', text='Theme 0 exception condition_DELTA.', actor='authority',
            source='fixture://revision', episode='fixture', scope='s', recorded_at='2026-01-02', valid_at='2026-01-02')
        store.append_edge(edge_id='revision-edge', from_id='revision', to_id='s-000', kind='exception_to',
                          recorded_at='2026-01-02', metadata={'condition': 'Only after condition_DELTA.'})
        refreshed = engine.prepare_question('Manager whole picture', scopes=('s',))
        updated = engine.build_view(refreshed['view_id'])
        self.assertIn('condition_DELTA', updated.summary)
        classified = {row['id'] for request in calls[before_refresh:] if request.task == 'Discover reusable memory themes'
                      for row in request.records if row.get('role') == 'source'}
        self.assertEqual(classified, {'revision', 's-000'})
        self.assertEqual(len(set(updated.upstream.values()) & set(first.upstream.values())), 2)
        self.assertNotEqual(updated.id, first.id)
        self.assertEqual(engine.get_version(first.id), frozen)
        self.assertNotIn('revision', {row['id'] for row in frozen.evidence})
        self.assertEqual(len(get_history(engine, 'Manager whole picture', scopes=('s',))), 2)
        self.assertTrue(all(len(format_prompt(request)) <= 60000 for request in calls))
        self.assertEqual(len(updated.evidence), 9001)
