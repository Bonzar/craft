"""Reports of adopted policy need independently verified, immutable source proof."""
import copy
import json
import unittest
from dataclasses import replace

from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.tests.test_semantic_links import assertion
from prototypes.assertion_memory.worker import LinkWorker, _propose


def report(key, text, *, actor='reporter', day=2, kind='assertion'):
    row = assertion(key, text, actor=actor, day=day, kind=kind)
    return replace(row, metadata={**row.metadata, 'event_id': 'event-' + key,
                                 'source_available_at': row.recorded_at})


def span(row):
    ref = row.metadata['evidence_refs'][0]
    return {key: ref[key] for key in ('event_id', 'start', 'end')}


class HistoricalPolicyLinksTest(unittest.TestCase):
    def setUp(self):
        self.old = report('old', 'On January 1 the safety council adopted a ban on Atlas deliveries.',
                          actor='Mira', day=1)
        self.new = report('new', 'On January 2 the safety council adopted an exception for medical deliveries.',
                          actor='Ren', day=2)
        self.condition = 'for medical deliveries'
        self.requests = []

    def propose(self, source=None, target=None, *, kind='exception_to', status='accepted',
                proof=True, evidence=None, basis='reported_decision', condition='default',
                resolver=None):
        source, target = source or self.new, target or self.old
        condition = self.condition if condition == 'default' and kind == 'exception_to' else (
            None if condition == 'default' else condition)
        def model(request):
            self.requests.append(request)
            if request.task.startswith('Propose'):
                return json.dumps({'links': [{'target_id': target.assertion_id, 'kind': kind,
                    'source_quote': source.text, 'target_quote': target.text, 'condition': condition}]})
            verdict = {'index': 0, 'status': status, 'reason': 'Independent assessment of actual adopting authority and policy event.'}
            if proof:
                verdict.update(basis=basis, decision_evidence=evidence if evidence is not None else {
                    'source_refs': [span(source)], 'target_refs': [span(target)]})
            return json.dumps({'verdicts': [verdict]})
        return _propose(model, source, {target.assertion_id: target}, {}, resolve_reference=resolver)[0]

    def test_different_reporters_of_same_adopting_council_reach_verifier(self):
        result = self.propose()
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['basis'], 'reported_decision')
        proof = result['verification']['decision_evidence']
        self.assertEqual(proof['source_refs'][0]['event_id'], 'event-new')
        self.assertEqual(proof['target_refs'][0]['relation'], 'statement')
        self.assertEqual(self.new.metadata['kind'], 'assertion')
        self.assertEqual(self.new.actor, 'Ren')
        self.assertIn('actual adopting authority', self.requests[-1].instructions)
        self.assertIn('event time', self.requests[-1].instructions)

    def test_reported_revision_refines_candidate_self_correction_basis(self):
        source = report('new', 'On January 2 the safety council adopted Atlas trains in place of trucks.',
                        actor='Mira')
        result = self.propose(source, kind='supersedes')
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['basis'], 'reported_decision')
        self.assertEqual(result['proposed_basis'], 'self_correction')
        self.assertEqual(result['verification']['basis'], 'reported_decision')

    def test_report_can_reference_a_raw_decision_with_source_spans(self):
        metadata = {key: value for key, value in self.old.metadata.items() if key != 'interpretation'}
        target = replace(self.old, metadata={**metadata, 'kind': 'decision'})
        result = self.propose(target=target)
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['verification']['basis'], 'reported_decision')

    def test_persisted_proof_keeps_source_spans_without_copying_raw_bodies(self):
        with MemoryStore(':memory:') as store:
            queue = CaptureQueue(store)
            events = [queue.capture_event('s', row.assertion_id, row.text, source=row.source)
                      for row in (self.old, self.new)]
            with store.transaction(write=True) as connection:
                connection.execute("UPDATE memory_capture_jobs SET status='completed', phase='done'")
            rows = []
            for row, event in zip((self.old, self.new), events):
                metadata = {**row.metadata, 'event_id': event.id, 'evidence_refs': [{
                    'event_id': event.id, 'start': 0, 'end': len(row.text),
                    'role': 'user', 'relation': 'statement'}]}
                rows.append(replace(row, metadata=metadata))
                queue.capture(CaptureInput(row.text, row.actor, row.source, row.episode,
                    assertion_id=row.assertion_id, scope=row.scope, recorded_at=row.recorded_at,
                    metadata=metadata))
            proposal = self.propose(rows[1], rows[0], resolver=queue.resolve_reference)
            LinkWorker(queue, MemoryEngine(store))._apply(proposal)
            edge = store.list_edges()[0]
            self.assertEqual(edge.metadata['basis'], 'reported_decision')
            self.assertEqual(edge.metadata['proposed_basis'], 'reported_decision')
            proof = edge.metadata['verification']['decision_evidence']
            for key, event in zip(('source_refs', 'target_refs'), reversed(events)):
                ref = proof[key][0]
                self.assertEqual(set(ref), {'event_id', 'start', 'end', 'role', 'relation'})
                self.assertEqual(queue.resolve_reference(ref)['text'], event.prompt)
            claim_records = [record for record in self.requests[-1].records if record.get('role') == 'claim']
            self.assertEqual(claim_records[0]['metadata']['evidence_refs'][0]['text'], self.new.text)

    def test_compact_proof_validation_is_reusable_without_raw_source_copies(self):
        from prototypes.assertion_memory.link_proof import reported_decision_proof
        result = self.propose()
        records = []
        for row in (self.new, self.old):
            metadata = copy.deepcopy(row.metadata)
            for ref in metadata['evidence_refs']:
                ref.pop('quote')
            records.append({'metadata': metadata})
        proof = reported_decision_proof(*records, result['verification'], resolved=False)
        self.assertEqual(proof['basis'], 'reported_decision')
        forged = copy.deepcopy(result['verification'])
        forged['decision_evidence']['source_refs'][0]['role'] = 'assistant'
        self.assertIsNone(reported_decision_proof(*records, forged, resolved=False))
        forged = copy.deepcopy(result['verification'])
        forged['decision_evidence']['target_refs'][0]['end'] += 1
        self.assertIsNone(reported_decision_proof(*records, forged, resolved=False))

    def test_other_reporter_adopted_revision_reaches_independent_verifier(self):
        source = report('new', 'On January 2 the safety council adopted Atlas trains in place of trucks.')
        result = self.propose(source, kind='supersedes')
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['proposed_basis'], 'reported_decision')

    def test_generic_acceptance_cannot_adopt_a_report(self):
        result = self.propose(proof=False)
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['verification']['status'], 'unresolved')

    def test_forged_outside_reversed_and_boolean_spans_are_unresolved(self):
        for invalid in ({'event_id': 'made-up', 'start': 0, 'end': 8},
                        {'event_id': 'event-new', 'start': 0, 'end': 10000},
                        {'event_id': 'event-new', 'start': 5, 'end': 2},
                        {'event_id': 'event-new', 'start': False, 'end': 8},
                        {'event_id': 'event-old', 'start': 0, 'end': 8}):
            with self.subTest(invalid=invalid):
                result = self.propose(evidence={'source_refs': [invalid], 'target_refs': [span(self.old)]})
                self.assertEqual(result['status'], 'pending')
                self.assertEqual(result['verification']['status'], 'unresolved')

    def test_role_and_relation_are_inherited_not_model_supplied(self):
        evidence = {'source_refs': [{**span(self.new), 'role': 'assistant', 'relation': 'antecedent'}],
                    'target_refs': [span(self.old)]}
        result = self.propose(evidence=evidence)
        proof = result['verification']['decision_evidence']['source_refs'][0]
        self.assertEqual(proof['role'], 'user')
        self.assertEqual(proof['relation'], 'statement')

    def test_both_endpoints_require_nonassistant_statement_anchor(self):
        for endpoint in ('source', 'target'):
            for role, relation in (('assistant', 'statement'), ('user', 'antecedent')):
                with self.subTest(endpoint=endpoint, role=role, relation=relation):
                    row = self.new if endpoint == 'source' else self.old
                    metadata = copy.deepcopy(row.metadata)
                    metadata['evidence_refs'][0].update(role=role, relation=relation)
                    altered = replace(row, metadata=metadata)
                    kwargs = {'source': altered} if endpoint == 'source' else {'target': altered}
                    result = self.propose(**kwargs)
                    self.assertEqual(result['status'], 'pending')
                    self.assertEqual(result['verification']['status'], 'unresolved')

    def test_short_confirmation_can_include_antecedent_but_never_replace_own_statement(self):
        raw = 'Yes, except for medical deliveries.'
        context = 'Did the safety council adopt the Atlas deliveries ban on January 1?'
        source = replace(self.new, metadata={**self.new.metadata, 'evidence_refs': [
            {'event_id': 'event-new', 'start': 8, 'end': 8 + len(raw), 'quote': raw,
             'role': 'user', 'relation': 'statement'},
            {'event_id': 'question', 'start': 0, 'end': len(context), 'quote': context,
             'role': 'assistant', 'relation': 'antecedent'}]})
        evidence = {'source_refs': [span(source), {'event_id': 'question', 'start': 0, 'end': len(context)}],
                    'target_refs': [span(self.old)]}
        accepted = self.propose(source, evidence=evidence)
        self.assertEqual(accepted['status'], 'eligible')
        refs = accepted['verification']['decision_evidence']['source_refs']
        self.assertEqual(refs[0]['start'], 8)
        self.assertEqual(refs[1]['role'], 'assistant')
        evidence['source_refs'].pop(0)
        self.assertEqual(self.propose(source, evidence=evidence)['status'], 'pending')

    def test_wrong_authority_subject_time_missing_adoption_or_condition_can_be_rejected(self):
        for text in ('Another council permits Atlas medical deliveries.',
                     'The safety council permits medical deliveries for Boreal.',
                     'A later reporter repeats the old Atlas ban.',
                     'The safety council discussed permitting medical deliveries.',
                     'The safety council permits medical deliveries only during the pilot.'):
            with self.subTest(text=text):
                source = report('new', text)
                # Relation semantics belong to the independent model boundary.
                result = self.propose(source, status='unsupported', condition=None)
                self.assertEqual(result['status'], 'rejected')
                self.assertEqual(result['verification']['status'], 'unsupported')

    def test_proposal_and_inference_never_enter_reported_decision_path(self):
        for kind in ('proposal', 'inference'):
            with self.subTest(kind=kind):
                source = report('new', self.new.text, kind=kind)
                self.requests.clear()
                result = self.propose(source)
                self.assertEqual(result['status'], 'pending')
                self.assertEqual(len(self.requests), 1)


if __name__ == '__main__':
    unittest.main()
