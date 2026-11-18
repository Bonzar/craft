"""Temporal provenance at the deterministic model boundary, without manual kinds/edges."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from prototypes.assertion_memory.dialogue import extract_dialogue
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore


class DialogueTemporalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = MemoryStore(Path(self.tmp.name) / 'memory.sqlite')
        self.addCleanup(self.store.close)
        self.now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
        self.queue = CaptureQueue(self.store, clock=lambda: self.now)
        self.requests = []

    def source(self, text='Проверил 5 ноября 2026 года: свет выключен.', **kwargs):
        return self.queue.capture_event('s', 't', text, source='dialogue://s/t', **kwargs)

    def extract(self, event, valid_at='2026-11-05', temporal_quote='5 ноября 2026 года', status='accepted'):
        value = {'text': event.prompt, 'quote': event.prompt, 'kind': 'observation'}
        if valid_at is not None:
            value.update(valid_at=valid_at, temporal_evidence=[{
                'event_id': event.id, 'quote': temporal_quote}])
        def model(request):
            self.requests.append(request)
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': [value]})
            return json.dumps({'verdicts': [{'index': 0, 'status': status, 'reason': 'Temporal anchor checked.'}]})
        return extract_dialogue(model, event, self.queue.context_events(event.id))

    def persist(self, event, extraction):
        claim = self.queue._claim(with_model=True)
        while claim.job.event_id is None:
            self.queue._finish(claim, {})
            claim = self.queue._claim(with_model=True)
        self.assertEqual(claim.job.event_id, event.id)
        return self.queue._finish_extraction(claim, event, extraction)

    def test_event_date_survives_delayed_extraction_and_keeps_three_clocks(self):
        event = self.source()
        self.now = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
        job = self.persist(event, self.extract(event))
        assertion = self.store.get_assertion(job.extracted_ids[0])
        self.assertEqual(assertion.valid_at, '2026-11-05T00:00:00.000000+00:00')
        self.assertEqual(assertion.recorded_at, event.recorded_at)
        self.assertEqual(assertion.metadata['interpretation']['interpreted_at'], self.queue._time())
        anchor = assertion.metadata['temporal_evidence'][0]
        self.assertEqual(self.queue.resolve_reference(anchor)['text'], '5 ноября 2026 года')
        self.assertNotIn('quote', anchor)

    def test_unknown_event_time_is_not_capture_time(self):
        event = self.source('Свет выключен.')
        job = self.persist(event, self.extract(event, valid_at=None))
        self.assertIsNone(self.store.get_assertion(job.extracted_ids[0]).valid_at)

    def test_missing_or_fabricated_temporal_anchor_is_rejected(self):
        event = self.source()
        with self.assertRaises(ModelError):
            self.extract(event, temporal_quote='4 ноября')

    def test_verifier_can_reject_wrong_date_despite_exact_anchor(self):
        event = self.source()
        result = self.extract(event, valid_at='2026-11-06', status='unsupported')
        self.assertEqual(result['assertions'], [])
        verifier = next(request for request in reversed(self.requests)
                        if request.task == 'Verify contextual assertion interpretations')
        interpreted = next(row for row in verifier.records if row.get('role') == 'interpretation')
        self.assertEqual(interpreted['valid_at'], '2026-11-06')
        self.assertIn('valid_at', verifier.instructions)

    def test_timezone_and_source_message_time_available_for_relative_dates(self):
        event = self.source('Вчера проверил: свет выключен.', metadata={'timezone': 'Europe/Moscow',
            'source_message_at': '2026-11-20T12:00:00Z', 'source_message_clock_basis': {
                'kind': 'producer', 'field': 'sent_at', 'value': '2026-11-20T12:00:00Z', 'source': 'dialogue://s/t'}})
        result = self.extract(event, valid_at='2026-11-19', temporal_quote='Вчера')
        source = self.requests[0].records[0]
        self.assertEqual(source['timezone'], 'Europe/Moscow')
        self.assertEqual(source['recorded_at'], event.recorded_at)
        self.assertEqual(source['source_message_at'], '2026-11-20T12:00:00.000000+00:00')
        self.assertEqual(result['assertions'][0]['valid_at'], '2026-11-19')

    def test_source_availability_can_be_imported_without_changing_retry_identity(self):
        event = self.source(recorded_at='2026-11-15T09:00:00+03:00')
        repeated = self.source()
        self.assertEqual(event, repeated)
        self.assertEqual(event.recorded_at, '2026-11-15T06:00:00.000000+00:00')
        with self.assertRaises(ValueError):
            self.source(recorded_at='2026-11-16T09:00:00+03:00')

    def test_corrected_event_time_is_a_new_immutable_interpretation(self):
        event = self.source()
        first = self.persist(event, self.extract(event))
        old = self.store.get_assertion(first.extracted_ids[0])
        self.queue.retry(event.id)
        # Stand-in verifier supplies a different accepted interpretation on reprocessing.
        second = self.persist(event, self.extract(event, valid_at='2026-11-06'))
        self.assertEqual(len(second.extracted_ids), 2)
        self.assertEqual(self.store.get_assertion(old.assertion_id), old)
        self.assertEqual(len(self.queue.extraction_history(event.id)), 2)

    def test_late_report_distinguishes_effective_date_from_source_availability(self):
        event = self.source(
            '15 ноября 2026 года узнали, что исключение действовало с 4 ноября 2026 года.',
            recorded_at='2026-11-15T12:00:00Z')
        job = self.persist(event, self.extract(event, valid_at='2026-11-04', temporal_quote='с 4 ноября 2026 года'))
        assertion = self.store.get_assertion(job.extracted_ids[0])
        self.assertEqual(assertion.valid_at, '2026-11-04T00:00:00.000000+00:00')
        self.assertEqual(assertion.recorded_at, '2026-11-15T12:00:00.000000+00:00')
        self.assertEqual(assertion.metadata['interpretation']['interpreted_at'], self.queue._time())

    def test_late_arriving_temporal_context_sets_latest_source_availability(self):
        anchor = self.queue.capture_event(
            's', 'anchor', 'Речь о ноябре 2026 года.', source='dialogue://s/anchor',
            recorded_at='2026-11-18T12:00:00Z', position=0, actor='assistant')
        event = self.source('Проверил 5-го: свет выключен.', recorded_at='2026-11-15T12:00:00Z', position=1)
        value = {'text': event.prompt, 'quote': event.prompt, 'kind': 'observation', 'valid_at': '2026-11-05',
                 'temporal_evidence': [{'event_id': event.id, 'quote': '5-го'},
                                       {'event_id': anchor.id, 'quote': 'ноябре 2026 года'}]}
        def model(request):
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': [value]})
            return json.dumps({'verdicts': [{'index': 0, 'status': 'accepted', 'reason': 'Cited month and day.'}]})
        extraction = extract_dialogue(model, event, self.queue.context_events(event.id))
        job = self.persist(event, extraction)
        assertion = self.store.get_assertion(job.extracted_ids[0])
        self.assertEqual(assertion.recorded_at, anchor.recorded_at)
        self.assertEqual(assertion.metadata['source_recorded_at'], event.recorded_at)

    def test_temporal_data_without_anchors_or_with_naive_instant_is_rejected(self):
        event = self.source()
        for temporal in ({'valid_at': '2026-11-05'}, {'valid_at': '2026-11-05T12:00:00',
                          'temporal_evidence': [{'event_id': event.id, 'quote': '5 ноября 2026 года'}]}):
            value = {'text': event.prompt, 'quote': event.prompt, **temporal}
            with self.subTest(temporal=temporal), self.assertRaises(ModelError):
                extract_dialogue(lambda request: json.dumps({'assertions': [value]}), event, [event])

    def test_current_clock_is_source_time_only_and_never_an_inferred_event_date(self):
        queue = CaptureQueue(self.store)
        event = queue.capture_event('now', '1', 'Проверил: свет выключен.', source='dialogue://now')
        extraction = self.extract(event, valid_at=None)
        job = queue._finish_extraction(queue._claim(with_model=True), event, extraction)
        assertion = self.store.get_assertion(job.extracted_ids[0])
        self.assertIsNone(assertion.valid_at)
        self.assertEqual(assertion.recorded_at, event.recorded_at)


if __name__ == '__main__':
    unittest.main()
