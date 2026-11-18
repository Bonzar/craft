"""Answer only at the selected location; navigate for necessary unopened detail."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_answering import answer_selected
from prototypes.assertion_memory.thought_engine import ThoughtDetailNeeded, ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.tests.test_thought_engine import ACCEPT, ScriptedModel, draft


class ThoughtAnsweringTest(unittest.TestCase):
    def test_support_identity_ignores_question_and_pair_order_but_preserves_full_content(self):
        selected = self.store.publish('Gate rules.', metadata={'verification': 'accepted'})
        packets = [
            ([{'role': 'candidate', 'text': 'Gate rules.'}, {'role': 'neighbor_option', 'description': 'Exception.'}],
             [{'thought_id': selected.id, 'version': 1}, {'thought_id': 'detail', 'version': 1}]),
            ([{'role': 'neighbor_option', 'description': 'Exception.'}, {'role': 'candidate', 'text': 'Gate rules.'}],
             [{'thought_id': 'detail', 'version': 1}, {'thought_id': selected.id, 'version': 1}]),
            ([{'role': 'candidate', 'text': 'Gate rules.'}, {'role': 'material', 'text': 'Exception. Full condition.'}],
             [{'thought_id': selected.id, 'version': 1}, {'thought_id': 'detail', 'version': 1}]),
        ]
        model = ScriptedModel(*({'verdict': 'drill', 'question': 'Missing condition', 'text': 'The condition is missing.'} for _ in packets))
        engine = self.engine(model)
        fingerprints = []
        with patch('prototypes.assertion_memory.thought_answering._material', side_effect=packets):
            for question in ('Original question', 'Different wording', 'Read the full condition'):
                with self.assertRaises(ThoughtDetailNeeded) as need:
                    answer_selected(engine, question, selected, neighborhood=None)
                fingerprints.append(need.exception.packet_fingerprint)
        self.assertEqual(fingerprints[0], fingerprints[1])
        self.assertNotEqual(fingerprints[1], fingerprints[2])

    def test_changed_support_is_not_a_current_ground_and_new_head_is_discoverable(self):
        from prototypes.assertion_memory.thought_navigation import neighborhood
        source = self.store.publish('The meeting is on Tuesday.', metadata={'verification': 'accepted'})
        old = self.store.publish('The meeting is on Tuesday.', question='When is the meeting?',
            bases=[{'thought_id': source.id}], metadata={'verification': 'accepted'})
        latest = self.store.publish('The meeting is on Wednesday.', thought_id=source.id,
            expected_version=source.version, metadata={'verification': 'accepted'})
        model = ScriptedModel(draft(old.text, [old.text]), draft(latest.text, [latest.text]), ACCEPT)
        engine = self.engine(model)
        result = answer_selected(engine, old.question, old, neighborhood=neighborhood)
        self.assertEqual(result.text, latest.text)
        self.assertTrue(engine._fresh(result))
        self.assertEqual([(b['thought_id'], b['version']) for b in result.bases], [(latest.id, latest.version)])
        self.assertNotIn(old.text, [r.get('text') for r in model.requests[0].records])
        self.assertIn(latest.text, [r.get('text') for r in model.requests[0].records])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = MemoryStore(Path(self.temp.name) / 'answering.sqlite3')
        self.addCleanup(self.memory.close)
        self.store = ThoughtStore(self.memory)

    def engine(self, model):
        engine = ThoughtEngine(self.store, model)
        # Public question API supplied by the independent questions change.
        engine.question_texts = lambda row: [row.question] if row.question else []
        original_content = engine._content
        def content(row):
            result = original_content(row)
            result.pop('question', None)
            return {**result, 'questions': engine.question_texts(row)}
        engine._content = content
        def forbidden(*args, **kwargs):
            raise AssertionError('answering must not retrieve distant starting points')
        engine.retrieve = forbidden
        return engine

    def catalog(self, engine, adjacency):
        def neighborhood(current_engine, focus, question):
            self.assertIs(current_engine, engine)
            neighbors = tuple(adjacency.get(focus.id, ()))
            relation = {'kind': 'semantic', 'direction': 'outgoing', 'meaning': 'Additional condition or history',
                        'from_ref': engine._basis(focus), 'link_id': 'CODE_ONLY_LINK'}
            return {'focus': focus, 'neighbors': neighbors,
                'bindings': tuple(engine._basis(row) for row in (focus, *neighbors)),
                'relations': (relation,), 'model_options': tuple({
                    'questions': engine.question_texts(row), 'description': row.text.split('\n\n', 1)[0],
                    'description_complete': '\n\n' not in row.text,
                    'description_total_characters': len(row.text), 'relations': [
                        {key: relation[key] for key in ('kind', 'direction', 'meaning')}],
                    'version_status': 'selected_version'} for row in neighbors),
                'complete': True, 'omissions': ()}
        return neighborhood

    def test_ready_content_uses_own_neighborhood_and_does_not_open_unneeded_full_neighbors(self):
        question = 'What approval is needed to open the gate?'
        selected = self.store.publish('Routine opening needs Mira approval; evacuation is exempt.', question=question,
                                      metadata={'verification': 'accepted'})
        neighbor = self.store.publish('Supplementary gate history.\n\nUNOPENED_FULL_TAIL is unnecessary history.',
            metadata={'verification': 'accepted', 'conditions': ['Evacuation is exempt.']})
        self.store.publish('FAR_DISTANT_MATERIAL is unrelated.')
        model = ScriptedModel({'verdict': 'ready', 'question': None, 'text': 'The candidate includes the necessary exception.'})
        engine = self.engine(model)
        neighborhood = self.catalog(engine, {selected.id: [neighbor]})
        seen = set()
        result = answer_selected(engine, question, selected, neighborhood=neighborhood, seen_packets=seen)
        self.assertEqual(result, selected)
        wire = json.dumps(model.requests[0].records)
        self.assertNotIn('FAR_DISTANT_MATERIAL', wire)
        self.assertNotIn('UNOPENED_FULL_TAIL', wire)
        self.assertNotIn('CODE_ONLY_LINK', wire)
        self.assertNotIn(selected.id, wire)
        self.assertIn('Evacuation is exempt.', wire)
        self.assertIn('questions', model.requests[0].records[1])
        self.assertNotIn('question', model.requests[0].records[1])
        with self.assertRaises(ThoughtDetailNeeded) as repeated:
            answer_selected(engine, question, selected, neighborhood=neighborhood, seen_packets=seen)
        self.assertTrue(repeated.exception.incomplete)
        self.assertEqual(len(model.requests), 1)

    def test_missing_question_resumes_original_with_explicit_full_neighbor_and_exact_grounds(self):
        question = 'How is gate opening controlled?'
        missing = 'What approval exceptions apply to gate opening?'
        selected = self.store.publish('Gate opening has approval rules.', question=question,
                                      metadata={'verification': 'accepted'})
        condition = 'Routine openings require Mira approval; emergency evacuation is exempt.'
        detail = self.store.publish('Gate approval detail.\n\n' + condition, question=missing,
                                    metadata={'verification': 'accepted'})
        self.store.publish('FAR_DISTANT_MATERIAL is unrelated.')
        model = ScriptedModel({'verdict': 'drill', 'question': missing, 'text': 'The unopened detail may change the exception.'},
            {'verdict': 'build', 'question': None, 'text': 'The explicitly opened detail now supplies the condition.'},
            draft(condition, [condition]), ACCEPT)
        engine = self.engine(model)
        neighborhood = self.catalog(engine, {selected.id: [detail], detail.id: [selected]})
        seen = set()
        with self.assertRaises(ThoughtDetailNeeded) as needed:
            answer_selected(engine, question, selected, neighborhood=neighborhood, seen_packets=seen)
        self.assertEqual(needed.exception.question, missing)
        self.assertFalse(needed.exception.incomplete)
        self.assertNotIn(condition, json.dumps(model.requests[0].records))
        result = answer_selected(engine, question, selected, neighborhood=neighborhood,
                                 additional=[detail], seen_packets=seen)
        self.assertNotEqual(result.id, selected.id)
        self.assertEqual(result.version, 1)
        self.assertEqual(self.store.get(selected.id), selected)
        self.assertEqual(result.text, condition)
        self.assertEqual([basis['thought_id'] for basis in result.bases], [detail.id])
        self.assertEqual(result.bases[0]['span']['start'], detail.text.index(condition))
        self.assertEqual([row.task for row in model.requests], ['Check ready thought', 'Check ready thought',
                                                              'Construct thought', 'Verify thought'])
        self.assertNotIn('FAR_DISTANT_MATERIAL', json.dumps([row.records for row in model.requests]))


if __name__ == '__main__':
    unittest.main()
