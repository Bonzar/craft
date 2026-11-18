"""Synthetic structural regression; fixture verdicts test routing, not model intelligence.

Real-model date/meaning acceptance is a separate release gate. No private sources
or network/model invocations are used here.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from prototypes.assertion_memory.dialogue import (
    _context_records, _event_record, _normalize_claims, extract_dialogue)
from prototypes.assertion_memory.dialogue_context import ContextWindow
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker
from prototypes.assertion_memory.tests.test_dialogue_recovery import event


CLOCK = '2026-04-10T12:00:00.000000+00:00'
KNOWLEDGE = '2026-04-12T12:00:00.000000+00:00'
BODY = 'Yesterday I repaired a radio. Separately, last winter I worked at a harbor.'

def clock_metadata(source):
    return {'timezone': 'UTC', 'source_message_at': CLOCK,
            'source_message_clock_basis': {'kind': 'producer', 'field': 'sent_at',
                                           'value': CLOCK, 'source': source}}


class TemporalMetadataContextTest(unittest.TestCase):
    def setUp(self):
        self.target = replace(event('target', BODY, 'user'), recorded_at=KNOWLEDGE,
                              metadata=clock_metadata('dialogue://target'))
        self.calls = []

    def ref(self, quote, source=None, **extra):
        return {'event_id': (source or self.target).id, 'quote': quote, **extra}

    def candidate(self, source=None, *, quote='Yesterday', valid_at='2026-04-09'):
        source = source or self.target
        return {'text': 'The speaker repaired a radio yesterday.',
                'quote': 'Yesterday I repaired a radio.', 'kind': 'observation',
                'valid_at': valid_at,
                'temporal_evidence': [self.ref(quote, source), self.ref(source.metadata['source_message_at'], source)]}

    def normalize(self, values, context=None, *, spans=None):
        context = context or [self.target]
        records, _ = _context_records(self.target, context)
        window = ContextWindow(self.target, context, records, _event_record, budget=36_000)
        if spans is not None:
            window.spans = spans
        self.window = window
        return _normalize_claims(self.target, values, context, window.records(), window)

    def normalization_audits(self, result=None):
        audit = result['context_recovery'] if result else self.window.audit
        return [row for row in audit if row['operation'] == 'temporal_metadata_normalization']

    def provider(self, values, *, rejected=False):
        """Boundary fixture checks verifier payload; rejection is not a date algorithm."""
        def model(request):
            self.calls.append(request)
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': values})
            if request.task.startswith('Repair'):
                return json.dumps({'assertions': []})
            if request.task.startswith('Propose'):
                return json.dumps({'links': [], 'branches': []})
            claims = [row for row in request.records if row.get('role') == 'interpretation']
            if request.task == 'Review source material coverage':
                # Preserve both distinct meanings and honest unknown harbor date.
                complete = len(claims) == 2 and any('harbor' in row['text'] and 'valid_at' not in row
                                                   for row in claims)
                return json.dumps({'source_coverage': {'status': 'accepted' if complete else 'unresolved',
                    'reason': 'Both separate activities must survive with no invented harbor day.',
                    'omissions': []}})
            self.assertEqual(request.task, 'Verify contextual assertion interpretations')
            for row in claims:
                for ref in row.get('temporal_evidence', []):
                    source = next(record for record in request.records if record.get('id') == ref['event_id'])
                    left = ref['start'] - source['source_span']['start']
                    self.assertEqual(source['text'][left:left + len(ref['quote'])], ref['quote'])
                    self.assertEqual(source['recorded_at'], KNOWLEDGE)
                    self.assertEqual(source['source_message_at'], CLOCK)
                    self.assertEqual(source['timezone'], 'UTC')
            return json.dumps({'verdicts': [{'index': row['index'],
                'status': 'unsupported' if rejected else 'accepted',
                'reason': 'Independent fixture rejects date/attribution.' if rejected else
                          'Independent fixture accepts supplied interpretation.'} for row in claims]})
        return model

    def test_paired_metadata_is_removed_after_validation_and_actual_prose_is_preserved(self):
        value = self.candidate()
        original = deepcopy(value)
        claim = self.normalize([value])[0]
        self.assertEqual(value, original, 'attempted provider payload must remain unchanged')
        self.assertEqual(claim['valid_at'], '2026-04-09')
        self.assertEqual(claim['temporal_precision'], 'day')
        self.assertEqual(claim['temporal_evidence'], [{
            'event_id': self.target.id, 'quote': 'Yesterday', 'start': 0, 'end': 9,
            'role': 'user', 'relation': 'temporal'}])
        self.assertEqual(self.normalization_audits(), [{
            'operation': 'temporal_metadata_normalization', 'event_id': self.target.id,
            'field': 'source_message_at', 'count': 1,
            'value_sha256': hashlib.sha256(CLOCK.encode('utf-8')).hexdigest(),
            'source_message_clock_basis': self.target.metadata['source_message_clock_basis']}])
        self.assertFalse(any(ref['quote'] == CLOCK for ref in claim['evidence_refs']))

    def test_public_autocapture_keeps_two_meanings_real_proof_and_immutable_raw_history(self):
        with tempfile.TemporaryDirectory() as directory, MemoryStore(Path(directory) / 'toy.sqlite') as store:
            service = MemoryService(store)
            self.target = service.queue.capture_event('s', 't', BODY, source='synthetic://radio',
                scope='toy', recorded_at=KNOWLEDGE, metadata=clock_metadata('synthetic://radio'))
            before = service.queue.get_event(self.target.id)
            values = [self.candidate(), {'text': 'Separately, the speaker worked at a harbor last winter.',
                'quote': 'Separately, last winter I worked at a harbor.', 'kind': 'observation'}]
            LinkWorker(service.queue, service.engine, self.provider(values)).drain()
            status = service.status(self.target.id, scope='toy')
            self.assertEqual(status['error_count'], 0)
            self.assertEqual(status['pending_count'], 0)
            self.assertEqual(status['unresolved_count'], 0)
            self.assertEqual(service.queue.get_event(self.target.id), before)
            assertions = store.list_assertions()
            self.assertEqual(len(assertions), 2)
            radio = next(row for row in assertions if 'radio' in row.text)
            harbor = next(row for row in assertions if 'harbor' in row.text)
            self.assertEqual(radio.valid_at, '2026-04-09T00:00:00.000000+00:00')
            self.assertIsNone(harbor.valid_at)
            self.assertEqual(radio.metadata['interpretation']['source_coverage']['status'], 'accepted')
            ref = radio.metadata['temporal_evidence'][0]
            self.assertEqual(service.queue.resolve_reference(ref)['text'], 'Yesterday')
            self.assertNotIn('quote', ref)
            for row in assertions:
                for proof in row.metadata['evidence_refs']:
                    self.assertNotEqual(service.queue.resolve_reference(proof)['text'], CLOCK)
            source = service.source(self.target.id)
            history = source['extraction_history']
            self.assertEqual(len(history), 1)
            self.assertEqual(len(history[0]['assertion_ids']), 2)
            self.assertEqual(len(self.normalization_audits(history[0])), 1)
            self.assertTrue(any(call.task == 'Review source material coverage' for call in self.calls))

    def test_metadata_only_and_cross_source_pairing_fail(self):
        other = replace(self.target, id='other', prompt='Yesterday I checked a clock.')
        for refs in ([self.ref(CLOCK)],
                     [self.ref('Yesterday'), self.ref(CLOCK, other)]):
            with self.subTest(refs=refs), self.assertRaises(ModelError):
                self.normalize([{**self.candidate(), 'temporal_evidence': refs}], [other, self.target])
            self.assertEqual(self.normalization_audits(), [])

    def test_arbitrary_bad_quotes_and_explicit_start_are_not_discarded(self):
        for bad in (self.ref(CLOCK.replace('.000000', '')), self.ref('invented temporal quote'),
                    self.ref(CLOCK, start=0), self.ref(CLOCK, start=None), self.ref(CLOCK, start=False)):
            with self.subTest(bad=bad), self.assertRaises(ModelError):
                self.normalize([{**self.candidate(), 'temporal_evidence': [self.ref('Yesterday'), bad]}])
            self.assertEqual(self.normalization_audits(), [])
        with self.assertRaises(ModelError):
            self.normalize([{**self.candidate(), 'evidence': [self.ref(CLOCK)]}])
        self.assertEqual(self.normalization_audits(), [])

    def test_valid_redundant_item_does_not_hide_bad_reference_or_batch_claim(self):
        for bad in (self.ref('fabricated'), self.ref(CLOCK, start=None), self.ref('Yesterday', event_id='absent')):
            with self.subTest(bad=bad), self.assertRaises(ModelError):
                self.normalize([{**self.candidate(),
                    'temporal_evidence': [*self.candidate()['temporal_evidence'], bad]}])
            self.assertEqual(self.normalization_audits(), [])
        with self.assertRaises(ModelError):
            self.normalize([self.candidate(), {'text': 'Bad second claim.', 'quote': 'invented'}])
        self.assertEqual(self.normalization_audits(), [])

    def test_unauthorized_unsupplied_and_foreign_anchors_reject(self):
        prior = replace(self.target, id='prior', prompt='Yesterday I checked a clock.')
        foreign = replace(prior, session_id='foreign')
        for context, source, spans in (([foreign, self.target], foreign, None),
                ([self.target], prior, None),
                ([prior, self.target], prior, {self.target.id: [(0, len(BODY))]}),
                ([prior, self.target], prior, {self.target.id: [(0, len(BODY))], prior.id: [(10, 28)]})):
            with self.subTest(context=context, spans=spans), self.assertRaises(ModelError):
                self.normalize([self.candidate(source)], context, spans=spans)
            self.assertEqual(self.normalization_audits(), [])

    def test_authorized_displayed_earlier_anchor_can_pair_with_its_clock(self):
        prior = replace(self.target, id='prior', prompt='Yesterday I checked a clock.')
        claim = self.normalize([self.candidate(prior)], [prior, self.target])[0]
        self.assertEqual(claim['temporal_evidence'][0]['event_id'], prior.id)
        self.assertEqual(self.normalization_audits()[0]['event_id'], prior.id)

    def test_target_anchor_outside_supplied_chunk_rejects_without_audit(self):
        self.target = replace(self.target, prompt='The radio was repaired. ' + 'filler ' * 6000 + 'Yesterday')
        value = {**self.candidate(), 'quote': 'The radio was repaired.'}
        with self.assertRaisesRegex(ModelError, 'actually supplied'):
            self.normalize([value])
        self.assertEqual(self.normalization_audits(), [])

    def test_timestamp_literal_in_body_is_preserved_with_and_without_start(self):
        self.target = replace(self.target, prompt=BODY + ' Printed timestamp: ' + CLOCK)
        for extra in ({}, {'start': self.target.prompt.index(CLOCK)}, {'start': None}):
            with self.subTest(extra=extra):
                value = self.candidate()
                value['temporal_evidence'][-1].update(extra)
                claim = self.normalize([value])[0]
                ref = claim['temporal_evidence'][-1]
                self.assertEqual(ref['quote'], CLOCK)
                self.assertEqual(self.target.prompt[ref['start']:ref['end']], CLOCK)
                self.assertEqual(self.normalization_audits(), [])

    def test_wrong_date_ambiguous_time_and_wrong_attribution_remain_verifier_rejections(self):
        for body, anchor, date in ((BODY, 'Yesterday', '2026-04-10'),
                ('I may do this sometime.', 'I may do this sometime', '2026-04-09'),
                ('Someone else said yesterday they repaired a radio.', 'yesterday', '2026-04-09')):
            with self.subTest(body=body):
                self.target = replace(self.target, prompt=body)
                value = {**self.candidate(quote=anchor, valid_at=date), 'quote': body}
                result = extract_dialogue(self.provider([value], rejected=True), self.target, [self.target])
                self.assertEqual(result['assertions'], [])
                self.assertEqual(len(self.normalization_audits(result)), 1)
                reviews = [row for row in result['reviews'] if row['kind'] == 'interpretation']
                self.assertEqual(reviews[0]['status'], 'unsupported')
                self.assertTrue(any(call.task.startswith('Verify') for call in self.calls))


if __name__ == '__main__':
    unittest.main()
