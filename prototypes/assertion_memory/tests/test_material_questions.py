"""Invented dialogue, real service/worker/store; scripted providers test contracts.

These are pipeline regressions, not evidence of real-model semantic quality.
The extractor follows explicit prompt exclusions; the verifier is a separate
allowlist-and-source oracle and rejects premise/decision promotion.
"""
import json
import unittest
from dataclasses import asdict
from unittest.mock import patch

from prototypes.assertion_memory.dialogue import extract_dialogue
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import format_prompt
from prototypes.assertion_memory.query_snapshot import capture, _restore_frozen
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker
from prototypes.assertion_memory.tests.automatic_overview_provider import respond


class MaterialQuestionsTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.calls, self.meanings, self.injected = [], {}, {}
        self.engine = MemoryEngine(self.store, model=self.provider, verify_overviews=True)
        self.service = MemoryService(self.store, self.engine)
        self.worker = LinkWorker(self.service.queue, self.engine, self.provider, source_first=True)
        manual = patch.object(self.service.queue, 'capture', wraps=self.service.queue.capture)
        self.canonical_captures = manual.start()
        self.addCleanup(manual.stop)
        links = patch.object(self.store, 'append_edge', wraps=self.store.append_edge)
        self.link_append = links.start()
        self.addCleanup(links.stop)

    def message(self, text, meaning=None, *, actor='Mira', session='workshop', position=0,
                role='user', evidence=None):
        receipt = self.service.capture_message(session, str(position), actor, role, text, position,
                                               f'fixture://{session}/{position}', scope='lab')
        event = self.service.queue.get_event(receipt['event']['id'])
        self.meanings[event.id] = ([] if meaning is None else [
            {'text': meaning, 'quote': text, 'kind': 'assertion', **({'evidence': evidence} if evidence else {})}])
        return event

    @staticmethod
    def excluded(request):
        return any(rule in request.instructions for rule in (
            'Exclude questions,', 'questions, hypotheticals and attribution',
            'Questions or purely hypothetical examples need no attributable fact.'))

    def provider(self, request):
        self.calls.append(request)
        self.assertLessEqual(len(format_prompt(request)), 60_000)
        target = next((r for r in request.records if r.get('is_target')), None)
        expected = self.meanings.get(target['id'], []) if target else []
        questioned = target and ('?' in target['text'] or 'asked whether' in target['text'])
        if request.task.startswith('Extract'):
            claims = self.injected.get(target['id'], expected)
            return json.dumps({'assertions': [] if self.excluded(request) and questioned else claims})
        if request.task == 'Repair rejected contextual assertion interpretations':
            # No oracle fixes an intentionally malicious extractor for it.
            return json.dumps({'assertions': []})
        if request.task == 'Review source material coverage':
            texts = {r['text'] for r in request.records if r.get('role') == 'interpretation'}
            missing = [] if self.excluded(request) and questioned else [c for c in expected if c['text'] not in texts]
            return json.dumps({'source_coverage': {
                'status': 'incomplete' if missing else 'accepted',
                'reason': 'Check the attributed source event, without promoting its premise.',
                'omissions': [{'quote': c['quote'], 'reason': 'Material attributed question was omitted.'}
                              for c in missing]}})
        if request.task == 'Repair omitted source meanings':
            return json.dumps({'assertions': [] if self.excluded(request) and questioned else expected})
        if request.task == 'Verify contextual assertion interpretations':
            # Independent bounded source oracle: exact permitted meanings plus actual
            # quoted spans. It never accepts arbitrary extractor or critique output.
            verdicts = []
            for row in request.records:
                if row.get('role') != 'interpretation':
                    continue
                refs = row.get('evidence_refs', [])
                supported = row['text'] in {c['text'] for c in expected}
                supported &= bool(refs) and all(any(
                    r.get('id') == ref['event_id'] and
                    r.get('source_span', {}).get('start', 0) <= ref['start'] and
                    ref['end'] <= r.get('source_span', {}).get('end', 0) and
                    r['text'][ref['start']-r['source_span']['start']:ref['end']-r['source_span']['start']] == ref['quote']
                    for r in request.records if r.get('role') != 'interpretation') for ref in refs)
                if self.excluded(request) and expected and questioned:
                    supported = False
                verdicts.append({'index': row['index'], 'status': 'accepted' if supported else 'unsupported',
                    'reason': 'Exact source and attributed speech act checked independently; no premise or choice promotion.'})
            return json.dumps({'verdicts': verdicts})
        if request.task == 'Discover reusable memory themes':
            ids = [r['id'] for r in request.records if r.get('role') == 'source']
            return json.dumps({'themes': [{'question': 'What workshop matters were raised?', 'source_ids': ids}],
                               'unassigned_source_ids': []})
        if request.task == 'Review thematic source membership':
            return json.dumps({'source_ids': [r['id'] for r in request.records if r.get('role') == 'candidate'],
                'unresolved_source_ids': [], 'reason': 'All supplied invented workshop records belong.'})
        if request.task in ('Review grounded memory overview', 'Review grounded memory contribution'):
            draft = next(r['text'] for r in request.records if r.get('role') == 'overview_draft')
            # Independent exact-content oracle: every line must be a supplied
            # canonical claim with its own citation or an unchanged reviewed child.
            allowed = set()
            for row in request.records:
                if row.get('role') in ('verified_view', 'verified_contribution'):
                    allowed.update(row.get('summary', '').splitlines())
                elif row.get('id') and row.get('text'):
                    allowed.add(row['text'] + ' [' + row['id'] + ']')
                    allowed.add(row['text'] + ' [source:' + row['id'] + ']')
            grounded = bool(draft.strip()) and all(line in allowed for line in draft.splitlines())
            status = 'accepted' if grounded else 'unresolved'
            common = {'reason': 'Every draft line must exactly preserve supplied attributable content and citation.',
                      'issues': [], 'context_requests': []}
            if request.task == 'Review grounded memory contribution':
                return json.dumps({**common, 'grounding_status': status, 'coverage': 'packet_claims',
                                   'global_coverage': 'not_assessed', 'global_requests': []})
            return json.dumps({**common, 'status': status, 'coverage': 'all_claims', 'unknown_needs': []})
        value = respond({'task': request.task, 'records': request.records})
        return value if isinstance(value, str) else json.dumps(value)

    def drain(self):
        for _ in range(80):
            if self.worker.run_once() is None:
                return
        self.fail('Worker did not reach a finite idle state')

    def texts(self):
        return {self.store.get_assertion(key).text for key in capture(self.engine).assertions}

    def check_source(self, event, expected_count):
        history = self.service.queue.extraction_history(event.id)
        self.assertEqual(history[-1]['source_revision']['status'], 'accepted_complete')
        self.assertEqual(history[-1]['source_revision']['schema'], 'source-interpretation/v1')
        self.assertEqual(len(history[-1]['source_revision']['assertion_ids']), expected_count)
        for assertion in self.store.list_assertions():
            for ref in assertion.metadata.get('evidence_refs', []):
                source = self.service.queue.get_event(ref['event_id'])
                self.assertEqual(source.prompt[ref['start']:ref['end']],
                                 self.service.queue.resolve_reference(ref)['text'])
                if ref['event_id'] == assertion.metadata['event_id']:
                    self.assertEqual(source.prompt[ref['start']:ref['end']], assertion.metadata['excerpt'])
        self.assertEqual(self.service.source(event.id)['event']['prompt'], event.prompt)

    def test_material_planning_question_is_automatically_discoverable_with_exact_attribution(self):
        meaning = 'Mira wants to reorganize workshop inspections and raised grouping by machine or by day before choosing.'
        event = self.message('I want to reorganize the workshop inspections. Should we group them by machine or by day? '
                             "Let's work through that before I choose.", meaning)
        self.drain()
        self.assertEqual(self.texts(), {meaning})
        self.check_source(event, 1)
        state = self.service.status(scope='lab')
        entries = state['derived_overviews']['entries']
        self.assertTrue(entries)
        root = entries[0]
        self.assertIsNotNone(root['current_version_id'])
        version = self.engine.get_version(root['current_version_id'])
        self.assertEqual(version.overview_review['assembly_coverage'], 'navigation_index')
        summary = '\n'.join(self.engine.get_version(key).summary for key in version.upstream.values())
        self.assertIn(meaning, summary)
        assertion = self.store.list_assertions()[0]
        self.assertEqual(assertion.actor, 'Mira')
        self.assertEqual(assertion.metadata['kind'], 'assertion')
        self.assertEqual(len(self.canonical_captures.call_args_list), 1)
        published = self.canonical_captures.call_args.args[0]
        self.assertEqual(published.metadata['event_id'], event.id)
        self.assertEqual(published.metadata['interpretation']['status'], 'model_verified')
        self.assertEqual([call for call in self.canonical_captures.call_args_list
                          if not call.args[0].metadata.get('event_id')], [], 'zero manually inserted nodes')
        self.link_append.assert_not_called()
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_edges').fetchone()[0], 0)

    def test_question_only_in_project_context_needs_no_remember_or_want_keyword(self):
        context = self.message('We are planning workshop inspections.', actor='assistant', role='assistant')
        meaning = 'Mira asked whether to group workshop inspections by machine or by day.'
        event = self.message('Should we group by machine or day?', meaning, position=1,
            evidence=[{'event_id': context.id, 'quote': context.prompt, 'relation': 'antecedent'}])
        self.drain()
        self.assertEqual(self.texts(), {meaning})
        self.check_source(event, 1)
        self.assertEqual(len(self.service.queue.extraction_history(context.id)), 0)
        self.assertEqual(len(self.store.list_assertions()[0].metadata['evidence_refs']), 2)

    def test_routine_lookup_greeting_and_disposable_hypothetical_stay_raw_only(self):
        for i, text in enumerate(('What is 7 times 8?', 'How are you?',
                'For a throwaway example, what if the moon were made of cheese?')):
            event = self.message(text, actor='Noor', session=str(i))
            self.drain()
            self.check_source(event, 0)
        self.assertEqual(self.texts(), set())

    def test_paired_capacity_investigation_is_retained_without_insufficiency_claim(self):
        meaning = 'Noor wants to investigate whether seven crews can cover eight sites for the maintenance plan.'
        event = self.message('For our maintenance plan, we need to determine whether seven crews can cover eight sites; '
                             'this is the capacity question I want to investigate next.', meaning, actor='Noor')
        self.drain()
        self.assertEqual(self.texts(), {meaning})
        self.check_source(event, 1)

    def test_reported_vendor_question_preserves_unconfirmed_premise_and_actor(self):
        meaning = 'Ivo reports that the vendor asked whether a cracked casing caused the outage; '
        meaning += 'Ivo has not confirmed a crack and wants to investigate the outage cause.'
        event = self.message('The vendor asked whether the cracked casing caused the outage. '
            'I have not confirmed that the casing is cracked. I want us to investigate the outage cause.', meaning, actor='Ivo')
        self.drain()
        self.assertEqual(self.texts(), {meaning})
        self.check_source(event, 1)
        self.assertEqual(self.store.list_assertions()[0].actor, 'Ivo')

    def test_vendor_question_cannot_promote_its_actual_premise_or_change_speaker(self):
        meaning = 'Ivo reports the vendor asked whether a cracked casing caused the outage; '
        meaning += 'Ivo has not confirmed the crack and wants to investigate the cause.'
        event = self.message('The vendor asked whether the cracked casing caused the outage. '
            'I have not confirmed that the casing is cracked. I want us to investigate the outage cause.',
            meaning, actor='Ivo')
        bad = ['The casing is cracked.', 'The cracked casing caused the outage.',
               "Ivo adopted the vendor's cracked-casing hypothesis.",
               'Ivo asked whether a cracked casing caused the outage.']
        self.injected[event.id] = [*self.meanings[event.id],
                                  *({'text': text, 'quote': event.prompt} for text in bad)]
        self.drain()
        self.assertEqual(self.texts(), {meaning})
        self.check_source(event, 1)
        reviews = self.service.queue.extraction_history(event.id)[-1]['reviews']
        for text in bad:
            self.assertTrue(any(r.get('text') == text and r['status'] == 'unsupported' for r in reviews))

    def test_independent_verifier_rejects_premise_choice_assignment_and_global_open_state(self):
        meaning = 'Mira asked whether to group workshop inspections by machine or by day.'
        event = self.message('For the workshop inspections, should we group by machine or by day?', meaning)
        bad = ['Mira chose grouping by day.', 'Mira assigned the grouping task to Noor.',
               'Nobody has answered the grouping question.', 'The casing is cracked.']
        self.injected[event.id] = [{'text': text, 'quote': event.prompt} for text in bad]
        self.drain()
        # Rejected repairs consume the existing shared two-round budget; a missing
        # speech act remains explicitly unresolved rather than bypassing review.
        self.assertEqual(self.texts(), set())
        history = self.service.queue.extraction_history(event.id)[-1]
        self.assertEqual(history['source_revision']['status'], 'unresolved')
        reviews = history['reviews']
        for text in bad:
            self.assertTrue(any(r.get('text') == text and r['status'] == 'unsupported' for r in reviews))

    def test_later_scoped_choice_preserves_historical_question_and_recorded_cutoff(self):
        raised = 'Ada asked whether to group inspections by machine or by day for this exercise, to choose a method.'
        first = self.message('For this exercise, should we group inspections by machine or by day? I want to choose a method.',
                             raised, actor='Ada')
        self.drain()
        self.assertEqual(self.texts(), {raised})
        frozen = capture(self.engine)
        cutoff = frozen.captured_at
        chosen = 'Ada chose grouping inspections by machine for this exercise.'
        later = self.message('I choose grouping by machine for this exercise.', chosen, actor='Ada', position=1)
        self.drain()
        self.assertEqual(self.texts(), {raised, chosen})
        self.assertEqual(asdict(_restore_frozen(self.engine, frozen.descriptor())), asdict(frozen))
        old = capture(self.engine, recorded_as_of=cutoff)
        self.assertEqual({self.store.get_assertion(key).text for key in old.assertions}, {raised})
        self.check_source(first, 1)
        self.check_source(later, 1)
        root = self.service.status(scope='lab')['derived_overviews']['entries'][0]
        version = self.engine.get_version(root['current_version_id'])
        self.assertEqual(version.overview_review['assembly_coverage'], 'navigation_index')
        summary = '\n'.join(self.engine.get_version(key).summary for key in version.upstream.values())
        self.assertIn(raised, summary)
        self.assertIn(chosen, summary)
        self.assertNotIn('remains undecided', summary)

    def test_accepted_empty_extraction_gets_independently_verified_material_repair(self):
        meaning = 'Mira asked whether to group workshop inspections by machine or by day.'
        event = self.message('For workshop inspections, should we group by machine or by day?', meaning)
        self.injected[event.id] = []
        self.drain()
        self.assertEqual(self.texts(), {meaning})
        self.check_source(event, 1)
        self.assertTrue(any(r.task == 'Repair omitted source meanings' for r in self.calls))
        checked = [r for r in self.calls if r.task == 'Verify contextual assertion interpretations']
        self.assertTrue(any(any(row.get('text') == meaning for row in r.records) for r in checked))

    def test_protocol_upgrade_reaudits_old_empty_questions_once_including_routine(self):
        from prototypes.assertion_memory import source_coverage, source_reprocessing
        raised = 'Mira asked whether to group workshop inspections by machine or by day.'
        first = self.message('For workshop inspections, should we group by machine or by day?', raised)
        routine = self.message('What is 7 times 8?', session='routine', actor='Noor')
        original = self.meanings[first.id]
        self.meanings[first.id] = []
        with patch.object(source_coverage, 'PROTOCOL', 'source-material-coverage-v1'), patch.object(
                source_reprocessing, 'PROTOCOL', 'source-reconciliation/v1:source-material-coverage-v1'):
            self.drain()
        old = json.dumps(self.service.queue.extraction_history(first.id), sort_keys=True)
        self.meanings[first.id] = original
        self.drain()
        self.assertEqual(self.texts(), {raised})
        self.check_source(first, 1)
        self.check_source(routine, 0)
        histories = [self.service.queue.extraction_history(e.id) for e in (first, routine)]
        self.assertEqual([len(h) for h in histories], [2, 2])
        self.assertEqual(json.dumps(histories[0][:-1], sort_keys=True), old)
        count = len(self.calls)
        self.drain()
        self.assertEqual(len(self.calls), count)
        self.assertEqual(source_coverage.PROTOCOL, 'source-material-coverage-v8:source-message-clock-v1')

    def test_independent_overview_reviewer_rejects_persistent_indecision_from_old_question(self):
        from prototypes.assertion_memory.model import ModelRequest
        question = 'Ada raised the machine-or-day question for this exercise.'
        choice = 'Ada chose grouping by machine for this exercise.'
        request = ModelRequest('Review grounded memory overview', [
            {'id': 'q', 'text': question}, {'id': 'c', 'text': choice},
            {'role': 'overview_draft', 'text': 'Ada remains undecided. [q]'}], 'Fixture review')
        self.assertEqual(json.loads(self.provider(request))['status'], 'unresolved')

    def test_actual_model_request_contracts_share_attributed_material_question_policy(self):
        meaning = 'Mira asked whether to group workshop inspections by machine or by day.'
        event = self.message('For workshop inspections, should we group by machine or by day?', meaning)
        self.injected[event.id] = []
        self.drain()
        tasks = {'Extract attributable assertions from the user event',
                 'Verify contextual assertion interpretations', 'Review source material coverage',
                 'Repair omitted source meanings'}
        requests = [r for r in self.calls if r.task in tasks]
        self.assertEqual({r.task for r in requests}, tasks)
        for request in requests:
            self.assertFalse(self.excluded(request))
            # Supporting transport parity only: the actual provider wire prompt
            # includes the same speech-act, source and non-promotion instructions.
            wire = format_prompt(request)
            self.assertEqual(json.loads(wire.split('\n\n', 1)[1])['instructions'], request.instructions)
            self.assertIn('Asking never', request.instructions)
            self.assertIn('global unresolved state', request.instructions)
            self.assertIn('kind assertion', request.instructions)
            self.assertIn(event.prompt, wire)

    def test_legacy_literal_question_guard_is_unchanged(self):
        event = self.message('Should we group inspections by machine or day?')
        def literal(request):
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': [event.prompt]})
            return self.provider(request)
        from prototypes.assertion_memory.model import ModelError
        with self.assertRaisesRegex(ModelError, 'question'):
            extract_dialogue(literal, event, [event])


if __name__ == '__main__':
    unittest.main()
