"""Bounded recovery of exact raw dialogue spans at the model boundary."""
import json
import unittest

from prototypes.assertion_memory.capture import RawEvent
from prototypes.assertion_memory.dialogue import (extract_dialogue, MAX_CONTEXT_CHARACTERS,
                                                MAX_MODEL_INPUT_CHARACTERS)
from prototypes.assertion_memory.model import ModelError, format_prompt


def event(i, text, actor='assistant', session='s'):
    return RawEvent(str(i), session, str(i), text, actor, 'dialogue://' + str(i), 'scope',
                    '2026-11-11T12:00:00.000000+00:00', {})


def accepted(request):
    return json.dumps({'verdicts': [{'index': row['index'], 'status': 'accepted', 'reason': 'Checked all supplied context.'}
                                  for row in request.records if row.get('role') == 'interpretation']})


class DialogueRecoveryTest(unittest.TestCase):
    def test_recovers_definition_after_nine_thousand_small_turns(self):
        definition = event(0, 'Метка «Синий» означает серверную 214 с непрерывным питанием.', 'user')
        filler = [event(i, 'Служебный разговор о погоде.') for i in range(1, 9001)]
        proposal = event(9001, 'Предлагаю ночью выключать корпус, кроме «Синего».')
        reply = event(9002, 'Да.', 'user')
        calls = []
        def model(request):
            calls.append(request)
            self.assertLessEqual(len(format_prompt(request)), 60_000)
            if request.task.startswith('Extract'):
                if not any(row.get('id') == definition.id and row.get('text') for row in request.records):
                    return json.dumps({'context_requests': [{'operation': 'search', 'query': 'Синий серверная непрерывное питание'}]})
                return json.dumps({'assertions': [{'text': 'Согласовано выключать корпус ночью, кроме серверной 214.',
                    'quote': 'Да.', 'kind': 'decision', 'evidence': [
                        {'event_id': definition.id, 'quote': definition.prompt, 'relation': 'definition'},
                        {'event_id': proposal.id, 'quote': proposal.prompt, 'relation': 'antecedent'}]}]})
            return accepted(request)
        result = extract_dialogue(model, reply, [definition, *filler, proposal, reply])
        self.assertEqual(len(result['assertions']), 1)
        ref = next(ref for ref in result['assertions'][0]['evidence_refs'] if ref['event_id'] == definition.id)
        self.assertEqual(definition.prompt[ref['start']:ref['end']], definition.prompt)
        self.assertTrue(result['context_recovery'])
        self.assertEqual(len([call for call in calls if call.task.startswith('Extract')]), 2)

    def test_oversized_immediate_proposal_can_be_read_without_adopting_older_alternative(self):
        older = event('A', 'Предлагаю вариант A.')
        proposal = event('B', 'Предлагаю вариант B. ' + 'Наполнитель. ' * 4000 + 'Только после проверки.')
        reply = event('yes', 'Да.', 'user')
        def model(request):
            self.assertLessEqual(len(format_prompt(request)), 60_000)
            if request.task.startswith('Extract'):
                chunks = [row for row in request.records if row.get('id') == proposal.id and row.get('text')]
                if not chunks:
                    return json.dumps({'context_requests': [
                        {'operation': 'read', 'event_id': proposal.id, 'start': 0, 'end': 100},
                        {'operation': 'read', 'event_id': proposal.id, 'start': len(proposal.prompt)-100, 'end': len(proposal.prompt)}]})
                return json.dumps({'assertions': [{'text': 'Согласован вариант B только после проверки.', 'quote': 'Да.', 'kind': 'decision',
                    'evidence': [{'event_id': 'B', 'quote': 'Предлагаю вариант B.', 'relation': 'antecedent'},
                                 {'event_id': 'B', 'quote': 'Только после проверки.', 'relation': 'qualification'}]}]})
            return accepted(request)
        result = extract_dialogue(model, reply, [older, proposal, reply])
        self.assertEqual(result['assertions'][0]['text'], 'Согласован вариант B только после проверки.')
        self.assertTrue(any(row.get('operation') == 'coverage' for row in result['context_recovery']))
        ref = result['assertions'][0]['evidence_refs'][-1]
        self.assertEqual(proposal.prompt[ref['start']:ref['end']], 'Только после проверки.')

    def test_unseen_quote_cannot_be_anchored_against_hidden_full_source(self):
        hidden = event('hidden', 'Начало. ' + 'x' * 50000 + 'Секретное условие.')
        reply = event('yes', 'Да.', 'user')
        def model(request):
            return json.dumps({'assertions': [{'text': 'Подтверждено секретное условие.', 'quote': 'Да.',
                'evidence': [{'event_id': hidden.id, 'quote': 'Секретное условие.'}]}]})
        with self.assertRaises(ModelError):
            extract_dialogue(model, reply, [hidden, reply])

    def test_coverage_scan_rejects_qualification_omitted_between_read_spans(self):
        # Keep this source outside the actual context budget: a fully supplied
        # source needs no extra scan, so it cannot exercise missing-middle checks.
        padding = MAX_CONTEXT_CHARACTERS // 2
        proposal = event('B', 'Предлагаю вариант B. ' + 'x' * padding + 'Но только если пройден аудит. ' + 'y' * padding)
        reply = event('yes', 'Да.', 'user')
        scans = []
        def model(request):
            self.assertLessEqual(len(format_prompt(request)), MAX_MODEL_INPUT_CHARACTERS)
            if request.task.startswith('Extract'):
                if not any(row.get('id') == 'B' and row.get('text') for row in request.records):
                    return json.dumps({'context_requests': [{'operation': 'read', 'event_id': 'B', 'start': 0, 'end': 100}]})
                return json.dumps({'assertions': [{'text': 'Согласован вариант B без условий.', 'quote': 'Да.', 'kind': 'decision',
                    'evidence': [{'event_id': 'B', 'quote': 'Предлагаю вариант B.', 'relation': 'antecedent'}]}]})
            scan = [row for row in request.records if row.get('coverage_scan')]
            scans.extend(scan)
            if any('Но только если пройден аудит.' in row.get('text', '') for row in scan):
                return json.dumps({'verdicts': [{'index': 0, 'status': 'unsupported', 'reason': 'Omitted mandatory audit condition.'}]})
            return accepted(request)
        result = extract_dialogue(model, reply, [proposal, reply])
        self.assertEqual(result['assertions'], [])
        self.assertTrue(any(row.get('operation') == 'read' and row.get('status') == 'supplied'
                            for row in result['context_recovery']))
        self.assertTrue(scans)
        self.assertIn('audit', str(result['reviews']))

    def test_retrieval_never_reads_future_or_other_session_events(self):
        prior = event('prior', 'Вариант A.')
        reply = event('yes', 'Да.', 'user')
        future = event('future', 'Будущее уточнение.')
        other = event('other', 'Другой проект.', session='other')
        for missing in (future, other):
            def model(request):
                if request.task.startswith('Extract'):
                    return json.dumps({'context_requests': [{'operation': 'read', 'event_id': missing.id, 'start': 0, 'end': 100}]})
                return accepted(request)
            result = extract_dialogue(model, reply, [other, prior, reply, future])
            self.assertEqual(result['assertions'], [])
            self.assertTrue(result['context_recovery'])
            self.assertIn('unavailable', str(result['context_recovery']))

    def test_retrieval_uses_injected_ranker_and_never_exposes_its_whole_index(self):
        from prototypes.assertion_memory.model import CandidateSearch
        class RecordingRanker(CandidateSearch):
            identity = 'semantic-test'
            def __init__(self):
                super().__init__(identity='semantic-test')
                self.seen = []
            def rank(self, query, candidates, *, limit=10):
                self.seen.append((query, len(candidates)))
                return super().rank(query, candidates, limit=limit)
        ranker = RecordingRanker()
        definition = event('definition', 'Синий означает комнату 214.')
        reply = event('yes', 'Да.', 'user')
        count = 0
        def model(request):
            nonlocal count
            count += 1
            if count == 1:
                return json.dumps({'context_requests': [{'operation': 'search', 'query': 'Синий 214'}]})
            return json.dumps({'assertions': []})
        result = extract_dialogue(model, reply, [definition, reply], candidates=ranker)
        self.assertEqual(ranker.seen, [('Синий 214', 1)])
        self.assertEqual(result['context_recovery'][0]['method'], 'semantic-test')

    def test_repeated_quote_respects_explicit_original_offset_in_retrieved_span(self):
        proposal = event('B', 'Условие. ' + 'x' * 40000 + 'Условие. Предлагаю вариант B.')
        reply = event('yes', 'Да.', 'user')
        start = proposal.prompt.rfind('Условие.')
        def model(request):
            if request.task.startswith('Extract'):
                if not any(row.get('id') == 'B' and row.get('text') for row in request.records):
                    return json.dumps({'context_requests': [{'operation': 'read', 'event_id': 'B',
                                      'start': start, 'end': len(proposal.prompt)}]})
                return json.dumps({'assertions': [{'text': 'Согласован вариант B с условием.', 'quote': 'Да.',
                    'evidence': [{'event_id': 'B', 'quote': 'Условие.', 'start': start}]}]})
            return accepted(request)
        result = extract_dialogue(model, reply, [proposal, reply])
        self.assertEqual(result['assertions'][0]['evidence_refs'][-1]['start'], start)

    def test_oversized_target_is_bounded_before_first_model_invocation(self):
        target = event('large', 'Начало. ' + 'x' * 100000, 'user')
        calls = []
        def model(request):
            calls.append(request)
            self.assertLessEqual(len(format_prompt(request)), 60000)
            return json.dumps({'assertions': []})
        result = extract_dialogue(model, target, [target])
        self.assertTrue(result['context_truncated'])
        self.assertTrue(any(review['status'] == 'unresolved' for review in result['reviews']),
                        'an unread target remainder must not be reported as a completed empty extraction')
        self.assertEqual(calls[0].records[0]['source_span'], {'start': 0, 'end': 6000})

    def test_independent_verifier_can_recover_competing_earlier_definition(self):
        first = event('first', 'Синий означает комнату 214.', 'user')
        changed = event('changed', 'Теперь Синий означает комнату 216.', 'user')
        filler = [event(i, 'Продолжение беседы.') for i in range(200)]
        proposal = event('proposal', 'Предлагаю сохранить питание Синего.')
        reply = event('yes', 'Да.', 'user')
        verified = False
        def model(request):
            nonlocal verified
            if request.task.startswith('Extract'):
                if not any(row.get('id') == 'first' and row.get('text') for row in request.records):
                    return json.dumps({'context_requests': [{'operation': 'read', 'event_id': 'first',
                                        'start': 0, 'end': len(first.prompt)}]})
                return json.dumps({'assertions': [{'text': 'Согласовано сохранить питание комнаты 214.',
                    'quote': 'Да.', 'evidence': [{'event_id': 'first', 'quote': first.prompt},
                                                {'event_id': 'proposal', 'quote': proposal.prompt}]}]})
            if not verified:
                verified = True
                return json.dumps({'context_requests': [{'operation': 'search', 'query': 'Теперь Синий означает 216'}]})
            self.assertTrue(any(row.get('id') == 'changed' and row.get('text') for row in request.records))
            return json.dumps({'verdicts': [{'index': 0, 'status': 'unsupported', 'reason': 'Later definition changed the room.'}]})
        result = extract_dialogue(model, reply, [first, changed, *filler, proposal, reply])
        self.assertEqual(result['assertions'], [])
        self.assertIn('changed the room', str(result['reviews']))
        self.assertTrue(any(row.get('operation') == 'search' for row in result['context_recovery']))

    def test_worker_excludes_unverified_staged_sources_and_persists_recovery_audit(self):
        import tempfile
        from pathlib import Path
        from prototypes.assertion_memory.engine import MemoryEngine
        from prototypes.assertion_memory.queue import CaptureQueue
        from prototypes.assertion_memory.storage import MemoryStore
        from prototypes.assertion_memory.worker import LinkWorker
        with tempfile.TemporaryDirectory() as temp, MemoryStore(Path(temp) / 'test.sqlite') as store:
            queue = CaptureQueue(store)
            staged = queue.capture_event('s', 'one', 'Непроверенный источник.', source='test://one', await_origin=True)
            target = queue.capture_event('s', 'two', 'Да.', source='test://two')
            def model(request):
                self.assertFalse(any(row.get('id')==staged.id and not row.get('context_unavailable')
                                     for row in request.records))
                self.assertNotIn(staged.prompt,json.dumps(request.records,ensure_ascii=False))
                return json.dumps({'context_requests': [{'operation': 'read', 'event_id': staged.id,
                                                        'start': 0, 'end': len(staged.prompt)}]})
            LinkWorker(queue, MemoryEngine(store), model).run_once()
            job = queue.get(target.id)
            self.assertEqual(job.status, 'completed', job.error)
            self.assertEqual(job.extracted_ids, [])
            audit = queue.extraction_history(target.id)[0]['context_recovery']
            self.assertEqual(audit[0]['status'], 'unavailable')
            self.assertEqual(queue.get(staged.id).status, 'waiting_origin')

    def test_exhausted_retrieval_is_explicit_and_does_not_loop(self):
        reply = event('yes', 'Да.', 'user')
        calls = []
        def model(request):
            calls.append(request)
            return json.dumps({'context_requests': [{'operation': 'search', 'query': 'unknown antecedent'}]})
        result = extract_dialogue(model, reply, [reply])
        self.assertEqual(result['assertions'], [])
        self.assertLess(len(calls), 10)
        self.assertTrue(result['context_truncated'])
        self.assertIn('unresolved', str(result['reviews']))


if __name__ == '__main__':
    unittest.main()
