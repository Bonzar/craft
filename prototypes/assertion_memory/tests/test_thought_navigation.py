"""Legacy views route to bounded useful source packets without flattening them."""
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.model import CandidateSearch
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.tests.test_thought_engine import ACCEPT, ScriptedModel, draft


class ViewsFirstSearch(CandidateSearch):
    def rank_relevance(self, query, candidates, *, limit=10):
        ranked = super().rank_relevance(query, candidates, limit=len(candidates))
        return sorted(ranked, key=lambda row: (not row.id.startswith('legacy-view'), -row.score))[:limit]


class ThoughtNavigationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = MemoryStore(Path(self.temp.name) / 'navigation.sqlite3')
        self.addCleanup(self.memory.close)
        self.store = ThoughtStore(self.memory)
        self.queue = CaptureQueue(self.memory)

    def test_overlapping_legacy_views_do_not_expand_every_graph_before_direct_answer(self):
        question = 'What speed and token constraints did Vlad set?'
        raw = self.queue.capture_event('fixture', 'constraints', 'Minimize time and token spending. Use focused tests; run a full suite only for a demonstrated risk.', source='fixture', actor='Vlad')
        source = self.store.publish(raw.prompt, bases=[{'event_id': raw.id}],
            origin={'kind': 'legacy_assertion', 'actor': 'Vlad'}, metadata={'verification': 'legacy_unverified'})
        others = [self.store.publish('Unrelated database design detail ' + str(i),
            origin={'kind': 'legacy_assertion'}, metadata={'verification': 'legacy_unverified'}) for i in range(35)]
        for index in range(10):
            self.store.publish('Unverified broad speed and token navigation overview.', question=question + ' overview',
                thought_id='legacy-view-' + str(index), origin={'kind': 'legacy_view'},
                metadata={'verification': 'legacy_unverified'},
                bases=[{'thought_id': row.id, 'version': 1} for row in [source, *others]])
        text = 'Minimize time and tokens; use focused tests, with a full suite only for a demonstrated risk. Unread branches have not been checked.'
        model = ScriptedModel(draft(text, [source.text]), ACCEPT)
        events = []
        answer = ThoughtEngine(self.store, model, ViewsFirstSearch(), trace=events.append).answer(question)
        self.assertEqual(answer.text, text)
        self.assertEqual(answer.bases[0]['event_id'], raw.id)
        expansions = [row for row in events if row['stage'] == 'expansion']
        self.assertEqual(len([row for row in expansions if row['parent']['thought_id'].startswith('legacy-view')]), 1)
        packet = [row for row in model.requests[0].records if row['role'] == 'material']
        self.assertLessEqual(len(packet), 10)
        self.assertTrue(all(row['origin'].get('kind') != 'legacy_view' for row in packet))
        self.assertTrue(all(row['material_availability']['coverage'] == 'partial' for row in packet))

    def test_selected_packet_keeps_complete_conditions_conflicts_and_uncertainty(self):
        qualifications = {'conditions': ['A concrete failure may require the complete suite.'],
            'conflicts': ['An earlier proposal requested broader checks.'], 'uncertainty': 'Spending is unknown.'}
        raw = self.queue.capture_event('fixture', 'conditions', 'Use the short checks. A complete suite remains required when a concrete failure demands it. Spending is unknown.',
            source='fixture', metadata=qualifications)
        source = self.store.publish(raw.prompt, bases=[{'event_id': raw.id}],
            origin={'kind': 'legacy_assertion'}, metadata={'verification': 'legacy_unverified', **qualifications})
        model = ScriptedModel(draft(source.text, [source.text]), ACCEPT)
        engine = ThoughtEngine(self.store, model)
        records, bases = engine._current_material('Which checks are allowed?', [source])
        answer = engine._construct(records, bases, question='Which checks are allowed?')[0]
        self.assertEqual(records[0]['text'], source.text)
        self.assertEqual(records[0]['qualifications']['conditions'], source.metadata['conditions'])
        self.assertEqual(records[0]['qualifications']['conflicts'], source.metadata['conflicts'])
        self.assertEqual(model.requests[-1].records[0]['qualifications']['uncertainty'], 'Spending is unknown.')
        self.assertIn('when a concrete failure', answer.text)
        self.assertNotIn('material_availability', records[0])


if __name__ == '__main__':
    unittest.main()
