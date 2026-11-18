"""Composed reviews retain gaps, temporal intent and bounded DAG traversal."""
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import format_prompt
from prototypes.assertion_memory.storage import MemoryStore


class ViewCompositionTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)

    def test_parent_keeps_unavailable_required_evidence_of_its_dependency(self):
        engine = MemoryEngine(self.store)
        engine.define_view(ViewSpec('child', 'Checks', 'Was the handover approved?', root_ids=('approval',)))
        engine.define_view(ViewSpec('parent', 'Opening', 'Can the campus open?', depends_on=('child',)))
        child = engine.build_view('child')
        parent = engine.build_view('parent')
        self.assertEqual(child.missing_roots, ['approval'])
        self.assertEqual(parent.missing_roots, ['approval'])

    def test_shared_dependency_dag_builds_each_unique_view_once_per_read(self):
        engine = MemoryEngine(self.store)
        previous = ()
        for level in range(9):
            current = (f'{level}-a', f'{level}-b')
            for key in current:
                engine.define_view(ViewSpec(key, key, f'Question {key}', depends_on=previous))
            previous = current
        engine.define_view(ViewSpec('root', 'Root', 'Campus overview', depends_on=previous))
        first = engine.build_view('root')
        with patch.object(engine, '_build', wraps=engine._build) as build:
            repeated = engine.build_view('root')
        self.assertEqual(repeated.id, first.id)
        self.assertLessEqual(build.call_count, 19)

    def test_temporal_cutoffs_are_passed_to_model_and_reproducible_requests(self):
        self.store.append_assertion(assertion_id='old', text='North gate approval.', actor='owner',
            source='test://approval', episode='approval', valid_at='2026-11-01', recorded_at='2026-11-02')
        requests = []
        def model(request):
            requests.append(request)
            return 'Approval as known at the requested cutoff.'
        engine = MemoryEngine(self.store, model=model)
        engine.define_view(ViewSpec('history', 'History', 'What is the gate rule?', root_ids=('old',)))
        view = engine.build_view('history', valid_as_of='2026-11-09', recorded_as_of='2026-11-10')
        self.assertEqual(engine.model_requests(view), requests)
        prompt = format_prompt(requests[0])
        self.assertIn('2026-11-09', prompt)
        self.assertIn('2026-11-10', prompt)


if __name__ == '__main__':
    unittest.main()
