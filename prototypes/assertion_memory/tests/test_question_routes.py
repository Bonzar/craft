"""Questions create reusable retrieval routes without caller-supplied graph IDs."""
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.storage import MemoryStore


class QuestionRouteTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.calls = []

    def model(self, request):
        self.calls.append(request)
        question = request.records[0]['question']
        views = [record for record in request.records if record.get('role') == 'view']
        if 'together' in question:
            return json.dumps({'reuse_view_id': None, 'related_view_ids': [v['id'] for v in views],
                'subquestions': [{'question': 'Which integration checks remain open?', 'purpose': 'domain_evidence'}], 'reason': 'Cross-layer question.'})
        if 'paraphrase' in question:
            return json.dumps({'reuse_view_id': views[0]['id'], 'related_view_ids': [],
                               'subquestions': [], 'reason': 'Equivalent question in this scope.'})
        return json.dumps({'reuse_view_id': None, 'related_view_ids': [], 'subquestions': [],
                           'reason': 'One coherent retrieval question.'})

    def test_cross_topic_question_automatically_builds_linked_routes(self):
        engine = MemoryEngine(self.store, model=self.model)
        first = engine.prepare_question('How does UI choose the version?', scopes=('campus',))
        second = engine.prepare_question('How does BFF receive that version?', scopes=('campus',))
        combined = engine.prepare_question('How do UI and BFF work together?', scopes=('campus',))
        route = next(view for view in engine.list_views() if view.id == combined['view_id'])
        self.assertIn(first['view_id'], combined['plan']['related_view_ids'])
        self.assertIn(second['view_id'], combined['plan']['related_view_ids'])
        self.assertEqual(route.depends_on, ())
        self.assertEqual(combined['plan']['subquestions'], ['Which integration checks remain open?'])
        self.assertFalse(any(view.query == 'Which integration checks remain open?' for view in engine.list_views()))
        count = len(self.calls)
        self.assertEqual(engine.prepare_question(route.query, scopes=('campus',)), combined)
        self.assertEqual(len(self.calls), count)

    def test_equivalent_question_reuses_existing_route_and_survives_new_engine(self):
        engine = MemoryEngine(self.store, model=self.model)
        first = engine.prepare_question('UI version choice', scopes=('campus',))
        equivalent = engine.prepare_question('UI version choice paraphrase', scopes=('campus',))
        self.assertEqual(equivalent['view_id'], first['view_id'])
        reopened = MemoryEngine(self.store, model=lambda _: self.fail('persisted routing unexpectedly replanned'))
        self.assertEqual(reopened.prepare_question('UI version choice paraphrase', scopes=('campus',)), equivalent)

    def test_exact_existing_subquestion_reuses_its_branch_without_replanning(self):
        engine = MemoryEngine(self.store, model=self.model)
        child = ViewSpec('integration-theme', 'Integration', 'Which integration checks remain open?', scopes=('campus',))
        engine.define_view(child)
        engine.prepare_question('How do UI and BFF work together?', scopes=('campus',))
        calls = len(self.calls)
        prepared = engine.prepare_question(child.query, scopes=('campus',))
        self.assertEqual(prepared['view_id'], child.id)
        self.assertEqual(prepared['plan']['method'], 'exact')
        self.assertEqual(len(self.calls), calls)

    def test_standalone_service_uses_the_same_automatic_question_routes(self):
        from prototypes.assertion_memory.service import MemoryService
        service = MemoryService(self.store, MemoryEngine(self.store, model=self.model))
        first = service.query('UI version choice', scope='campus')
        second = service.query('BFF version reception', scope='campus')
        combined = service.query('How do UI and BFF work together?', scope='campus')
        self.assertEqual(combined['retrieval_plan']['method'], 'model')
        route = next(view for view in service.engine.list_views() if view.id == combined['view']['view_id'])
        self.assertIn(first['view']['view_id'], combined['retrieval_plan']['related_view_ids'])
        self.assertIn(second['view']['view_id'], combined['retrieval_plan']['related_view_ids'])
        self.assertEqual(route.depends_on, ())

    def test_planner_cannot_choose_unknown_or_other_scope_view(self):
        engine = MemoryEngine(self.store, model=self.model)
        private = engine.prepare_question('UI private version', scopes=('private',))
        def invalid(request):
            self.assertNotIn(private['view_id'], {r.get('id') for r in request.records})
            return json.dumps({'reuse_view_id': private['view_id'], 'related_view_ids': [],
                               'subquestions': [], 'reason': 'Invalid cross-scope reuse.'})
        engine.model = invalid
        before = engine.list_views()
        with self.assertRaises(ModelError) as caught:
            engine.prepare_question('UI public version', scopes=('public',))
        self.assertEqual([view for view in engine.list_views() if view.scopes == ('private',)], before)
        self.assertEqual(caught.exception.failure_version.evidence, [])
        self.assertIsNone(engine.inspect_view(caught.exception.failure_view_id)['current_id'])


if __name__ == '__main__':
    unittest.main()
