"""Fictional contract probes for bounded material referent closure.

Fixtures deliberately start with a true, incomplete extraction. They exercise
independent coverage, exact repair evidence and entailment; they do not certify
real-model semantic obedience (that requires a fresh provider gate).
"""
import json
import unittest

from prototypes.assertion_memory.dialogue import extract_dialogue
from prototypes.assertion_memory.model import format_prompt
from prototypes.assertion_memory.tests.test_dialogue_recovery import event


class ContextualReferentClosureTest(unittest.TestCase):
    def run_case(self, original, qualifier, *, reply='Yes, use that late slot.',
                 prefix='I propose the late slot.', relation='reason',
                 meaning=None, kind='decision', reject_repair=False):
        proposal = event('proposal', original)
        target = event('target', reply, 'user')
        unrelated = event('unrelated', 'I also propose a separate room booking because it has a window.')
        base = {'text': 'The user chose the proposed late slot.' if kind == 'decision' else
                'The user asked about the proposed late slot.', 'quote': reply, 'kind': kind,
                'evidence': [{'event_id': proposal.id, 'quote': prefix, 'relation': 'antecedent'}]}
        closed = {'text': meaning or ('The user chose the late slot; the assistant attached this ' + relation + ': ' + qualifier),
                  'quote': reply, 'kind': kind, 'evidence': [*base['evidence'],
                      {'event_id': proposal.id, 'quote': qualifier, 'relation': relation}]}
        calls = []
        def provider(request):
            calls.append(request)
            self.assertLessEqual(len(format_prompt(request)), 60_000)
            if request.task.startswith('Extract'):
                # Deliberate first-pass omission, regardless of improved extractor instructions.
                return json.dumps({'assertions': [base]})
            if request.task == 'Review source material coverage':
                present = any(r.get('role') == 'interpretation' and r['text'] == closed['text'] for r in request.records)
                # Simulates the old target-only coverage contract vs the new material
                # referent obligation; source semantics are fixed by this toy case.
                required = 'Material referent closure' in request.instructions
                return json.dumps({'source_coverage': {'status': 'incomplete' if required and not present else 'accepted',
                    'reason': 'The selected referent includes separately attributed material meaning.',
                    'omissions': [{'quote': reply, 'reason': 'Target reference loses the proposer-stated qualification.'}]
                                 if required and not present else []}})
            if request.task == 'Repair omitted source meanings':
                return json.dumps({'assertions': [closed]})
            if request.task.startswith('Verify'):
                verdicts = []
                for r in request.records:
                    if r.get('role') != 'interpretation':
                        continue
                    allowed = r['text'] == base['text'] or (r['text'] == closed['text'] and not reject_repair)
                    # Independent truth check uses supplied exact body spans, not critique.
                    allowed &= all(any(s.get('id') == ref['event_id'] and s.get('source_span', {}).get('start', -1) <= ref['start']
                        and ref['end'] <= s.get('source_span', {}).get('end', -1)
                        and s['text'][ref['start']-s['source_span']['start']:ref['end']-s['source_span']['start']] == ref['quote']
                        for s in request.records if 'source_span' in s) for ref in r['evidence_refs'])
                    verdicts.append({'index': r['index'], 'status': 'accepted' if allowed else 'unsupported',
                                     'reason': 'Check exact speech attribution and source evidence independently.'})
                return json.dumps({'verdicts': verdicts})
            raise AssertionError(request.task)
        result = extract_dialogue(provider, target, [proposal, unrelated, target])
        return result, calls, proposal, target, base, closed

    def test_true_first_extraction_is_incomplete_and_repaired_with_exact_attributed_reason(self):
        reason = 'Morning access is reserved for inspection.'
        result, calls, proposal, target, base, closed = self.run_case('I propose the late slot. ' + reason, reason)
        self.assertEqual([a['text'] for a in result['assertions']], [base['text'], closed['text']])
        audits = [r for r in result['context_recovery'] if r['operation'] == 'source_coverage']
        self.assertEqual([r['status'] for r in audits], ['unresolved', 'accepted'])
        ref = result['assertions'][1]['evidence_refs'][-1]
        self.assertEqual((ref['role'], ref['relation']), ('assistant', 'reason'))
        self.assertEqual(proposal.prompt[ref['start']:ref['end']], reason)
        self.assertEqual(result['assertions'][1]['quote'], target.prompt)
        self.assertTrue(any(r.task.startswith('Verify') and any(a.get('text') == closed['text'] for a in r.records) for r in calls))
        for task in ('Extract attributable assertions from the user event', 'Verify contextual assertion interpretations', 'Review source material coverage'):
            self.assertTrue(all('Material referent closure' in r.instructions for r in calls if r.task == task))

    def test_reason_before_option_and_separate_sentence_are_preserved(self):
        for original, reason in [('Access opens late. I propose the late slot.', 'Access opens late.'),
                                 ('I propose the late slot. My reason is the morning allocation.', 'My reason is the morning allocation.')]:
            with self.subTest(original=original):
                result, _, _, _, _, closed = self.run_case(original, reason)
                self.assertIn(closed['text'], [a['text'] for a in result['assertions']])

    def test_shared_definition_safety_condition_and_reported_uncertainty_are_preserved(self):
        for qualifier, relation in [('Late means after the inspection.', 'definition'),
                                   ('Only if the access check passes.', 'qualification'),
                                   ('Noor says morning access may be reserved; I have not checked.', 'reason')]:
            with self.subTest(relation=relation, qualifier=qualifier):
                result, _, _, _, _, closed = self.run_case('I propose the late slot. ' + qualifier, qualifier, relation=relation)
                self.assertIn(closed['text'], [a['text'] for a in result['assertions']])
                self.assertEqual(result['assertions'][-1]['evidence_refs'][-1]['relation'], relation)

    def test_adopted_action_with_disputed_reason_preserves_disagreement(self):
        reason = 'I think morning access is reserved.'
        meaning = 'The user chose the late slot but disputes the reason; the assistant had said it thinks morning access is reserved.'
        result, _, _, _, _, _ = self.run_case('I propose the late slot. ' + reason, reason,
            reply='Use the proposed late slot, but I dispute your reason.', meaning=meaning)
        self.assertEqual(result['assertions'][-1]['text'], meaning)

    def test_referred_proposal_question_stays_question_without_adoption(self):
        reason = 'My reason is the morning allocation.'
        meaning = 'The user asked whether the proposed late slot is necessary; the assistant had given the morning allocation as its reason.'
        result, _, _, _, _, _ = self.run_case('I propose the late slot. ' + reason, reason,
            reply='Should we use that proposed slot?', kind='assertion', meaning=meaning)
        self.assertEqual(result['assertions'][-1]['kind'], 'assertion')
        self.assertEqual(result['assertions'][-1]['text'], meaning)

    def test_unrelated_adjacent_proposal_is_not_canonicalized(self):
        result, _, _, _, _, closed = self.run_case('I propose the late slot. Access opens late.', 'Access opens late.')
        self.assertEqual(len(result['assertions']), 2)
        self.assertEqual(result['assertions'][-1]['text'], closed['text'])
        self.assertTrue(all(ref['event_id'] != 'unrelated' for a in result['assertions'] for ref in a['evidence_refs']))
        assistant = event('unendorsed', 'I propose another option because of the light.')
        self.assertEqual(extract_dialogue(lambda r: self.fail('Assistant context must not be extracted'), assistant, [assistant])['assertions'], [])

    def test_unaccepted_repair_never_becomes_fact_and_coverage_remains_unresolved(self):
        result, calls, _, _, base, _ = self.run_case('I propose the late slot. Access opens late.', 'Access opens late.', reject_repair=True)
        self.assertEqual([a['text'] for a in result['assertions']], [base['text']])
        self.assertEqual(result['assertions'][0]['interpretation']['source_coverage']['status'], 'unresolved')
        self.assertEqual(sum(r.task == 'Repair omitted source meanings' for r in calls), 2)

    def test_coverage_omission_uses_target_reference_not_context_quote(self):
        result, calls, _, target, _, _ = self.run_case('I propose the late slot. Access opens late.', 'Access opens late.')
        repair = next(r for r in calls if r.task == 'Repair omitted source meanings')
        omission = next(r for r in repair.records if r.get('role') == 'source_omission')
        self.assertEqual(omission['quote'], target.prompt)
        self.assertEqual(omission['event_id'], target.id)
        self.assertEqual(result['assertions'][-1]['interpretation']['source_coverage']['status'], 'accepted')

    def test_missing_or_ambiguous_antecedent_stays_unresolved_under_existing_budget(self):
        target = event('orphan', 'Yes, use that option.', 'user')
        calls = []
        def provider(request):
            calls.append(request)
            return json.dumps({'context_requests': [{'operation': 'search', 'query': 'proposal antecedent', 'limit': 3}]})
        result = extract_dialogue(provider, target, [target])
        self.assertEqual(result['assertions'], [])
        self.assertTrue(result['reviews'])
        self.assertLessEqual(len(calls), 7)


if __name__ == '__main__':
    unittest.main()
