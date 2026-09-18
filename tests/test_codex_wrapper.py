"""Обёртка Codex: перевод события и перевод ответа.

Полная обёртка — этап 7; здесь то, что несёт этап 3: старт сессии и «после
сжатия», обе формы через один SessionStart харнеса, плюс те из трёх заложенных
заранее событий, что у Codex есть, — запрос разрешения и старт подагента.
"""

import io
import unittest

from jarvis import events as ev
from jarvis.response import Allow, Block, Context, Deny, Question, Silence
from jarvis.wrappers import codex


def raw(name: str = 'SessionStart', **fields) -> dict:
    base = {'hook_event_name': name, 'session_id': 'sess-1', 'cwd': '/work'}
    base.update(fields)
    return base


class EventTest(unittest.TestCase):
    def test_session_start_is_split_by_its_source(self) -> None:
        # замер 17.09.2026: после сжатия Codex перезапускает тот же хук и ставит
        # source=compact, как Claude.
        for source in ('startup', 'resume'):
            self.assertEqual(codex.to_event(raw(source=source)).event, ev.SESSION_START, source)
        self.assertEqual(codex.to_event(raw(source='compact')).event, ev.AFTER_COMPACT)

    def test_without_a_source_it_is_the_start(self) -> None:
        self.assertEqual(codex.to_event(raw()).event, ev.SESSION_START)

    def test_both_our_events_ride_one_harness_event(self) -> None:
        self.assertEqual(codex.CODEX_BY_EVENT[ev.SESSION_START], 'SessionStart')
        self.assertEqual(codex.CODEX_BY_EVENT[ev.AFTER_COMPACT], 'SessionStart')

    def test_common_fields_reach_the_unified_event(self) -> None:
        event = codex.to_event(raw(session_id='s-7', cwd='/here', source='startup'))
        self.assertEqual(event.session_id, 's-7')
        self.assertEqual(event.cwd, '/here')
        self.assertEqual(event.harness, 'codex')

    def test_raw_event_is_carried_as_is(self) -> None:
        source = raw(source='compact', model='gpt', transcript_path='/t')
        self.assertEqual(codex.to_event(source).raw, source)

    def test_an_event_the_stage_does_not_carry_is_named(self) -> None:
        with self.assertRaises(ValueError) as caught:
            codex.to_event(raw('PreToolUse'))
        self.assertIn('PreToolUse', str(caught.exception))

    def test_the_model_message_event_codex_does_not_have_at_all(self) -> None:
        # замер 18.09.2026: в списке хуков Codex такого события нет, поэтому
        # его нет и в карте — установщик по ней и предупреждает.
        self.assertNotIn(ev.MODEL_MESSAGE, codex.CODEX_BY_EVENT)


class ThreeLaidInEventsTest(unittest.TestCase):
    """Те из трёх заложенных событий, что у Codex есть."""

    def test_permission_request_carries_the_call(self) -> None:
        event = codex.to_event(raw(
            'PermissionRequest',
            tool_name='Bash',
            tool_input={'command': "printf 'ЗАМЕР-К2' > /etc/probe.txt",
                        'description': 'Разрешить запись?'},
        ))
        self.assertEqual(event.event, ev.PERMISSION_REQUEST)
        self.assertEqual(event.tool_name, 'Bash')
        self.assertEqual(event.tool_input['command'], "printf 'ЗАМЕР-К2' > /etc/probe.txt")

    def test_codex_offers_the_human_no_options(self) -> None:
        # замер: списка вариантов Codex на запросе разрешения не даёт вовсе.
        event = codex.to_event(raw('PermissionRequest', tool_name='Bash', tool_input={}))
        self.assertEqual(event.permission_options, ())

    def test_subagent_start_carries_the_subagent_id_and_type(self) -> None:
        event = codex.to_event(raw('SubagentStart', agent_id='a-1', agent_type='reviewer'))
        self.assertEqual(event.event, ev.SUBAGENT_START)
        self.assertEqual(event.agent_id, 'a-1')
        self.assertEqual(event.agent_type, 'reviewer')

    def test_deny_becomes_a_decision_object(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        delivery = codex.translate(ev.PERMISSION_REQUEST, Deny('замок не пускает'))
        self.assertEqual(
            delivery.payload,
            {'hookSpecificOutput': {'hookEventName': 'PermissionRequest',
                                    'decision': {'behavior': 'deny',
                                                 'message': 'замок не пускает'}}},
        )
        codex.emit(delivery, stdout=out, stderr=err)
        self.assertIn('"behavior": "deny"', out.getvalue())

    def test_allow_carries_no_reason(self) -> None:
        delivery = codex.translate(ev.PERMISSION_REQUEST, Allow('проверено'))
        self.assertEqual(
            delivery.payload,
            {'hookSpecificOutput': {'hookEventName': 'PermissionRequest',
                                    'decision': {'behavior': 'allow'}}},
        )

    def test_subagent_start_takes_context_as_a_structure(self) -> None:
        delivery = codex.translate(ev.SUBAGENT_START, Context('вот что помнит сессия'))
        self.assertEqual(
            delivery.payload,
            {'hookSpecificOutput': {'hookEventName': 'SubagentStart',
                                    'additionalContext': 'вот что помнит сессия'}},
        )

    def test_a_question_to_the_human_is_not_supported(self) -> None:
        for event in (ev.PERMISSION_REQUEST, ev.SUBAGENT_START):
            delivery = codex.translate(event, Question('так ли?'))
            self.assertFalse(delivery.supported, event)
            self.assertIsNone(delivery.payload)

    def test_silence_stays_silence(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        for event in (ev.PERMISSION_REQUEST, ev.SUBAGENT_START):
            codex.emit(codex.translate(event, Silence(reason='проба')), stdout=out, stderr=err)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), '')


class TranslateTest(unittest.TestCase):
    def test_context_goes_to_stdout_as_text(self) -> None:
        for event in (ev.SESSION_START, ev.AFTER_COMPACT):
            delivery = codex.translate(event, Context('вот что я помню'))
            self.assertTrue(delivery.supported, event)
            out, err = io.StringIO(), io.StringIO()
            self.assertEqual(codex.emit(delivery, stdout=out, stderr=err), 0)
            self.assertEqual(out.getvalue(), 'вот что я помню')
            self.assertEqual(err.getvalue(), '')

    def test_silence_prints_nothing(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        codex.emit(codex.translate(ev.SESSION_START, Silence()), stdout=out, stderr=err)
        self.assertEqual(out.getvalue(), '')

    def test_a_form_the_stage_does_not_carry_is_named_not_swapped(self) -> None:
        for form in (Block('нельзя'), Question('что делать?')):
            delivery = codex.translate(ev.SESSION_START, form)
            self.assertFalse(delivery.supported)
            self.assertIsNone(delivery.payload)
            out, err = io.StringIO(), io.StringIO()
            codex.emit(delivery, stdout=out, stderr=err)
            self.assertEqual(out.getvalue(), '')
            self.assertIn('этапа 3', err.getvalue())


if __name__ == '__main__':
    unittest.main()
