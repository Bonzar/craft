"""Immutable routing plans under upgrades, interleaving and temporal requests."""
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError, format_prompt
from prototypes.assertion_memory.question_routes import prepare_question
from prototypes.assertion_memory.storage import MemoryStore


def plan(**overrides):
    if 'subquestions' in overrides:
        overrides['subquestions'] = [{'question': text, 'purpose': 'domain_evidence'} for text in overrides['subquestions']]
    return json.dumps({'reuse_view_id': None, 'related_view_ids': [], 'subquestions': [],
                       'reason': 'Bounded test plan.', **overrides})


class QuestionRoutesHardeningTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)

    def test_concurrent_child_and_planned_root_keep_both_immutable_definitions(self):
        engine = MemoryEngine(self.store)
        entered = False
        def model(request):
            nonlocal entered
            question = request.records[0]['question']
            if question == 'B' and not entered:
                entered = True
                prepare_question(engine, 'A', scopes=('s',))
                return plan(subquestions=['C'])
            return plan(subquestions=['B'] if question == 'A' else [])
        engine.model = model
        result = prepare_question(engine, 'B', scopes=('s',))
        views = {view.id: view for view in engine.list_views()}
        root = views[result['view_id']]
        self.assertEqual(root.depends_on, ())
        self.assertEqual(result['plan']['subquestions'], ['C'])
        a = next(view for view in views.values() if view.query == 'A')
        self.assertEqual(a.depends_on, ())
        self.assertNotEqual(a.id, root.id)
        self.assertEqual(engine.question_history('A', scopes=('s',))[0]['plan']['subquestions'], ['B'])

    def test_direct_route_upgrades_to_model_without_erasing_plan_history(self):
        from prototypes.assertion_memory.question_routes import get_history
        direct_engine = MemoryEngine(self.store)
        old = prepare_question(direct_engine, 'Combined question', scopes=('s',))
        before = {view.id: view for view in direct_engine.list_views()}
        calls = []
        engine = MemoryEngine(self.store, model=lambda request: calls.append(request) or plan(subquestions=['First question', 'Second question']))
        current = prepare_question(engine, 'Combined question', scopes=('s',))
        self.assertEqual(len(calls), 1)
        self.assertEqual(current['plan']['method'], 'model')
        self.assertNotEqual(current['view_id'], old['view_id'])
        views = {view.id: view for view in engine.list_views()}
        self.assertEqual(views[old['view_id']], before[old['view_id']])
        self.assertEqual(views[current['view_id']].depends_on, ())
        self.assertEqual(current['plan']['subquestions'], ['First question', 'Second question'])
        history = get_history(engine, 'Combined question', scopes=('s',))
        self.assertEqual([row['revision'] for row in history], [1, 2])
        self.assertEqual(history[0]['plan'], old['plan'])
        self.assertEqual(history[1]['plan'], current['plan'])
        again = prepare_question(MemoryEngine(self.store, model=lambda _: self.fail('model plan was replanned')),
                                 'Combined question', scopes=('s',))
        self.assertEqual(again, current)

    def test_upgrade_can_retain_old_flat_route_as_dependency_without_self_cycle(self):
        old = prepare_question(MemoryEngine(self.store), 'Shared question', scopes=('s',))
        engine = MemoryEngine(self.store, model=lambda request: plan(
            related_view_ids=[old['view_id']], subquestions=['Additional branch']))
        current = prepare_question(engine, 'Shared question', scopes=('s',))
        view = next(view for view in engine.list_views() if view.id == current['view_id'])
        self.assertNotEqual(view.id, old['view_id'])
        self.assertIn(old['view_id'], current['plan']['related_view_ids'])
        self.assertNotIn(old['view_id'], view.depends_on)
        self.assertEqual(view.depends_on, ())

    def test_failed_upgrade_keeps_current_direct_route_and_history(self):
        from prototypes.assertion_memory.question_routes import get_history
        engine = MemoryEngine(self.store)
        original = prepare_question(engine, 'Question', scopes=('s',))
        views = engine.list_views()
        bad = MemoryEngine(self.store, model=lambda request: plan(related_view_ids=['unknown']))
        with self.assertRaises(ModelError):
            prepare_question(bad, 'Question', scopes=('s',))
        self.assertEqual(engine.list_views(), views)
        self.assertEqual(prepare_question(engine, 'Question', scopes=('s',)), original)
        self.assertEqual(len(get_history(engine, 'Question', scopes=('s',))), 1)

    def test_concurrent_model_upgrade_publishes_only_one_winning_revision(self):
        from prototypes.assertion_memory.question_routes import get_history
        prepare_question(MemoryEngine(self.store), 'Question', scopes=('s',))
        winner = MemoryEngine(self.store, model=lambda request: plan(subquestions=['Winner child']))
        def delayed(request):
            prepare_question(winner, 'Question', scopes=('s',))
            return plan(subquestions=['Losing child'])
        engine = MemoryEngine(self.store, model=delayed)
        result = prepare_question(engine, 'Question', scopes=('s',))
        self.assertEqual(result['plan']['subquestions'], ['Winner child'])
        self.assertFalse(any(view.query == 'Losing child' for view in engine.list_views()))
        self.assertEqual(len(get_history(engine, 'Question', scopes=('s',))), 2)

    def test_temporal_cutoffs_are_normalized_in_planning_and_request_identity(self):
        from prototypes.assertion_memory.question_routes import get_history
        calls = []
        engine = MemoryEngine(self.store, model=lambda request: calls.append(request) or plan())
        result = prepare_question(engine, 'Historical question', scopes=('s',),
                                  valid_as_of='2026-11-09', recorded_as_of='2026-11-10T03:00:00+03:00')
        record = calls[0].records[0]
        self.assertEqual(record['valid_as_of'], '2026-11-09T00:00:00.000000+00:00')
        self.assertEqual(record['recorded_as_of'], '2026-11-10T00:00:00.000000+00:00')
        self.assertEqual(record['scopes'], ['s'])
        equivalent = prepare_question(engine, 'Historical question', scopes=('s',),
                                      valid_as_of='2026-11-09T00:00:00Z', recorded_as_of='2026-11-10')
        self.assertEqual(result, equivalent)
        self.assertEqual(len(calls), 1)
        current = prepare_question(engine, 'Historical question', scopes=('s',))
        self.assertNotEqual(current['plan']['request'], result['plan']['request'])
        self.assertEqual(len(get_history(engine, 'Historical question', scopes=('s',))), 1)
        self.assertEqual(len(get_history(engine, 'Historical question', scopes=('s',),
                                         valid_as_of='2026-11-09', recorded_as_of='2026-11-10')), 1)

    def test_oversized_candidate_title_does_not_block_bounded_exact_question(self):
        calls = []
        engine = MemoryEngine(self.store, model=lambda request: calls.append(request) or plan())
        engine.define_view(ViewSpec('old', 'Huge title ' * 10000, 'Shared question', scopes=('s',)))
        prepare_question(engine, 'Shared question details', scopes=('s',))
        self.assertLessEqual(len(format_prompt(calls[0])), 60000)
        candidate = next(row for row in calls[0].records if row.get('id') == 'old')
        self.assertEqual(candidate['question'], 'Shared question')
        self.assertNotIn('title', candidate)

    def test_oversized_candidate_query_is_explicitly_skipped_without_truncation(self):
        calls = []
        engine = MemoryEngine(self.store, model=lambda request: calls.append(request) or plan())
        large = 'Shared question ' * 5000
        engine.define_view(ViewSpec('huge', 'Title', large, scopes=('s',)))
        result = prepare_question(engine, 'Shared question details', scopes=('s',))
        self.assertNotIn('huge', {row.get('id') for row in calls[0].records})
        self.assertEqual(result['plan']['omitted_candidates'][0]['view_id'], 'huge')
        self.assertIn('budget', result['plan']['omitted_candidates'][0]['reason'])
        self.assertEqual(engine.list_views()[0].query, large)

    def test_candidate_budget_counts_escaping_and_record_overhead(self):
        calls = []
        engine = MemoryEngine(self.store, model=lambda request: calls.append(request) or plan())
        for index in range(3):
            engine.define_view(ViewSpec(f'escaped-{index}', 'Title',
                                        'Shared question ' + ('\n' * 7500) + str(index), scopes=('s',)))
        result = prepare_question(engine, 'Shared question details', scopes=('s',))
        candidates = [row for row in calls[0].records if row.get('role') == 'view']
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(result['plan']['omitted_candidates']), 2)
        self.assertLessEqual(len(json.dumps(candidates, ensure_ascii=False, indent=2)), 24000)
        self.assertIn('\n' * 7500, candidates[0]['question'])

    def test_ordinary_exact_subquestion_reuse_stays_cheap(self):
        calls = []
        engine = MemoryEngine(self.store, model=lambda request: calls.append(request) or plan(subquestions=['Child']))
        engine.define_view(ViewSpec('child-theme', 'Child', 'Child', scopes=('s',)))
        prepare_question(engine, 'Parent', scopes=('s',))
        before = len(calls)
        result = prepare_question(engine, 'Child', scopes=('s',))
        self.assertEqual(result['plan']['method'], 'exact')
        self.assertEqual(len(calls), before)

    def test_legacy_route_backfill_preserves_original_plan_with_unknown_creation_time(self):
        from prototypes.assertion_memory.question_routes import _key, get_history
        engine = MemoryEngine(self.store)
        key = _key('Legacy', ('s',))
        engine.define_view(ViewSpec(key, 'Legacy', 'Legacy', scopes=('s',)))
        original = {'reuse_view_id': None, 'related_view_ids': [], 'subquestions': [], 'reason': 'Legacy fallback', 'method': 'direct'}
        with self.store.transaction(write=True) as connection:
            connection.execute('CREATE TABLE memory_question_routes (request_id TEXT PRIMARY KEY, view_id TEXT NOT NULL REFERENCES memory_views(id), plan TEXT NOT NULL)')
            connection.execute('INSERT INTO memory_question_routes VALUES (?, ?, ?)', (key, key, json.dumps(original)))
        history = get_history(engine, 'Legacy', scopes=('s',))
        self.assertEqual(history[0]['plan'], original)
        self.assertIsNone(history[0]['recorded_at'])
        upgraded = prepare_question(MemoryEngine(self.store, model=lambda request: plan(subquestions=['New child'])),
                                    'Legacy', scopes=('s',))
        self.assertEqual(upgraded['plan']['method'], 'model')
        self.assertEqual(len(get_history(engine, 'Legacy', scopes=('s',))), 2)


if __name__ == '__main__':
    unittest.main()
