"""Fictional clocks and deterministic boundary reviewers; never contact models."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prototypes.assertion_memory import dialogue
from prototypes.assertion_memory.dialogue_context import ContextWindow
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.overview import _model_record
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker

ORIGIN = '2026-03-10T23:30:00.000000+00:00'
INGEST = '2026-03-13T12:00:00.000000+00:00'
BODY = 'Yesterday I repaired a fictional radio.'

def producer_clock(value=ORIGIN, source='fixture://radio', **extra):
    return {'source_message_at': value, 'source_message_clock_basis': {
        'kind': 'producer', 'field': 'sent_at', 'value': value, 'source': source}, **extra}


class SourceMessageClockTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:'); self.addCleanup(self.store.close)
        self.now = datetime.fromisoformat(INGEST)
        self.queue = CaptureQueue(self.store, clock=lambda: self.now)

    def source(self, metadata=None, **kwargs):
        return self.queue.capture_event('fiction', 'one', BODY, source='fixture://radio',
            metadata=metadata, **kwargs)

    def normalize(self, event, refs):
        records, _ = dialogue._context_records(event, [event])
        window = ContextWindow(event, [event], records, dialogue._event_record, budget=36_000)
        claims = dialogue._normalize_claims(event, [{'text': BODY, 'quote': BODY, 'valid_at': '2026-03-10',
            'temporal_evidence': refs}], [event], window.records(), window)
        return claims, window

    def test_projection_separates_knowledge_origin_calendar_and_preserves_compact_basis(self):
        event = self.source(producer_clock(timezone='Europe/Moscow'))
        row = dialogue._event_record(event, is_target=True)
        self.assertEqual(row['recorded_at'], INGEST)
        self.assertEqual(row['base_available_at'], INGEST)
        self.assertEqual(row['source_message_at'], ORIGIN)
        self.assertEqual(row['source_message_clock_basis'], event.metadata['source_message_clock_basis'])
        self.assertEqual(row['timezone'], 'Europe/Moscow')
        compact = _model_record({'id': 'fact', 'text': BODY, 'metadata': event.metadata})
        self.assertEqual(compact['metadata']['source_message_at'], ORIGIN)
        self.assertEqual(compact['metadata']['source_message_clock_basis'], row['source_message_clock_basis'])

    def test_clockless_source_even_with_timezone_stays_unknown(self):
        event = self.source({'timezone': 'Europe/Moscow', 'source_recorded_at': '2026-03-10'})
        row = dialogue._event_record(event, is_target=True)
        self.assertIsNone(row['source_message_at'])
        self.assertIsNone(row['source_message_clock_basis'])
        self.assertEqual(row['base_available_at'], INGEST)

    def test_invalid_present_clocks_basis_and_timezone_are_explicit_errors_without_write(self):
        invalid = ['2026-03-10', '2026-03-10T12:00:00', '2026-03-10 12:00:00Z',
                   '2026-02-30T12:00:00Z', '2026-03-10T12:00:00+25:00',
                   '2026-03-10T12:00:00+01:60', 42, None]
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'source.message|clock'):
                self.source(producer_clock(value))
        for metadata in ({'source_message_at': ORIGIN}, producer_clock(source='fixture://other'),
                         producer_clock(timezone='Mars/Olympus'),
                         {**producer_clock(), 'source_message_clock_basis': {'verified': True}}):
            with self.subTest(metadata=metadata), self.assertRaisesRegex(ValueError, 'clock|timezone|source.message'):
                self.source(metadata)
        self.assertEqual(self.queue.list_events(), [])

    def test_only_source_clock_metadata_is_normalized_with_same_source_body_anchor_and_audit(self):
        event = self.source(producer_clock(timezone='Europe/Moscow'))
        claims, window = self.normalize(event, [{'event_id': event.id, 'quote': 'Yesterday'},
                                              {'event_id': event.id, 'quote': ORIGIN}])
        self.assertEqual([ref['quote'] for ref in claims[0]['temporal_evidence']], ['Yesterday'])
        audit = window.audit[0]
        self.assertEqual(audit['field'], 'source_message_at')
        self.assertEqual(audit['value_sha256'], hashlib.sha256(ORIGIN.encode()).hexdigest())
        self.assertEqual(audit['source_message_clock_basis'], event.metadata['source_message_clock_basis'])

    def test_ingestion_clock_cannot_be_normalized_as_temporal_evidence(self):
        event = self.source(producer_clock(timezone='Europe/Moscow'))
        with self.assertRaises(ModelError):
            self.normalize(event, [{'event_id': event.id, 'quote': 'Yesterday'},
                                   {'event_id': event.id, 'quote': INGEST}])

    def test_origin_token_alone_wrong_id_or_forged_basis_rejects(self):
        event = self.source(producer_clock())
        for refs in ([{'event_id': event.id, 'quote': ORIGIN}],
                     [{'event_id': 'wrong', 'quote': ORIGIN}, {'event_id': event.id, 'quote': 'Yesterday'}]):
            with self.subTest(refs=refs), self.assertRaises(ModelError): self.normalize(event, refs)
        forged = replace(event, metadata={**event.metadata, 'source_message_clock_basis': {'verified': True}})
        with self.assertRaises((ModelError, ValueError)): self.normalize(forged, [
            {'event_id': event.id, 'quote': 'Yesterday'}, {'event_id': event.id, 'quote': ORIGIN}])

    def test_actual_body_timestamp_equal_to_knowledge_clock_is_still_literal_proof(self):
        event = replace(self.source(), prompt=BODY + ' Printed timestamp: ' + INGEST)
        refs = [{'event_id': event.id, 'quote': 'Yesterday'}, {'event_id': event.id, 'quote': INGEST}]
        claims, window = self.normalize(event, refs)
        self.assertEqual(claims[0]['temporal_evidence'][-1]['quote'], INGEST)
        self.assertEqual(window.audit, [])

    def test_all_generation_boundaries_share_projection_and_clock_rules(self):
        event = self.source(producer_clock(timezone='Europe/Moscow'))
        calls = []
        def provider(request):
            calls.append(request)
            raw = next(row for row in request.records if row.get('id') == event.id)
            self.assertEqual(raw['source_message_at'], ORIGIN)
            self.assertEqual(raw['base_available_at'], INGEST)
            self.assertIn('source_message_at', request.instructions)
            self.assertIn('speaker', request.instructions)
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': [{'text': BODY, 'quote': BODY, 'valid_at': '2026-03-10',
                    'temporal_evidence': [{'event_id': event.id, 'quote': 'Yesterday'}]}]})
            if request.task.startswith('Verify'):
                return json.dumps({'verdicts': [{'index': 0, 'status': 'accepted',
                    'reason': 'Fixture: explicitly supplied Moscow calendar makes local March 11 yesterday March 10.'}]})
            return json.dumps({'source_coverage': {'status': 'accepted', 'reason': 'Fixture complete.', 'omissions': []}})
        result = dialogue.extract_dialogue(provider, event, [event])
        self.assertEqual(result['assertions'][0]['valid_at'], '2026-03-10')
        self.assertEqual(result['assertions'][0]['temporal_precision'], 'day')
        self.assertEqual(len(calls), 3)

    def test_unknown_speaker_calendar_model_boundary_does_not_use_transport_utc_or_ingestion(self):
        for metadata in (producer_clock(), {'timezone': 'Europe/Moscow'}, {}):
            with self.subTest(metadata=metadata):
                event = replace(self.source(), metadata=metadata)
                def provider(request):
                    self.assertIn('source_message_at', request.instructions)
                    self.assertIn('transport', request.instructions)
                    self.assertIn('ingestion', request.instructions)
                    raw = next(row for row in request.records if row.get('id') == event.id)
                    self.assertEqual(raw['base_available_at'], INGEST)
                    if request.task.startswith('Extract'):
                        return json.dumps({'assertions': [{'text': BODY, 'quote': BODY}]})
                    if request.task.startswith('Verify'):
                        return json.dumps({'verdicts': [{'index': 0, 'status': 'accepted', 'reason': 'Fixture keeps relative meaning undated.'}]})
                    return json.dumps({'source_coverage': {'status': 'accepted', 'reason': 'Relative meaning retained.', 'omissions': []}})
                self.assertNotIn('valid_at', dialogue.extract_dialogue(provider, event, [event])['assertions'][0])

    def test_late_cited_context_determines_knowledge_availability_and_historical_cutoff(self):
        event = self.source(producer_clock(timezone='Europe/Moscow'), position=1)
        self.now = datetime(2026, 3, 15, 12, tzinfo=timezone.utc)
        prior = self.queue.capture_event('fiction', 'prior', 'The local calendar was March 11, 2026.',
            source='fixture://prior', actor='assistant', position=0,
            metadata=producer_clock('2026-03-08T12:00:00Z', source='fixture://prior'))
        item = {'quote': BODY, 'text': BODY, 'valid_at': '2026-03-10', 'temporal_precision': 'day',
            'temporal_evidence': [], 'source_span': {'start': 0, 'end': len(BODY)},
            'evidence_refs': [{'event_id': event.id, 'start': 0, 'end': len(BODY)},
                              {'event_id': prior.id, 'start': 0, 'end': len(prior.prompt)}]}
        with self.store.transaction(write=True) as db:
            key = self.queue._publish_interpretation(db, event, item,
                {'context_event_ids': [prior.id, event.id], 'context_truncated': False}, {}, self.queue._time())
        assertion = self.store.get_assertion(key)
        self.assertEqual(assertion.recorded_at, '2026-03-15T12:00:00.000000+00:00')
        self.assertEqual(assertion.metadata['source_available_at'], assertion.recorded_at)
        self.assertEqual(assertion.metadata['source_recorded_at'], INGEST)
        self.assertEqual(assertion.metadata['source_message_at'], ORIGIN)
        self.assertEqual(self.store.list_assertions(recorded_as_of='2026-03-14'), [])
        self.assertEqual(self.store.list_assertions(recorded_as_of='2026-03-11'), [])

    def test_protocol_and_frozen_input_include_clock_contract(self):
        from prototypes.assertion_memory.source_reprocessing import PROTOCOL
        event = self.source(producer_clock())
        frozen = self.queue.freeze_input(event.id)
        self.assertIn('source-message-clock-v1', PROTOCOL)
        self.assertNotEqual(frozen.fingerprint, hashlib.sha256(b'').hexdigest())
        with patch('prototypes.assertion_memory.source_reprocessing.PROTOCOL', 'fictional-next-clock-contract'):
            self.assertNotEqual(frozen.fingerprint, self.queue.freeze_input(event.id).fingerprint)


class GenericMessageClockTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:'); self.addCleanup(self.store.close)
        self.service = MemoryService(self.store)
        self.service.queue._clock = lambda: datetime.fromisoformat(INGEST)
        self.args = ('fiction', 'message', 'Mira', 'user', BODY, 0, 'fixture://radio')

    def test_new_generic_optional_clock_canonicalizes_retry_without_backdating(self):
        first = self.service.capture_message(*self.args, source_message_at='2026-03-10T23:30:00Z', timezone='Europe/Moscow')
        second = self.service.capture_message(*self.args, source_message_at='2026-03-10T23:30:00+00:00', timezone='Europe/Moscow')
        omitted = self.service.capture_message(*self.args)
        self.assertEqual(first['event']['id'], second['event']['id'])
        self.assertEqual(first['event']['id'], omitted['event']['id'])
        event = self.service.queue.get_event(first['event']['id'])
        self.assertEqual(event.recorded_at, INGEST)
        self.assertEqual(event.metadata['source_message_at'], ORIGIN)
        self.assertEqual(event.metadata['source_message_clock_basis']['kind'], 'producer')
        with self.assertRaises(ValueError):
            self.service.capture_message(*self.args, source_message_at='2026-03-11T23:30:00Z')
        self.assertEqual(self.service.queue.get_event(event.id), event)

    def test_old_clockless_generic_receipt_is_reused_without_enrichment(self):
        first = self.service.capture_message(*self.args)
        old = self.service.queue.get_event(first['event']['id'])
        repeated = self.service.capture_message(*self.args, source_message_at=ORIGIN, timezone='Europe/Moscow')
        self.assertEqual(repeated['event']['id'], old.id)
        self.assertEqual(self.service.queue.get_event(old.id), old)
        self.assertNotIn('source_message_at', old.metadata)
        with self.assertRaises(ValueError): self.service.capture_message(*self.args[:-1], 'fixture://other', source_message_at=ORIGIN)

    def test_producer_cannot_claim_native_authentication_and_invalid_clock_is_rejected(self):
        for kwargs in ({'source_message_at': '2026-03-10'},
                       {'source_message_at': ORIGIN, 'source_message_clock_basis': {'kind': 'native-record', 'verified': True}},
                       {'source_message_at': ORIGIN, 'source_message_clock_basis': {}},
                       {'timezone': 'Mars/Olympus'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): self.service.capture_message(*self.args, **kwargs)
        self.assertEqual(self.service.queue.list_events(), [])

    def test_mcp_tool_exposes_optional_clock_arguments(self):
        from prototypes.assertion_memory.mcp_server import _register_write_tools
        class Registry:
            def __init__(self): self.functions = {}
            def tool(self):
                def register(fn): self.functions[fn.__name__] = fn; return fn
                return register
        registry = Registry(); _register_write_tools(registry, self.service, None, False)
        parameters = inspect.signature(registry.functions['memory_capture_message']).parameters
        for name in ('source_message_at', 'source_message_clock_basis', 'timezone'):
            self.assertIn(name, parameters)
            self.assertIsNone(parameters[name].default)

    def test_explicit_clock_basis_cannot_name_another_message_in_same_source(self):
        basis = {'kind': 'producer', 'field': 'sent_at', 'value': ORIGIN,
                 'source': self.args[-1], 'message_id': 'other-message'}
        with self.assertRaisesRegex(ValueError, 'message|identity'):
            self.service.capture_message(*self.args, source_message_at=ORIGIN,
                                         source_message_clock_basis=basis)
        self.assertEqual(self.service.queue.list_events(), [])
        basis['message_id'] = self.args[1]
        saved = self.service.capture_message(*self.args, source_message_at=ORIGIN,
                                            source_message_clock_basis=basis)
        event = self.service.queue.get_event(saved['event']['id'])
        self.assertEqual(event.metadata['source_message_clock_basis']['message_id'], self.args[1])


class LegacyTemporalAuditTest(unittest.TestCase):
    def test_new_protocol_withdraws_only_unsupported_absolute_date_preserving_relative_and_absolute_facts(self):
        from prototypes.assertion_memory.query_snapshot import capture, _restore_frozen
        from prototypes.assertion_memory.tests.test_source_reprocessing import SourceReprocessingTest
        fixture = SourceReprocessingTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.queue._clock = lambda: datetime.fromisoformat(INGEST)
        target = fixture.event(BODY, position=0)
        fixture.seed(target, BODY, key='old-relative-date')
        # Manufacture exactly the legacy unsafe date strengthening, without
        # claiming a real model ever produced it. Immutable before/after checks
        # below refer to this deliberately seeded fictional history.
        with fixture.store.transaction(write=True) as db:
            raw = db.execute('SELECT metadata FROM memory_assertions WHERE assertion_id=?', ('old-relative-date',)).fetchone()
            metadata = json.loads(raw['metadata'])
            metadata.update(temporal_precision='day', temporal_evidence=[{'event_id': target.id, 'start': 0, 'end': 9}])
            db.execute('UPDATE memory_assertions SET valid_at=?,metadata=? WHERE assertion_id=?',
                ('2026-03-12T00:00:00.000000+00:00', json.dumps(metadata), 'old-relative-date'))
        absolute = fixture.event('On March 9, 2026, I tested a fictional lamp.', turn='absolute', position=1)
        fixture.seed(absolute, absolute.prompt, key='absolute-fact')
        with fixture.store.transaction(write=True) as db:
            row = db.execute('SELECT metadata FROM memory_assertions WHERE assertion_id=?', ('absolute-fact',)).fetchone()
            metadata = json.loads(row['metadata'])
            start = absolute.prompt.index('March 9, 2026')
            metadata.update(temporal_precision='day', temporal_evidence=[{'event_id': absolute.id,
                'start': start, 'end': start + len('March 9, 2026')}])
            db.execute('UPDATE memory_assertions SET valid_at=?,metadata=? WHERE assertion_id=?',
                ('2026-03-09T00:00:00.000000+00:00', json.dumps(metadata), 'absolute-fact'))
        before_old = fixture.store.get_assertion('old-relative-date')
        before_absolute = fixture.store.get_assertion('absolute-fact')
        before_raw = fixture.queue.get_event(target.id)
        historic = capture(fixture.engine)
        fixture.queue._clock = lambda: datetime(2026, 3, 16, 12, tzinfo=timezone.utc)
        calls = []
        def provider(request):
            calls.append(request)
            raw = next(row for row in request.records if row.get('is_target'))
            self.assertEqual(raw['base_available_at'], INGEST)
            self.assertIsNone(raw['source_message_at'])
            self.assertIn('source_message_at', request.instructions)
            if request.task == 'Verify contextual assertion interpretations':
                return json.dumps({'verdicts': [{'index': row['index'],
                    'status': 'unsupported' if raw['id'] == target.id and row.get('valid_at') else 'accepted',
                    'reason': 'Fixture rejects only the absolute date invented from ingestion; relative meaning remains.'}
                    for row in request.records if row.get('role') == 'interpretation']})
            if request.task == 'Verify source interpretation withdrawals':
                hypothesis = next(row for row in request.records if row.get('role') == 'withdrawal_hypothesis')
                self.assertEqual(hypothesis['valid_at'], before_old.valid_at)
                return json.dumps({'withdrawals': [{'assertion_id': hypothesis['assertion_id'], 'status': 'accepted',
                    'reason': 'Exact relative-only source does not substantiate this absolute-date strengthening.',
                    'evidence': [{'event_id': target.id, 'quote': BODY}]}]})
            if request.task == 'Review source material coverage':
                meanings = [row for row in request.records if row.get('role') == 'interpretation']
                missing = raw['id'] == target.id and not meanings
                return json.dumps({'source_coverage': {'status': 'incomplete' if missing else 'accepted',
                    'reason': 'Keep the attributed relative meaning without fabricating its day.',
                    'omissions': [{'quote': BODY, 'reason': 'Relative report omitted.'}] if missing else []}})
            if request.task == 'Repair omitted source meanings':
                return json.dumps({'assertions': [{'text': BODY, 'quote': BODY, 'kind': 'observation'}]})
            raise AssertionError(request.task)
        worker = LinkWorker(fixture.queue, fixture.engine, provider)
        for _ in range(16):
            if worker.run_once() is None: break
        selected = capture(fixture.engine)
        self.assertNotIn('old-relative-date', selected.assertions)
        self.assertIn('absolute-fact', selected.assertions)
        replacement = next(row for row in fixture.store.list_assertions()
                           if row.assertion_id not in ('old-relative-date', 'absolute-fact'))
        self.assertIsNone(replacement.valid_at)
        self.assertEqual(fixture.store.get_assertion('old-relative-date'), before_old)
        self.assertEqual(fixture.store.get_assertion('absolute-fact'), before_absolute)
        self.assertEqual(fixture.queue.get_event(target.id), before_raw)
        self.assertEqual(asdict(_restore_frozen(fixture.engine, historic.descriptor())), asdict(historic))
        history = fixture.queue.extraction_history(target.id)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1]['source_revision']['status'], 'accepted_complete')
        with fixture.store.transaction() as db:
            self.assertEqual(db.execute('SELECT completed_at FROM memory_extraction_runs WHERE event_id=? ORDER BY id DESC LIMIT 1',
                (target.id,)).fetchone()[0], '2026-03-16T12:00:00.000000+00:00')
