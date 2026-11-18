"""Fresh corpus checks reuse unchanged semantics and replay known material gaps."""
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import Candidate
from prototypes.assertion_memory.storage import MemoryStore


class Relevance:
    def rank(self, query, candidates, *, limit):
        wanted = 'b' if query == 'registry authorization' else 'a'
        return [Candidate(wanted, 1, 'fixture')] if wanted in candidates else []


class SemanticReuseTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.add('a', 'Root baseline report.')

    def add(self, key, text):
        self.store.append_assertion(assertion_id=key, text=text, actor='fixture', source='fixture://' + key,
            episode='fixture', scope='s', recorded_at='2026-01-01', valid_at='2026-01-01')

    def test_complete_freshness_recheck_stops_when_semantic_inputs_are_unchanged(self):
        requests = []
        def model(request):
            requests.append(request)
            if request.task.startswith('Review'):
                return json.dumps({'status': 'accepted', 'coverage': 'all_claims', 'reason': 'Grounded.',
                    'issues': [], 'context_requests': [], 'unknown_needs': []})
            return 'Baseline report [a].'
        engine = MemoryEngine(self.store, model=model, candidate_search=Relevance(), verify_overviews=True)
        engine.define_view(ViewSpec('v', 'Baseline', 'Root baseline', scopes=('s',), root_ids=('a',)))
        first = engine.build_view('v')
        first_check = engine.latest_check(first)
        self.add('unrelated', 'Unrelated botanical inventory.')
        second = engine.build_view('v')
        second_check = engine.latest_check(second)
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(requests), 2, 'fresh candidate/closure checks must stop unchanged semantic propagation')
        self.assertNotEqual(first_check['freshness']['covered_corpus_revision'], second_check['freshness']['covered_corpus_revision'])
        self.assertEqual(second_check['source_version'], self.store.version)
        self.assertFalse(second_check['freshness']['refresh_required'])
        self.assertTrue(second_check['overview_review']['selection_revalidation']['summary_reused'])

    def test_new_answer_to_previous_negative_selector_invalidates_summary_before_writer(self):
        requests = []
        unknown = 'The bounded registry search has not established confirmation'
        def model(request):
            requests.append(request)
            if request.task == 'Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id': None, 'related_view_ids': [], 'subquestions': [],
                    'reason': 'Independent reusable themes.', 'discover_topics': request.records[0]['question'] == 'Whole picture'})
            if request.task == 'Discover reusable memory themes':
                ids = [row['id'] for row in request.records if row.get('role') == 'source']
                return json.dumps({'themes': [{'question': question, 'source_ids': selected}
                    for question, selected in [('Root baseline', [key for key in ids if key == 'a']),
                        ('Other domain', [key for key in ids if key != 'a'])] if selected], 'unassigned_source_ids': []})
            if request.task == 'Review thematic source membership':
                is_root = request.records[0]['question'] == 'Root baseline'
                selected = [row['id'] for row in request.records if row.get('role') == 'candidate'
                            and (row['id'] == 'a') == is_root]
                return json.dumps({'source_ids': selected, 'unresolved_source_ids': [],
                                   'reason': 'Preserve the independently scoped fixture theme membership.'})
            found = any(row.get('id') == 'b' for row in request.records)
            if request.task.startswith('Review'):
                base = {'status': 'accepted', 'coverage': 'all_claims', 'reason': 'Faithful scoped answer.',
                        'issues': [], 'context_requests': [], 'unknown_needs': []}
                if found:
                    return json.dumps(base)
                searched = any(item['gap_id'] == 'registry' for row in request.records
                    if row.get('role') == 'retrieval_state' for item in row['obligations'])
                if not searched:
                    return json.dumps({**base, 'status': 'revise', 'context_requests': [
                        {'operation': 'search', 'query': 'registry authorization', 'limit': 1, 'gap_id': 'registry'}]})
                return json.dumps({**base, 'unknown_needs': [{'quote': unknown, 'gap_id': 'registry',
                    'basis': 'searched_unresolved', 'reason': 'Previous bounded lookup found no confirmation.'}]})
            return 'Registry confirmation is documented [b].' if found else unknown + ' [a].'
        engine = MemoryEngine(self.store, model=model, candidate_search=Relevance(), verify_overviews=True)
        self.add('c', 'Other domain background.')
        route = engine.prepare_question('Whole picture', scopes=('s',))
        view_id = next(key for key in engine._spec(route['view_id']).depends_on if engine._spec(key).query == 'Root baseline')
        first = engine.build_view(view_id)
        before = len(requests)
        self.assertIn(unknown, first.summary)
        self.add('b', 'Registry authorization was confirmed in the other domain.')
        engine.prepare_question('Whole picture', scopes=('s',))
        before = len(requests)
        second = engine.build_view(view_id)
        self.assertIn('confirmation is documented', second.summary)
        self.assertNotIn(unknown, second.summary)
        self.assertEqual(len(requests) - before, 2, 'replay known selectors before choosing whether to reuse or write')
        self.assertEqual({row['id'] for row in second.evidence}, {'a', 'b'})
        trace = second.overview_review['selection_revalidation']
        self.assertFalse(trace['summary_reused'])
        self.assertFalse(trace['material_acquisition_outcomes_unchanged'])
        self.assertEqual(trace['replayed_acquisition'][0]['selected_ids'], ['b'])
        self.assertEqual(second.overview_review['selection_index']['method'], 'complete_thematic_membership')
