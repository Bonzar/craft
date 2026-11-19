"""Equal questions cannot narrow a source head or privilege a refresh facet."""
import json
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_answering import answer_selected, refresh_thought
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_engine_contract import PROTOCOL
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_worker import ThoughtWorker
from .prepared_support_helpers import PreparedModel, drafts, record_text
ACCEPT={'verdict':'accept','text':'The meaning and conditions are retained.'}


class ThoughtWholeRefreshTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = MemoryStore(Path(self.temp.name) / 'whole.sqlite3')
        self.addCleanup(self.memory.close)
        self.store, self.queue = ThoughtStore(self.memory), CaptureQueue(self.memory)

    def test_narrow_query_build_does_not_overwrite_wide_source_or_its_equal_questions(self):
        raw = self.queue.capture_event('fixture', 'source', 'The room is twelve square metres and its finish awaits inspection.', source='fixture')
        selected = self.store.publish(raw.prompt, origin={'kind': 'source_interpretation', 'event_id': raw.id},
            bases=[{'event_id': raw.id}], metadata={'verification': 'accepted'})
        broad, narrow = 'What is the room state?', 'Is the room finish ready?'
        for question in (broad, narrow):
            self.store.save_question_check(selected, question, 'old-ready:' + question)
        detail = self.store.publish('The finish is ready only after inspection.', metadata={'verification': 'accepted'})
        model = PreparedModel({'verdict': 'build', 'question': None, 'text': 'A focused answer should expose the condition.'},
                              lambda request: drafts(request,(detail.text,[detail.text])), ACCEPT)
        engine = ThoughtEngine(self.store, model)
        def neighborhood(current, focus, question):
            return {'focus': focus, 'neighbors': (detail,), 'bindings': (engine._basis(focus), engine._basis(detail)),
                'relations': (), 'model_options': ({'questions': [], 'description': detail.text,
                    'description_complete': True, 'description_total_characters': len(detail.text), 'relations': []},),
                'complete': True, 'omissions': ()}
        result = answer_selected(engine, narrow, selected, neighborhood=neighborhood)
        self.assertNotEqual(result.id, selected.id)
        self.assertEqual(self.store.get(selected.id), selected)
        self.assertEqual(set(engine.question_texts(selected)), {broad, narrow})
        self.assertEqual(selected.origin['kind'], 'source_interpretation')
        worker = ThoughtWorker(self.queue, engine, lane='maintenance')
        engine.retrieve = lambda *args, **kwargs: [selected]
        worker.enqueue_dependents([detail])
        self.assertIsNone(worker.work.get(selected.id))
        self.assertEqual(refresh_thought(engine, selected), selected)
        self.assertEqual(len(model.requests), 3)

    def test_earlier_report_processed_later_does_not_replace_current_state(self):
        question = 'Has the room been inspected?'
        latest = 'The room has been inspected.'
        earlier = 'The room has not been inspected yet.'
        newer_at = '2026-11-19T12:00:00Z'
        older_at = '2026-11-19T10:00:00Z'
        metadata = {'protocol': PROTOCOL, 'verification': 'accepted'}
        newer = self.store.publish(latest,
            origin={'kind': 'source_interpretation', 'source_message_at': newer_at},
            metadata=metadata)
        published = self.store.publish(latest, question=question, origin={'kind': 'inference'},
            bases=[{'thought_id': newer.id, 'version': newer.version,
                    'span': {'start': 0, 'end': len(latest)}, 'meaning': latest}],
            metadata=metadata)
        older = self.store.publish(earlier,
            origin={'kind': 'source_interpretation', 'source_message_at': older_at},
            metadata=metadata)
        self.assertGreater(older.created_at, newer.created_at)

        def build(request):
            by_text = {record_text(row): row for row in request.records}
            self.assertTrue(by_text[latest]['was_prior_basis'])
            self.assertFalse(by_text[earlier]['was_prior_basis'])
            self.assertEqual(by_text[latest]['origin']['source_message_at'], newer_at)
            self.assertEqual(by_text[earlier]['origin']['source_message_at'], older_at)
            self.assertIn('A late-processed report of an earlier state', request.instructions)
            return drafts(request, (latest, [latest]))

        def verify(request):
            self.assertIn('A late-processed report of an earlier state', request.instructions)
            self.assertTrue(any(record_text(row) == earlier for row in request.records))
            return dict(ACCEPT, meaning_changed=False)

        model = PreparedModel(build, verify)
        engine = ThoughtEngine(self.store, model)
        result = refresh_thought(engine, published, additional=[older])
        self.assertEqual(result, published)
        self.assertEqual(self.store.versions(published.id), [published])
        self.assertEqual(self.store.get(older.id), older)
        self.assertEqual([row.task for row in model.requests], ['Construct thought', 'Verify thought'])

    def test_worker_refreshes_all_questions_with_one_builder_and_no_old_ready_inheritance(self):
        old_text = 'The room is twelve square metres and has a stove.'
        source = self.store.publish(old_text, metadata={'verification': 'accepted'})
        current = self.store.publish(old_text, origin={'kind': 'inference'},
            bases=[{'thought_id': source.id, 'version': 1}], metadata={'verification': 'accepted'})
        questions = {'What are the room dimensions?', 'Is the room equipment ready?'}
        for question in questions:
            self.store.save_question_check(current, question, 'old-ready:' + question)
        new_text = 'The room is twelve square metres; the induction stove is ready only after inspection.'
        source = self.store.publish(new_text, thought_id=source.id, expected_version=1,
                                    metadata={'verification': 'accepted'})
        calls = []
        def model(request):
            self.assertFalse(self.memory._writing)
            calls.append(request)
            scope = next(row for row in request.records if row.get('role') == 'whole_refresh_scope')
            self.assertEqual(set(scope['questions']), questions)
            self.assertEqual(scope['prior_content'], old_text if len(calls) <= 2 else new_text)
            self.assertNotIn('text', scope)
            self.assertTrue(any(record_text(row) == new_text for row in request.records))
            if request.task == 'Construct thought':
                self.assertIn('ONE WHOLE thought', request.instructions)
                return json.dumps(drafts(request,(new_text,[new_text]),question='Is the room equipment ready?'))
            self.assertIn('ALL equal', request.instructions)
            return json.dumps(dict(ACCEPT, meaning_changed=True))
        engine = ThoughtEngine(self.store, model)
        engine.retrieve = lambda *args, **kwargs: []
        worker = ThoughtWorker(self.queue, engine, lane='maintenance')
        worker.enqueue_dependents([source])
        self.assertEqual(worker.work.get(current.id)['action'], 'refresh')
        self.assertIsNone(current.question)
        result = worker.run_once()
        self.assertEqual(result['completed_version'], 2)
        refreshed = self.store.get(current.id)
        self.assertEqual(refreshed.text, new_text)
        self.assertIsNone(refreshed.question)
        self.assertEqual(set(engine.question_texts(refreshed)), questions)
        for question in questions:
            self.assertFalse(self.store.question_checked(refreshed, question, 'old-ready:' + question))
        self.assertEqual([row.task for row in calls], ['Construct thought', 'Verify thought'])
        self.assertEqual(len(self.store.versions(current.id)), 2)
        unchanged = refresh_thought(engine, refreshed)
        self.assertEqual(unchanged, refreshed)
        self.assertEqual(len(self.store.versions(current.id)), 2)
        self.assertEqual(len(calls), 4)


if __name__ == '__main__':
    unittest.main()
