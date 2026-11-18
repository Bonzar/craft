"""Use historical primary speech and satisfy missing detail inside the engine."""
import json
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.model import Candidate, CandidateSearch
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.tests.test_thought_engine import ACCEPT, ScriptedModel, draft


class QuestionSearch(CandidateSearch):
    def __init__(self, detail_question):
        super().__init__()
        self.detail_question = detail_question

    def rank_relevance(self, query, candidates, *, limit=10):
        wanted = 'detail-source' if query == self.detail_question else 'base-source'
        if wanted in candidates:
            return [Candidate(wanted, 1.0, 'fixture')]
        return super().rank_relevance(query, candidates, limit=limit)


class ThoughtDetailTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = MemoryStore(Path(self.temp.name) / 'details.sqlite3')
        self.addCleanup(self.memory.close)
        self.store = ThoughtStore(self.memory)
        self.queue = CaptureQueue(self.memory)

    def test_legacy_material_contains_primary_speech_and_bounded_visible_context(self):
        proposal = self.queue.capture_event('fixture', 'proposal',
            'Use focused tests; run the full suite only for a concrete failure.',
            source='codex://fixture', actor='assistant', metadata={'role': 'assistant'})
        raw = self.queue.capture_event('fixture', 'choice', 'Yes, use that testing plan.',
            source='codex://fixture', actor='user', metadata={'role': 'user'})
        legacy = self.store.publish('UNVERIFIED_PARAPHRASE: Always run every test.',
            thought_id='base-source', origin={'kind': 'legacy_assertion'},
            bases=[{'event_id': raw.id}], metadata={'verification': 'legacy_unverified'})
        text = 'The user chose focused tests, with the full suite only for a concrete failure.'
        model = ScriptedModel(dict(draft(text, [raw.prompt, proposal.prompt]), needs_detail=None), ACCEPT)
        engine = ThoughtEngine(self.store, model)
        records, bases = engine._current_material('What testing plan was chosen?', [legacy])
        answer = engine._construct(records, bases, question='What testing plan was chosen?')[0]
        wire = json.dumps(model.requests[0].records)
        self.assertNotIn('UNVERIFIED_PARAPHRASE', wire)
        self.assertEqual([row['origin']['role'] for row in records], ['user', 'assistant'])
        self.assertTrue(all(row['verification'] == 'original_source' for row in records))
        self.assertEqual({row['event_id'] for row in answer.bases}, {raw.id, proposal.id})
        self.assertIn('only for a concrete failure', answer.text)

    def test_needs_detail_fetches_builds_and_retries_original_question_centrally(self):
        original_question = 'What speed and test limits did Vlad set?'
        detail_question = 'When is the full test suite allowed?'
        base_raw = self.queue.capture_event('base', 'limits', 'Minimize time and token spending; use focused tests.', source='fixture')
        detail_raw = self.queue.capture_event('detail', 'exception', 'A full suite is allowed only when a concrete failure requires it.', source='fixture')
        with self.memory.transaction(write=True) as connection:
            connection.execute("UPDATE memory_capture_jobs SET status='completed',phase='done' WHERE event_id=?", (detail_raw.id,))
        for key, raw in (('base-source', base_raw), ('detail-source', detail_raw)):
            self.store.publish('UNVERIFIED summary for ' + key, thought_id=key,
                origin={'kind': 'legacy_assertion'}, bases=[{'event_id': raw.id}],
                metadata={'verification': 'legacy_unverified'})
        detail_text = 'A full suite is allowed only when a concrete failure requires it.'
        final_text = 'Minimize time and tokens; use focused tests. A full suite is allowed only for a concrete failure.'
        model = ScriptedModel({'thoughts': [], 'needs_detail': {'question': detail_question, 'text': 'The suite exception is necessary.'}},
            dict(draft(detail_text, [detail_raw.prompt]), needs_detail=None), ACCEPT,
            dict(draft(final_text, [base_raw.prompt, detail_text]), needs_detail=None), ACCEPT)
        answer = ThoughtEngine(self.store, model, QuestionSearch(detail_question)).answer(original_question)
        details = [row for row in self.store.list_thoughts() if row.question == detail_question]
        self.assertEqual(len(details), 1)
        self.assertEqual(answer.question, original_question)
        self.assertEqual(answer.text, final_text)
        self.assertIn(details[0].id, [row.get('thought_id') for row in answer.bases])
        self.assertEqual(model.requests[1].records[0]['text'], detail_question)
        self.assertEqual(model.requests[-2].records[0]['text'], original_question)
        self.assertEqual(len(model.requests), 5)


if __name__ == '__main__':
    unittest.main()
