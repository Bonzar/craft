"""Regression checks for causal dialogue context at the capture boundary."""
import json
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker


class ContextCaptureTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = MemoryStore(Path(folder.name) / 'memory.sqlite3')
        self.addCleanup(self.store.close)
        self.queue = CaptureQueue(self.store)

    def event(self, turn, text, actor='user', session='office'):
        return self.queue.capture_event(session, turn, text, actor=actor,
                                        source=f'chat://{session}/{turn}')

    def test_assistant_message_is_durable_context_without_becoming_a_user_claim(self):
        proposal = self.event('one', 'Предлагаю прозрачные окна.', actor='assistant')
        job = self.queue.get(proposal.id)
        self.assertEqual(job.status, 'completed')
        self.assertEqual(self.queue.list_events(), [proposal])
        self.assertIsNone(LinkWorker(self.queue, MemoryEngine(self.store)).run_once())
        self.assertEqual(self.store.list_assertions(), [])

    def test_context_stops_at_reply_and_stays_in_its_session(self):
        definition = self.event('one', '214 и 216 — аудитории западного крыла.')
        proposal = self.event('two', 'Оставим все окна прозрачными?', actor='assistant')
        reply = self.event('three', 'Да, кроме помещений 214 и 216.')
        self.event('four', 'Значит, можно закрыть весь фасад.', actor='assistant')
        self.event('one', 'Для другого кампуса выбираем другое.', session='other')
        context = self.queue.context_events(reply.id)
        self.assertEqual([row.id for row in context], [definition.id, proposal.id, reply.id])

    def test_contextual_claim_retains_verbatim_source_and_reason_references(self):
        definition = self.event('one', 'В аудиториях 214 и 216 блики на экранах.')
        proposal = self.event('two', 'Оставим прозрачные окна, кроме этих двух аудиторий?', actor='assistant')
        reply = self.event('three', 'Да, для них нужны ламели.')
        seen = []

        def provider(request):
            seen.append(request)
            if request.task == 'Verify contextual assertion interpretations':
                return json.dumps({'verdicts': [{'index': 0, 'status': 'accepted', 'reason': 'Однозначное согласие.'}]})
            if request.task == 'Extract attributable assertions from the user event':
                if request.records[0]['id'] != reply.id:
                    return json.dumps({'assertions': []})
                return json.dumps({'assertions': [{
                    'text': 'Пользователь согласовал ламели только для аудиторий 214 и 216 из-за бликов на экранах.',
                    'quote': reply.prompt, 'kind': 'decision',
                    'evidence': [{'event_id': definition.id, 'quote': definition.prompt, 'relation': 'reason'},
                                 {'event_id': proposal.id, 'quote': proposal.prompt, 'relation': 'antecedent'}],
                }]})
            return json.dumps({'links': []})

        LinkWorker(self.queue, MemoryEngine(self.store), provider).drain()
        assertions = self.store.list_assertions()
        self.assertEqual(len(assertions), 1)
        claim = assertions[0]
        self.assertEqual(claim.actor, 'user')
        self.assertEqual(claim.metadata['excerpt'], reply.prompt)
        self.assertEqual(claim.metadata['kind'], 'decision')
        self.assertEqual({row['event_id'] for row in claim.metadata['evidence_refs']},
                         {definition.id, proposal.id, reply.id})
        self.assertEqual(claim.metadata['interpretation']['status'], 'model_verified')
        extraction = next(r for r in seen if r.task.startswith('Extract') and r.records[0]['id'] == reply.id)
        self.assertEqual({r['id'] for r in extraction.records}, {definition.id, proposal.id, reply.id})

    def test_native_message_identity_and_position_survive_delayed_capture(self):
        reply = self.queue.capture_event('native', 'turn', 'Да.', source='native://reply',
                                         message_id='reply', position=30)
        prior = self.queue.capture_event('native', 'earlier', 'Прозрачные окна?', source='native://proposal',
                                         actor='assistant', message_id='proposal', position=10)
        self.queue.capture_event('native', 'later', 'Теперь изменим решение.', source='native://later',
                                 actor='assistant', message_id='later', position=40)
        repeated = self.queue.capture_event('native', 'turn', 'Да.', source='native://second-reply',
                                            message_id='second-reply', position=35)
        self.assertNotEqual(reply.id, repeated.id)
        self.assertEqual([row.id for row in self.queue.context_events(reply.id)], [prior.id, reply.id])

    def test_retrying_assistant_context_never_extracts_claims(self):
        proposal = self.event('one', 'Отключим все серверы?', actor='assistant')
        self.queue.retry(proposal.id)
        calls = []
        worker = LinkWorker(self.queue, MemoryEngine(self.store), lambda request: calls.append(request))
        worker.drain()
        self.assertEqual(calls, [])
        self.assertEqual(self.store.list_assertions(), [])

    def test_partial_legacy_quote_cannot_drop_adjacent_condition(self):
        event = self.event('one', 'Используем A. Только во время пилота.')
        calls = []
        def provider(request):
            calls.append(request.task)
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': ['Используем A.']})
            return json.dumps({'verdicts': [{'index': 0, 'status': 'unsupported', 'reason': 'Omitted pilot restriction.'}]})
        LinkWorker(self.queue, MemoryEngine(self.store), provider).drain()
        self.assertIn('Verify contextual assertion interpretations', calls)
        self.assertEqual(self.store.list_assertions(), [])
        self.assertEqual(self.queue.get(event.id).proposals[0]['status'], 'unsupported')

    def test_large_assistant_message_does_not_hide_earlier_definitions(self):
        definition = self.event('one', 'Помещения 214 и 216 находятся в западном крыле.')
        proposal = self.event('two', 'Обсуждение ' + 'а' * 19000, actor='assistant')
        reply = self.event('three', 'Да, кроме этих двух помещений.')
        calls = []
        def provider(request):
            calls.append(request)
            return json.dumps({'assertions': []})
        LinkWorker(self.queue, MemoryEngine(self.store), provider).drain()
        request = next(r for r in calls if r.records[0]['id'] == reply.id)
        self.assertIn(definition.id, {r['id'] for r in request.records})
        self.assertIn(proposal.id, {r['id'] for r in request.records})

    def test_omitted_intervening_proposal_is_visible_as_a_context_gap(self):
        earlier = self.event('one', 'Предлагаю вариант A.', actor='assistant')
        missing = self.event('two', 'Предлагаю вариант B. ' + 'б' * 40000, actor='assistant')
        reply = self.event('three', 'Да.')
        calls = []
        def provider(request):
            calls.append(request)
            return json.dumps({'assertions': []})
        LinkWorker(self.queue, MemoryEngine(self.store), provider).drain()
        request = next(row for row in calls if row.records[0]['id'] == reply.id)
        gap = next(row for row in request.records if row['id'] == missing.id)
        self.assertTrue(gap['context_unavailable'])
        self.assertIn(earlier.id, {row['id'] for row in request.records})

    def test_verified_generic_kind_is_not_reinterpreted_from_its_words(self):
        from prototypes.assertion_memory.capture import declared_kind
        metadata = {'kind': 'assertion', 'interpretation': {'status': 'model_verified'}}
        self.assertEqual(declared_kind('We decided on this wording.', metadata), 'assertion')

    def test_proofless_direct_legacy_assertion_is_preserved_but_not_reused_as_source_proof(self):
        from prototypes.assertion_memory.capture import CaptureInput
        from prototypes.assertion_memory.queue import _identity
        from prototypes.assertion_memory.tests.model_fixtures import with_literal_verification
        event = self.event('one', 'Сохраняем прозрачные окна.')
        old_id = _identity('assertion', event.id, event.prompt)
        self.queue.capture(CaptureInput(event.prompt, 'user', event.source, event.session_id,
                                       assertion_id=old_id))
        provider = with_literal_verification(lambda request: json.dumps(
            {'assertions': [event.prompt]} if request.task.startswith('Extract') else {'links': []}))
        LinkWorker(self.queue, MemoryEngine(self.store), provider).drain()
        assertions=self.store.list_assertions()
        self.assertEqual(len(assertions),2)
        self.assertIn(old_id,{row.assertion_id for row in assertions})
        direct=self.store.get_assertion(old_id)
        self.assertEqual(direct.metadata,{})
        sourced=next(row for row in assertions if row.assertion_id!=old_id)
        self.assertEqual(sourced.metadata['evidence_refs'][0]['event_id'],event.id)

    def test_shared_context_is_referenced_without_copying_its_body_into_claims(self):
        reason = self.event('one', ('Обоснование ' + 'сведения ' * 600).rstrip())
        reply = self.event('two', 'Так и делаем.')
        def provider(request):
            if request.task.startswith('Extract'):
                objects = [{'text': 'Пользователь подтвердил решение с общим обоснованием.',
                    'quote': reply.prompt, 'kind': 'decision',
                    'evidence': [{'event_id': reason.id, 'quote': reason.prompt, 'relation': 'reason'}]}]
                return json.dumps({'assertions': objects if request.records[0]['id'] == reply.id else []})
            if request.task.startswith('Verify'):
                return json.dumps({'verdicts': [{'index': 0, 'status': 'accepted', 'reason': 'Confirmed.'}]})
            return json.dumps({'links': []})
        LinkWorker(self.queue, MemoryEngine(self.store), provider).drain()
        assertion, = self.store.list_assertions()
        self.assertNotIn(reason.prompt, json.dumps(assertion.metadata, ensure_ascii=False))
        ref = next(row for row in assertion.metadata['evidence_refs'] if row['event_id'] == reason.id)
        self.assertEqual(self.queue.resolve_reference(ref)['text'], reason.prompt)

    def test_late_authenticated_antecedent_automatically_rechecks_unresolved_reply(self):
        reply = self.queue.capture_event('native', 'reply', 'Да.', source='native://reply', position=30)
        first = LinkWorker(self.queue, MemoryEngine(self.store), lambda request: json.dumps(
            {'source_coverage':{'status':'unresolved','reason':'No authenticated antecedent yet.','omissions':[]}}
            if request.task=='Review source material coverage' else {'assertions': []}))
        first.drain()
        self.assertEqual(self.queue.get(reply.id).status, 'completed')
        proposal = self.queue.capture_event('native', 'earlier', 'Оставим питание серверной 214?',
            actor='assistant', source='native://proposal', position=10, message_id='proposal')
        # Capture leaves a short marker; bounded worker metadata scanning wakes the audit.
        self.queue._maintenance_tick()
        self.assertEqual(self.queue.get(reply.id).status, 'pending')
        self.assertEqual(self.queue.get(reply.id).phase, 'audit')
        def provider(request):
            if request.task=='Review source material coverage':
                present=any(row.get('role')=='interpretation' for row in request.records)
                return json.dumps({'source_coverage':{'status':'accepted' if present else 'incomplete',
                    'reason':'The target assent now has an authenticated antecedent.',
                    'omissions':[] if present else [{'quote':reply.prompt,'reason':'The attributable assent is missing.'}]}})
            if request.task.startswith('Extract') or request.task=='Repair omitted source meanings':
                return json.dumps({'assertions': [{'text': 'Подтверждено сохранение питания серверной 214.',
                    'quote': 'Да.', 'kind': 'decision',
                    'evidence': [{'event_id': proposal.id, 'quote': proposal.prompt, 'relation': 'antecedent'}]}]})
            if request.task.startswith('Verify'):
                return json.dumps({'verdicts': [{'index': 0, 'status': 'accepted', 'reason': 'Now has an antecedent.'}]})
            return json.dumps({'links': []})
        LinkWorker(self.queue, MemoryEngine(self.store), provider).drain()
        self.assertEqual(len(self.store.list_assertions()), 1)
        history = self.queue.extraction_history(reply.id)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['assertion_ids'], [])
        self.assertEqual(len(history[1]['assertion_ids']), 1)
        self.assertEqual(self.queue.get_event(reply.id), reply)

    def test_late_context_rechecks_unresolved_part_without_duplicating_accepted_part(self):
        reply = self.queue.capture_event('native', 'reply', 'Окна прозрачные. И да.', source='native://reply', position=30)
        state = {'has_context': False}
        def provider(request):
            if request.task.startswith('Repair rejected'):
                return json.dumps({'assertions':[]})
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': [
                    {'text': 'Окна прозрачные.', 'quote': 'Окна прозрачные.', 'kind': 'assertion'},
                    {'text': 'Принят режим ночного отключения.', 'quote': 'И да.', 'kind': 'decision'}]})
            if request.task=='Review source material coverage':
                present=any(row.get('text')=='Принят режим ночного отключения.' for row in request.records)
                return json.dumps({'source_coverage':{'status':'accepted' if present else 'incomplete' if state['has_context'] else 'unresolved',
                    'reason':'The target contains an explicit window fact and a context-dependent assent.',
                    'omissions':[] if present or not state['has_context'] else [{'quote':'И да.','reason':'The sourced assent is missing.'}]}})
            if request.task=='Repair omitted source meanings':
                return json.dumps({'assertions':[{'text':'Принят режим ночного отключения.','quote':'И да.','kind':'decision'}]})
            if request.task.startswith('Verify'):
                return json.dumps({'verdicts': [{'index':row['index'],
                    'status':'accepted' if row['text']=='Окна прозрачные.' or state['has_context'] else 'unresolved',
                    'reason':'Explicit statement.' if row['text']=='Окна прозрачные.' else 'New antecedent.' if state['has_context'] else 'Missing antecedent.'}
                    for row in request.records if row.get('role')=='interpretation']})
            return json.dumps({'links': []})
        worker = LinkWorker(self.queue, MemoryEngine(self.store), provider)
        worker.drain()
        self.assertEqual(len(self.store.list_assertions()), 1)
        state['has_context'] = True
        self.queue.capture_event('native', 'earlier', 'Отключать корпус ночью?', actor='assistant',
                                 source='native://proposal', position=10, message_id='proposal')
        # Capture leaves a short marker; bounded worker metadata scanning wakes the audit.
        self.queue._maintenance_tick()
        self.assertEqual(self.queue.get(reply.id).status, 'pending')
        self.assertEqual(self.queue.get(reply.id).phase, 'audit')
        worker.drain()
        self.assertEqual(len(self.store.list_assertions()), 2)
        self.assertEqual(len(self.queue.extraction_history(reply.id)), 2)

    def test_model_work_can_extend_only_its_current_claim(self):
        from datetime import datetime, timedelta, timezone
        now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
        queue = CaptureQueue(self.store, clock=lambda: now[0], lease_seconds=10)
        event = queue.capture_event('long', 'one', 'Согласовали изменение.', source='chat://long')
        claim = queue._claim(with_model=True)
        now[0] += timedelta(seconds=9)
        queue.renew_claim(claim)
        now[0] += timedelta(seconds=9)
        self.assertIsNone(queue._claim(with_model=True))
        now[0] += timedelta(seconds=2)
        replacement = queue._claim(with_model=True)
        from prototypes.assertion_memory.queue import LeaseLost
        with self.assertRaises(LeaseLost):
            queue.renew_claim(claim)
        self.assertEqual(replacement.job.event_id, event.id)

    def test_context_order_reports_legacy_fallback_and_excluded_unpositioned_messages(self):
        legacy = self.event('one', 'Старая реплика без позиции.', actor='assistant')
        current = self.queue.capture_event('office', 'two', 'Въезд открыт.', source='chat://current', position=30)
        fallback = self.queue.context_order(legacy.id)
        self.assertEqual(fallback['basis'], 'intake_order')
        self.assertFalse(fallback['has_recorded_source_position'])
        verified = self.queue.context_order(current.id)
        self.assertEqual(verified['basis'], 'transcript_position')
        self.assertEqual(verified['excluded_unpositioned_event_count'], 1)
        self.assertEqual([row.id for row in self.queue.context_events(current.id)], [current.id])


if __name__ == '__main__':
    unittest.main()
