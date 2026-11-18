"""Source-later self-corrections are distinct from later effective rule changes."""
import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import Assertion, MemoryStore
from prototypes.assertion_memory.worker import LinkWorker, _propose


def claim(key, text, *, kind='observation', valid='2026-11-05', recorded='2026-11-10', actor='user'):
    stamp = lambda value: value + 'T12:00:00.000000+00:00'
    return Assertion(key, text, actor, 'test://' + key, 's', stamp(valid), stamp(recorded), 'lighting', {
        'kind': kind, 'excerpt': text, 'source_available_at': stamp(recorded),
        'source_recorded_at': stamp(recorded),
        'interpretation': {'status': 'model_verified', 'interpreted_at': stamp(recorded)},
        'evidence_refs': [{'event_id': 'event-' + key, 'start': 0, 'end': len(text),
                           'quote': text, 'role': 'user', 'relation': 'statement'}],
    })


class RetroactiveCorrectionTest(unittest.TestCase):
    def setUp(self):
        self.old = claim('old', 'Проверил 5 ноября: лампа горела.')
        self.new = claim('new', 'Исправляю свою запись: проверка была 3 ноября, и лампа была выключена.',
                         valid='2026-11-03', recorded='2026-11-20')
        self.requests = []

    def propose(self, source=None, target=None, *, status='accepted', condition=None, proof=True,
                source_proof=None, target_proof=None):
        source, target = source or self.new, target or self.old
        def model(request):
            self.requests.append(request)
            if request.task.startswith('Propose'):
                return json.dumps({'links': [{'target_id': target.assertion_id, 'kind': 'supersedes',
                    'source_quote': source.text, 'target_quote': target.text, 'condition': condition}]})
            verdict = {'index': 0, 'status': status, 'reason': 'The later source explicitly corrects the same reported event.'}
            if proof:
                verdict.update(basis='self_correction', correction_evidence={
                    'source_quote': source_proof or source.text, 'target_quote': target_proof or target.text})
            return json.dumps({'verdicts': [verdict]})
        return _propose(model, source, {target.assertion_id: target}, {})[0]

    def test_corrected_earlier_effective_date_requires_verified_exact_self_correction(self):
        result = self.propose()
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['basis'], 'self_correction')
        proof = result['verification']['correction_evidence']
        self.assertEqual(proof['source']['event_id'], 'event-new')
        self.assertEqual(proof['target']['event_id'], 'event-old')
        self.assertEqual(self.new.text[proof['source']['start']:proof['source']['end']], self.new.text)
        self.assertNotIn('quote', proof['source'])
        verifier = self.requests[-1]
        records = [row for row in verifier.records if row.get('role') == 'claim']
        self.assertEqual(records[0]['valid_at'], self.new.valid_at)
        self.assertEqual(records[0]['metadata']['source_available_at'], self.new.recorded_at)

    def test_same_effective_date_decision_correction_can_replace_later_learned_record(self):
        old = claim('old', 'С 5 ноября согласовали вариант A.', kind='decision')
        new = claim('new', 'Поправляю свою запись: с 5 ноября согласовали вариант B, вариант A указан ошибочно.',
                    kind='decision', recorded='2026-11-20')
        result = self.propose(new, old)
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['basis'], 'self_correction')

    def test_sequential_observations_are_not_corrections(self):
        ordinary = claim('new', 'Проверил 6 ноября: лампа выключена.', valid='2026-11-06', recorded='2026-11-20')
        result = self.propose(ordinary, status='unsupported')
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['verification']['status'], 'unsupported')
        self.assertIn('same past event', self.requests[-1].instructions)

    def test_late_report_of_different_older_event_is_not_a_self_correction(self):
        ordinary = claim('new', 'Проверил 3 ноября другую лампу: она выключена.', valid='2026-11-03', recorded='2026-11-20')
        result = self.propose(ordinary, status='unsupported')
        self.assertEqual(result['status'], 'rejected')

    def test_cross_actor_proposal_inference_and_same_availability_remain_blocked(self):
        for source in (replace(self.new, actor='other'),
                       claim('new', self.new.text, kind='proposal', recorded='2026-11-20'),
                       claim('new', self.new.text, kind='inference', recorded='2026-11-20'),
                       claim('new', self.new.text, valid='2026-11-03', recorded='2026-11-10')):
            with self.subTest(source=source):
                self.requests.clear()
                result = self.propose(source)
                self.assertEqual(result['status'], 'pending')
                self.assertFalse(any(request.task == 'Verify semantic evidence links' for request in self.requests))

    def test_generic_acceptance_or_fabricated_raw_proof_cannot_authorize_correction(self):
        for kwargs in ({'proof': False}, {'source_proof': 'Fabricated correction'}, {'target_proof': 'Fabricated old event'}):
            with self.subTest(kwargs=kwargs):
                result = self.propose(**kwargs)
                self.assertEqual(result['status'], 'pending')
                self.assertEqual(result['verification']['status'], 'unresolved')

    def test_partial_scope_omission_is_rejected_but_exact_condition_can_be_retained(self):
        source = claim('new', 'Поправляю своё сообщение только для лампы у двери: 5 ноября она была выключена.',
                       recorded='2026-11-20')
        broad = claim('old', 'Проверил 5 ноября: обе лампы горели.')
        self.assertEqual(self.propose(source, broad, status='unsupported')['status'], 'rejected')
        corrected = self.propose(source, broad, condition='только для лампы у двери')
        self.assertEqual(corrected['status'], 'eligible')
        self.assertEqual(corrected['condition'], 'только для лампы у двери')
        with MemoryStore(':memory:') as store:
            queue = CaptureQueue(store, clock=lambda: datetime(2026, 11, 21, tzinfo=timezone.utc))
            for row in (broad, source):
                queue.capture(CaptureInput(row.text, row.actor, row.source, row.episode,
                    assertion_id=row.assertion_id, scope=row.scope, valid_at=row.valid_at,
                    recorded_at=row.recorded_at, metadata=row.metadata))
            engine = MemoryEngine(store)
            LinkWorker(queue, engine)._apply(corrected)
            engine.define_view(ViewSpec('lamps', 'Lamps', 'Лампы', scopes=('lighting',), root_ids=('old', 'new')))
            version = engine.build_view('lamps', valid_as_of='2026-11-06', recorded_as_of='2026-11-22')
            prior = next(row for row in version.evidence if row['id'] == 'old')
            self.assertNotEqual(prior['disposition'], 'superseded')
            self.assertEqual(version.edges[0]['metadata']['condition'], 'только для лампы у двери')

    def test_proof_must_use_raw_actor_statement_not_normalization_or_assistant_context(self):
        raw = 'Поправка: ошибся датой и состоянием лампы.'
        metadata = {**self.new.metadata, 'event_id': 'event-new', 'evidence_refs': [
            {'event_id': 'event-new', 'start': 10, 'end': 10 + len(raw), 'quote': raw,
             'role': 'user', 'relation': 'statement'},
            {'event_id': 'assistant', 'start': 0, 'end': len(self.new.text), 'quote': self.new.text,
             'role': 'assistant', 'relation': 'context'}]}
        source = replace(self.new, metadata=metadata)
        invalid = self.propose(source)
        self.assertEqual(invalid['status'], 'pending')
        accepted = self.propose(source, source_proof=raw)
        self.assertEqual(accepted['status'], 'eligible')
        anchor = accepted['verification']['correction_evidence']['source']
        self.assertEqual(anchor['start'], 10)
        self.assertEqual(anchor['end'], 10 + len(raw))

    def test_current_correction_and_historical_prior_report_keep_both_raw_sources(self):
        with MemoryStore(':memory:') as store:
            now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
            queue = CaptureQueue(store, clock=lambda: now)
            old_event = queue.capture_event('s', 'old', self.old.text, source=self.old.source)
            now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
            new_event = queue.capture_event('s', 'new', self.new.text, source=self.new.source)
            with store.transaction(write=True) as connection:
                connection.execute("UPDATE memory_capture_jobs SET status='completed', phase='done'")
            for row, event in ((self.old, old_event), (self.new, new_event)):
                metadata = {**row.metadata, 'evidence_refs': [{'event_id': event.id, 'start': 0,
                            'end': len(event.prompt), 'role': 'user', 'relation': 'statement'}]}
                queue.capture(CaptureInput(row.text, row.actor, row.source, row.episode,
                    assertion_id=row.assertion_id, scope=row.scope, valid_at=row.valid_at,
                    recorded_at=row.recorded_at, metadata=metadata))
            engine = MemoryEngine(store)
            engine.define_view(ViewSpec('lighting', 'Lighting', 'Лампа', scopes=('lighting',), root_ids=('old', 'new')))
            before = engine.build_view('lighting', valid_as_of='2026-11-06', recorded_as_of='2026-11-15')
            def model(request):
                if request.task.startswith('Propose'):
                    source = request.records[0]
                    if source['id'] != 'new':
                        return '{"links":[]}'
                    return json.dumps({'links': [{'target_id': 'old', 'kind': 'supersedes',
                                                 'source_quote': self.new.text, 'target_quote': self.old.text}]})
                return json.dumps({'verdicts': [{'index': 0, 'status': 'accepted', 'reason': 'Exact raw self-correction.',
                    'basis': 'self_correction', 'correction_evidence': {
                        'source_quote': self.new.text, 'target_quote': self.old.text}}]})
            LinkWorker(queue, engine, model).drain()
            edges = store.list_edges()
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].metadata['basis'], 'self_correction')
            after = engine.build_view('lighting', valid_as_of='2026-11-06', recorded_as_of='2026-11-21')
            dispositions = {row['id']: row['disposition'] for row in after.evidence}
            self.assertEqual(dispositions['old'], 'superseded')
            self.assertNotEqual(dispositions['new'], 'superseded')
            self.assertEqual(engine.get_version(before.id), before)
            known_before = engine.build_view('lighting', valid_as_of='2026-11-06', recorded_as_of='2026-11-15')
            self.assertEqual(known_before.id, before.id)
            self.assertEqual({row['id'] for row in known_before.evidence}, {'old'})
            self.assertEqual(queue.get_event(old_event.id).prompt, self.old.text)
            self.assertEqual(queue.get_event(new_event.id).prompt, self.new.text)


if __name__ == '__main__':
    unittest.main()
