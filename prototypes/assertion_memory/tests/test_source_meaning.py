"""Source-level omission detection is separate from truth of emitted records.

All material is synthetic. The provider fixtures exercise the contract, not LLM
semantic intelligence: real-model source entailment remains a release gate.
"""
import json
import unittest

from prototypes.assertion_memory.dialogue import extract_dialogue
from prototypes.assertion_memory.model import format_prompt
from prototypes.assertion_memory.tests.test_dialogue_recovery import event


class SourceMeaningTest(unittest.TestCase):
    def setUp(self):
        self.target = event('target', 'During one trial, the cooling interval included the sensor check. '
                            'Other intervals were not recorded.', 'user')
        self.context = [self.target]
        self.base = {'text': 'The speaker reports a cooling interval during one trial.',
                     'quote': 'During one trial, the cooling interval included the sensor check.'}
        self.relation = {'text': 'The speaker reports that, in the described trial, the sensor check '
                         'was inside the cooling interval; other intervals were not recorded.',
                         'quote': self.target.prompt}
        self.calls = []

    def provider(self, request):
        self.calls.append(request)
        self.assertLessEqual(len(format_prompt(request)), 60_000)
        if request.task.startswith('Extract'):
            return json.dumps({'assertions': [self.base]})
        if request.task == 'Review source material coverage':
            captures = [row for row in request.records if row.get('role') == 'interpretation']
            complete = any(row['text'] == self.relation['text'] for row in captures)
            return json.dumps({'source_coverage': {
                'status': 'accepted' if complete else 'incomplete',
                'reason': 'Containment and the explicitly partial episode must survive.',
                'omissions': [] if complete else [{'quote': self.target.prompt,
                    'reason': 'The emitted interval alone loses containment and partial episode coverage.'}]}})
        if request.task == 'Repair omitted source meanings':
            return json.dumps({'assertions': [self.relation]})
        return json.dumps({'verdicts': [{'index': row['index'], 'status': 'accepted',
            'reason': 'The cited source entails this attributable interpretation.'}
            for row in request.records if row.get('role') == 'interpretation']})

    def coverage_audits(self, result):
        return [row for row in result['context_recovery'] if row['operation'] == 'source_coverage']

    def test_true_but_incomplete_capture_gets_additional_independently_verified_relation(self):
        result = extract_dialogue(self.provider, self.target, self.context)
        self.assertEqual([row['text'] for row in result['assertions']],
                         [self.base['text'], self.relation['text']])
        added = result['assertions'][1]
        self.assertEqual(added['interpretation']['status'], 'model_verified')
        self.assertEqual(added['evidence_refs'][0]['quote'], self.target.prompt)
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'accepted')
        checked = [call for call in self.calls if call.task.startswith('Verify')]
        self.assertTrue(any(any(row.get('text') == self.relation['text'] for row in call.records)
                            for call in checked), 'coverage repair must not bypass independent truth verification')

    def test_coherent_singleton_account_needs_no_extra_assertion_or_policy_edge(self):
        def provider(request):
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': [self.relation]})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual([row['text'] for row in result['assertions']], [self.relation['text']])
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'accepted')
        self.assertFalse(any(call.task.startswith('Repair') for call in self.calls))

    def test_omission_review_runs_even_when_extractor_emits_nothing(self):
        def provider(request):
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': []})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual([row['text'] for row in result['assertions']], [self.relation['text']])
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'accepted')

    def test_coverage_feedback_is_not_evidence_and_cannot_publish_unsupported_addition(self):
        def provider(request):
            if request.task.startswith('Verify') and any(row.get('text') == self.relation['text'] for row in request.records):
                return json.dumps({'verdicts': [{'index': row['index'], 'status': 'unsupported',
                    'reason': 'This fixture rejects the proposed repair.'} for row in request.records
                    if row.get('role') == 'interpretation']})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual([row['text'] for row in result['assertions']], [self.base['text']])
        self.assertEqual(sum(call.task == 'Repair omitted source meanings' for call in self.calls), 2)
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'unresolved')
        self.assertTrue(any(row['kind'] == 'source_coverage' and row['status'] == 'unresolved'
                            for row in result['reviews']))

    def test_fabricated_omission_anchor_cannot_trigger_repair_or_coverage_acceptance(self):
        def provider(request):
            if request.task == 'Review source material coverage':
                return json.dumps({'source_coverage': {'status': 'incomplete', 'reason': 'Missing relation.',
                    'omissions': [{'quote': 'An invented causal relationship.', 'reason': 'Missing.'}]}})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual([row['text'] for row in result['assertions']], [self.base['text']])
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'unresolved')
        self.assertFalse(any(call.task == 'Repair omitted source meanings' for call in self.calls))

    def test_legacy_verdict_response_does_not_claim_source_completeness(self):
        def provider(request):
            if request.task == 'Review source material coverage':
                return json.dumps({'verdicts': []})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual([row['text'] for row in result['assertions']], [self.base['text']])
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'unresolved')
        self.assertIn('coverage', self.coverage_audits(result)[-1]['reason'])
        self.assertTrue(any(row['kind'] == 'source_coverage' and row['status'] == 'unresolved'
                            for row in result['reviews']))

    def test_complete_verdict_with_omissions_is_not_coverage_acceptance(self):
        def provider(request):
            if request.task == 'Review source material coverage':
                return json.dumps({'source_coverage': {'status': 'accepted', 'reason': 'Contradictory.',
                    'omissions': [{'quote': self.target.prompt, 'reason': 'Still missing.'}]}})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'unresolved')
        self.assertEqual(len(result['assertions']), 1)

    def test_repair_cannot_regenerate_an_accepted_capture_as_a_new_kind(self):
        def provider(request):
            if request.task == 'Repair omitted source meanings':
                return json.dumps({'assertions': [{**self.base, 'kind': 'observation'}]})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual([row['text'] for row in result['assertions']], [self.base['text']])
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'unresolved')

    def test_missing_coverage_is_visible_in_public_status_history_and_overview_projection(self):
        import tempfile
        from pathlib import Path
        from prototypes.assertion_memory.service import MemoryService
        from prototypes.assertion_memory.storage import MemoryStore
        from prototypes.assertion_memory.worker import LinkWorker
        from prototypes.assertion_memory.overview import _model_record
        with tempfile.TemporaryDirectory() as directory, MemoryStore(Path(directory) / 'toy.sqlite') as store:
            service = MemoryService(store)
            self.target = service.queue.capture_event('s', 'turn', self.target.prompt, source='synthetic://trial', scope='toy')
            self.context = [self.target]
            def provider(request):
                if request.task == 'Review source material coverage':
                    return json.dumps({'verdicts': []})
                if request.task.startswith('Propose'):
                    return json.dumps({'links': [], 'branches': []})
                return self.provider(request)
            LinkWorker(service.queue, service.engine, provider).drain()
            status = service.status(self.target.id, scope='toy')
            self.assertEqual(status['unresolved_count'], 1)
            self.assertEqual(status['unresolved'][0]['proposal']['kind'], 'source_coverage')
            history = service.queue.extraction_history(self.target.id)
            self.assertTrue(any(row['kind'] == 'source_coverage' for row in history[0]['reviews']))
            source = service.source(self.target.id)
            self.assertTrue(any(row['kind'] == 'source_coverage'
                                for row in source['extraction_history'][0]['reviews']))
            assertion = store.list_assertions()[0]
            from dataclasses import asdict
            projected = _model_record({**asdict(assertion), 'disposition': 'active'})
            self.assertEqual(projected['metadata']['interpretation']['source_coverage']['status'], 'unresolved')
            # Reviewer critique is not a second, unverified fact stream to summaries.
            self.assertNotIn('reason', projected['metadata']['interpretation']['source_coverage'])

    def test_missing_target_tail_is_scanned_and_recovered_as_exact_repair_evidence(self):
        head = 'The trial used one cooling interval. '
        tail = 'The sensor check occurred inside that interval; other intervals were not recorded.'
        self.target = event('long-target', head + ('Neutral background. ' * 2500) + tail, 'user')
        self.context = [self.target]
        self.base = {'text': 'The speaker reports one cooling interval.', 'quote': head.strip()}
        self.relation = {'text': 'In the described trial, the sensor check was inside the cooling interval; '
                         'other intervals were not recorded.', 'quote': tail,
                         'evidence': [{'event_id': self.target.id, 'quote': head.strip()}]}
        saw_tail = []
        def provider(request):
            if request.task == 'Review source material coverage':
                self.calls.append(request)
                self.assertLessEqual(len(format_prompt(request)), 60_000)
                captures = [row for row in request.records if row.get('role') == 'interpretation']
                has_tail = any(tail in row.get('text', '') for row in request.records if row.get('is_target'))
                saw_tail.append(has_tail)
                missing = has_tail and not any(row['text'] == self.relation['text'] for row in captures)
                return json.dumps({'source_coverage': {'status': 'incomplete' if missing else 'accepted',
                    'reason': 'Review only the actual displayed target portions.',
                    'omissions': [{'quote': tail, 'reason': 'Missing qualified containment.'}] if missing else []}})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertTrue(any(saw_tail))
        self.assertEqual([row['text'] for row in result['assertions']], [self.base['text'], self.relation['text']])
        self.assertTrue(any(row['operation'] == 'read' and row.get('start') == self.target.prompt.index(tail)
                            for row in result['context_recovery']))
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'accepted')
        for ref in result['assertions'][-1]['evidence_refs']:
            self.assertEqual(self.target.prompt[ref['start']:ref['end']], ref['quote'])

    def test_exhausted_interpretation_repairs_leave_no_extra_coverage_repair_rounds(self):
        def provider(request):
            if request.task == 'Repair rejected contextual assertion interpretations':
                self.calls.append(request)
                return json.dumps({'assertions': [self.base]})
            if request.task.startswith('Verify'):
                return json.dumps({'verdicts': [{'index': row['index'], 'status': 'unsupported',
                    'reason': 'Rejected source interpretation.'} for row in request.records
                    if row.get('role') == 'interpretation']})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual(result['assertions'], [])
        self.assertEqual(sum(call.task.startswith('Repair') for call in self.calls), 2)
        self.assertFalse(any(call.task == 'Repair omitted source meanings' for call in self.calls))
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'unresolved')

    def test_coverage_cannot_accept_an_unread_target_beyond_existing_chunk_limit(self):
        self.target = event('huge-target', 'The trial used one cooling interval.' +
                            ('Neutral background. ' * 12000), 'user')
        self.context = [self.target]
        def provider(request):
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': []})
            if request.task == 'Review source material coverage':
                self.fail('Coverage beyond the chunk budget must not be sent to the provider.')
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual(result['assertions'], [])
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'unresolved')
        self.assertIn('chunk budget', self.coverage_audits(result)[-1]['reason'])

    def test_coverage_never_accepts_more_than_32_canonical_interpretations(self):
        originals = [{**self.base, 'text': f'The speaker reports sourced property {index}.'} for index in range(32)]
        def provider(request):
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': originals})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual(len(result['assertions']), 32)
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'unresolved')
        self.assertFalse(any(call.task == 'Repair omitted source meanings' for call in self.calls))

    def test_coverage_repair_never_exceeds_exhausted_context_recovery_budget(self):
        from unittest.mock import patch
        from prototypes.assertion_memory.dialogue_context import ContextWindow, MAX_RECOVERY_ROUNDS
        original = ContextWindow.coverage_records
        def exhausted(window, claims):
            # Earlier legitimate targeted retrieval consumed the shared limit;
            # the independent full-source scan can still see a target tail.
            window.recovery_rounds = MAX_RECOVERY_ROUNDS
            return original(window, claims)
        head = 'The trial used one cooling interval. '
        tail = 'The sensor check was inside it.'
        self.target = event('long-target', head + ('Neutral background. ' * 2500) + tail, 'user')
        self.context = [self.target]
        self.base = {'text': 'The speaker reports one cooling interval.', 'quote': head.strip()}
        def provider(request):
            if request.task == 'Review source material coverage':
                missing = any(tail in row.get('text', '') for row in request.records if row.get('is_target'))
                return json.dumps({'source_coverage': {'status': 'incomplete' if missing else 'accepted',
                    'reason': 'Missing relation in displayed target.',
                    'omissions': [{'quote': tail, 'reason': 'Missing containment.'}] if missing else []}})
            return self.provider(request)
        with patch.object(ContextWindow, 'coverage_records', exhausted):
            result = extract_dialogue(provider, self.target, self.context)
        self.assertFalse(any(call.task == 'Repair omitted source meanings' for call in self.calls))
        self.assertFalse(any(row['operation'] == 'read' for row in result['context_recovery']))
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'unresolved')

    def test_malformed_coverage_status_preserves_already_grounded_claims(self):
        def provider(request):
            if request.task == 'Review source material coverage':
                return json.dumps({'source_coverage': {'status': ['accepted'],
                    'reason': 'Malformed provider response.', 'omissions': []}})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual([row['text'] for row in result['assertions']], [self.base['text']])
        self.assertEqual(self.coverage_audits(result)[-1]['status'], 'unresolved')

    def test_later_coverage_repair_appends_meaning_without_rewriting_old_run_or_assertion(self):
        import tempfile
        from pathlib import Path
        from dataclasses import asdict
        from prototypes.assertion_memory.service import MemoryService
        from prototypes.assertion_memory.storage import MemoryStore
        from prototypes.assertion_memory.worker import LinkWorker
        with tempfile.TemporaryDirectory() as directory, MemoryStore(Path(directory) / 'toy.sqlite') as store:
            service = MemoryService(store)
            self.target = service.queue.capture_event('s', 'turn', self.target.prompt,
                                                      source='synthetic://trial', scope='toy')
            self.context = [self.target]
            def legacy(request):
                if request.task == 'Review source material coverage':
                    return json.dumps({'verdicts': []})
                if request.task.startswith('Propose'):
                    return json.dumps({'links': [], 'branches': []})
                return self.provider(request)
            LinkWorker(service.queue, service.engine, legacy).drain()
            old_history = json.dumps(service.queue.extraction_history(self.target.id)[0], sort_keys=True)
            old_assertion = store.list_assertions()[0]
            old_bytes = json.dumps(asdict(old_assertion), sort_keys=True)
            service.queue.retry(self.target.id)
            def repaired(request):
                if request.task.startswith('Propose'):
                    return json.dumps({'links': [], 'branches': []})
                return self.provider(request)
            LinkWorker(service.queue, service.engine, repaired).drain()
            history = service.queue.extraction_history(self.target.id)
            self.assertEqual(len(history), 2)
            self.assertEqual(json.dumps(history[0], sort_keys=True), old_history)
            self.assertEqual(json.dumps(asdict(store.get_assertion(old_assertion.assertion_id)), sort_keys=True), old_bytes)
            self.assertEqual({row.text for row in store.list_assertions()}, {self.base['text'], self.relation['text']})
            self.assertEqual(service.status(self.target.id, scope='toy')['unresolved_count'], 0)


if __name__ == '__main__':
    unittest.main()
