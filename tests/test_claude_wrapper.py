"""Обёртка Claude: перевод события и перевод ответа."""

import io
import unittest

from jarvis import events as ev
from jarvis import response as forms
from jarvis.response import Allow, Ask, Block, Context, Deny, Question, Silence, UpdatedInput
from jarvis.wrappers import claude


def raw(name: str, **fields) -> dict:
    base = {'hook_event_name': name, 'session_id': 'sess-1', 'cwd': '/work'}
    base.update(fields)
    return base


class EventTranslationTest(unittest.TestCase):
    def test_every_unified_event_has_a_claude_name(self) -> None:
        self.assertEqual(sorted(claude.CLAUDE_BY_EVENT), sorted(ev.ALL))

    def test_the_two_tables_are_inverses(self) -> None:
        for claude_name, group in claude.CLAUDE_EVENTS.items():
            for unified in group:
                self.assertEqual(claude.CLAUDE_BY_EVENT[unified], claude_name)

    def test_session_start_is_split_by_its_source(self) -> None:
        # Одно имя события харнеса, два единых: старт сессии и «после сжатия».
        for source in ('startup', 'resume', 'clear'):
            self.assertEqual(claude.to_event(raw('SessionStart', source=source)).event,
                             ev.SESSION_START, source)
        self.assertEqual(claude.to_event(raw('SessionStart', source='compact')).event,
                         ev.AFTER_COMPACT)

    def test_session_start_without_a_source_is_the_start(self) -> None:
        self.assertEqual(claude.to_event(raw('SessionStart')).event, ev.SESSION_START)

    def test_after_compact_takes_context(self) -> None:
        delivery = claude.translate(ev.AFTER_COMPACT, Context('вот что я помню'))
        self.assertEqual(
            delivery.payload,
            {'hookSpecificOutput': {'hookEventName': 'SessionStart',
                                    'additionalContext': 'вот что я помню'}},
        )

    def test_an_event_outside_the_catalog_is_named(self) -> None:
        with self.assertRaises(ValueError) as caught:
            claude.to_event(raw('WorktreeCreate'))
        self.assertIn('WorktreeCreate', str(caught.exception))

    def test_common_fields_reach_the_unified_event(self) -> None:
        event = claude.to_event(raw('UserPromptSubmit', prompt='привет'))
        self.assertEqual(event.event, ev.PROMPT)
        self.assertEqual(event.session_id, 'sess-1')
        self.assertEqual(event.cwd, '/work')
        self.assertEqual(event.harness, 'claude')
        self.assertEqual(event.prompt_text, 'привет')

    def test_raw_event_is_carried_as_is(self) -> None:
        source = raw('Stop', stop_hook_active=True, last_assistant_message='готово')
        self.assertEqual(claude.to_event(source).raw, source)

    def test_one_pre_tool_event_carries_the_tool_name_inside(self) -> None:
        event = claude.to_event(raw('PreToolUse', tool_name='Bash', tool_input={'command': 'ls'}))
        self.assertEqual(event.event, ev.PRE_TOOL)
        self.assertEqual(event.tool_name, 'Bash')
        self.assertEqual(event.tool_input, {'command': 'ls'})

    def test_tool_error_carries_the_error_text(self) -> None:
        event = claude.to_event(
            raw('PostToolUseFailure', tool_name='Bash', tool_input={'command': 'npm test'},
                error='Exit code 1\nCannot find module')
        )
        self.assertEqual(event.event, ev.TOOL_ERROR)
        self.assertEqual(event.tool_name, 'Bash')
        self.assertIn('Exit code 1', event.error)

    def test_events_without_an_error_leave_the_field_empty(self) -> None:
        self.assertIsNone(claude.to_event(raw('UserPromptSubmit', prompt='привет')).error)

    def test_post_tool_carries_the_call_result(self) -> None:
        event = claude.to_event(raw('PostToolUse', tool_name='Write', tool_response={'type': 'create'}))
        self.assertEqual(event.tool_result, {'type': 'create'})

    def test_human_answer_is_filled_from_the_question_tool_result(self) -> None:
        event = claude.to_event(
            raw('PostToolUse', tool_name='AskUserQuestion', tool_response={'answers': {'Сносим?': 'да'}})
        )
        self.assertEqual(event.human_answer, {'Сносим?': 'да'})

    def test_human_answer_is_empty_for_other_tools(self) -> None:
        event = claude.to_event(raw('PostToolUse', tool_name='Bash', tool_response={'stdout': ''}))
        self.assertIsNone(event.human_answer)

    def test_unknown_claude_event_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            claude.to_event(raw('WorktreeCreate'))


# Сырые события трёх заложенных заранее хуков — дословно из замера 18.09.2026
# (Claude Code 2.1.276, `claude -p --permission-mode default` в контейнере
# облачной сессии). Лишние общие поля из замера тут опущены: их проверяют
# соседние тесты.
PERMISSION_REQUEST_RAW = raw(
    'PermissionRequest',
    permission_mode='default',
    tool_name='Bash',
    tool_input={'command': "echo 'ЗАМЕР-4' > marker.txt", 'description': 'Create marker.txt'},
    permission_suggestions=[
        {'type': 'addDirectories', 'directories': ['/work'], 'destination': 'session'},
        {'type': 'setMode', 'mode': 'acceptEdits', 'destination': 'session'},
    ],
)
SUBAGENT_START_RAW = raw(
    'SubagentStart',
    agent_id='af53b93ea6e4abebd',
    agent_type='general-purpose',
)
MESSAGE_DISPLAY_RAW = raw(
    'MessageDisplay',
    turn_id='28d6b096-1571-44f9-95b3-b008c8d5400f',
    message_id='e59d45d2-42c6-4a6f-bf88-a33f11302838',
    index=0,
    final=True,
    delta='ФИОЛЕТ',
)


class ThreeLaidInEventsTest(unittest.TestCase):
    """Три события, заложенные до первого модуля на них."""

    def test_permission_request_carries_the_call_and_what_is_offered_to_the_human(self) -> None:
        event = claude.to_event(PERMISSION_REQUEST_RAW)
        self.assertEqual(event.event, ev.PERMISSION_REQUEST)
        self.assertEqual(event.tool_name, 'Bash')
        self.assertEqual(event.tool_input['command'], "echo 'ЗАМЕР-4' > marker.txt")
        self.assertEqual(len(event.permission_options), 2)
        self.assertEqual(event.permission_options[0]['type'], 'addDirectories')

    def test_permission_request_without_offers_leaves_the_field_empty(self) -> None:
        # замер: список вариантов бывает пустым, и это не ошибка.
        event = claude.to_event(raw('PermissionRequest', tool_name='Bash', tool_input={},
                                    permission_suggestions=[]))
        self.assertEqual(event.permission_options, ())

    def test_subagent_start_carries_the_subagent_id_and_type(self) -> None:
        event = claude.to_event(SUBAGENT_START_RAW)
        self.assertEqual(event.event, ev.SUBAGENT_START)
        self.assertEqual(event.agent_id, 'af53b93ea6e4abebd')
        self.assertEqual(event.agent_type, 'general-purpose')

    def test_model_message_carries_the_text(self) -> None:
        event = claude.to_event(MESSAGE_DISPLAY_RAW)
        self.assertEqual(event.event, ev.MODEL_MESSAGE)
        self.assertEqual(event.message_text, 'ФИОЛЕТ')

    def test_deny_on_a_permission_request_has_its_own_decision_shape(self) -> None:
        # замер: решение лежит в `decision`, а не в `permissionDecision`.
        delivery = claude.translate(ev.PERMISSION_REQUEST, Deny('замок не пускает'))
        self.assertEqual(
            delivery.payload,
            {'hookSpecificOutput': {'hookEventName': 'PermissionRequest',
                                    'decision': {'behavior': 'deny',
                                                 'message': 'замок не пускает'}}},
        )

    def test_allow_on_a_permission_request_carries_no_reason(self) -> None:
        # Поля причины у «разрешить» в схеме харнеса нет — она остаётся в следе.
        delivery = claude.translate(ev.PERMISSION_REQUEST, Allow('проверено'))
        self.assertEqual(
            delivery.payload,
            {'hookSpecificOutput': {'hookEventName': 'PermissionRequest',
                                    'decision': {'behavior': 'allow'}}},
        )

    def test_subagent_start_takes_context(self) -> None:
        delivery = claude.translate(ev.SUBAGENT_START, Context('вот что помнит сессия'))
        self.assertEqual(
            delivery.payload,
            {'hookSpecificOutput': {'hookEventName': 'SubagentStart',
                                    'additionalContext': 'вот что помнит сессия'}},
        )

    def test_a_question_to_the_human_is_not_supported_on_the_three(self) -> None:
        for event in (ev.PERMISSION_REQUEST, ev.SUBAGENT_START, ev.MODEL_MESSAGE):
            delivery = claude.translate(event, Question('так ли?'))
            self.assertFalse(delivery.supported, event)
            self.assertIsNone(delivery.payload)

    def test_model_message_takes_no_answer_at_all(self) -> None:
        for response in (Context('текст'), Allow(''), Deny('нет'), Block('нет')):
            delivery = claude.translate(ev.MODEL_MESSAGE, response)
            self.assertFalse(delivery.supported, response.kind)

    def test_silence_stays_silence_on_all_three(self) -> None:
        for event in (ev.PERMISSION_REQUEST, ev.SUBAGENT_START, ev.MODEL_MESSAGE):
            delivery = claude.translate(event, Silence(reason='проба'))
            self.assertTrue(delivery.supported, event)
            self.assertIsNone(delivery.payload)


class ResponseTranslationTest(unittest.TestCase):
    def test_silence_prints_nothing_on_every_event(self) -> None:
        for event in ev.ALL:
            delivery = claude.translate(event, Silence())
            self.assertIsNone(delivery.payload, event)
            self.assertEqual(delivery.exit_code, 0)

    def test_context_becomes_additional_context(self) -> None:
        payload = claude.translate(ev.POST_TOOL, Context('так-то')).payload
        self.assertEqual(
            payload,
            {'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': 'так-то'}},
        )

    def test_allow_deny_and_ask_become_a_permission_decision(self) -> None:
        for response, expected in (
            (Allow('ок'), 'allow'),
            (Deny('нельзя'), 'deny'),
            (Ask(reason='точно?', phrase='да'), 'ask'),
        ):
            decision = claude.translate(ev.PRE_TOOL, response).payload['hookSpecificOutput']
            self.assertEqual(decision['permissionDecision'], expected)

    def test_ask_carries_the_confirmation_phrase_to_the_human(self) -> None:
        decision = claude.translate(ev.PRE_TOOL, Ask(reason='точно?', phrase='снеси ветку')).payload
        self.assertIn('снеси ветку', decision['hookSpecificOutput']['permissionDecisionReason'])

    def test_updated_input_replaces_the_whole_input(self) -> None:
        payload = claude.translate(ev.PRE_TOOL, UpdatedInput({'command': 'ls -la'})).payload
        self.assertEqual(payload['hookSpecificOutput']['updatedInput'], {'command': 'ls -la'})

    def test_block_becomes_a_top_level_decision(self) -> None:
        for event in (ev.STOP, ev.SUBAGENT_STOP, ev.POST_TOOL):
            payload = claude.translate(event, Block('доделай')).payload
            self.assertEqual(payload, {'decision': 'block', 'reason': 'доделай'}, event)

    def test_question_becomes_an_order_to_call_the_question_tool(self) -> None:
        payload = claude.translate(ev.PROMPT, Question('Сносим?', ('да', 'нет')), slug='probe').payload
        text = payload['hookSpecificOutput']['additionalContext']
        self.assertIn(claude.QUESTION_TOOL, text)
        self.assertIn('Сносим?', text)
        self.assertIn('да; нет', text)
        self.assertIn('probe', text)


class UnsupportedTest(unittest.TestCase):
    def assert_not_delivered(self, event: str, response) -> None:
        delivery = claude.translate(event, response)
        self.assertFalse(delivery.supported, f'{event}/{response.kind}')
        self.assertIsNone(delivery.payload)
        self.assertTrue(delivery.note)

    def test_block_is_not_available_before_a_call(self) -> None:
        self.assert_not_delivered(ev.PRE_TOOL, Block('нет'))

    def test_block_is_not_available_after_a_failed_call(self) -> None:
        self.assert_not_delivered(ev.TOOL_ERROR, Block('нет'))

    def test_permission_decisions_are_not_available_outside_the_two_call_events(self) -> None:
        # Решение принимают только «перед вызовом» и «запрос разрешения».
        for event in (ev.PROMPT, ev.POST_TOOL, ev.STOP, ev.SESSION_START):
            self.assert_not_delivered(event, Deny('нет'))

    def test_session_end_and_notification_take_nothing(self) -> None:
        for event in (ev.SESSION_END, ev.NOTIFICATION):
            self.assert_not_delivered(event, Context('текст'))

    def test_pre_compact_takes_no_context(self) -> None:
        self.assert_not_delivered(ev.PRE_COMPACT, Context('текст'))

    def test_every_event_has_a_note_about_what_the_harness_cannot_do(self) -> None:
        self.assertEqual(sorted(claude.UNSUPPORTED_NOTE), sorted(ev.ALL))

    def test_supported_table_covers_every_event(self) -> None:
        self.assertEqual(sorted(claude.SUPPORTED), sorted(ev.ALL))
        for event, kinds in claude.SUPPORTED.items():
            self.assertTrue(kinds <= set(forms.ALL_KINDS), event)


class EmitTest(unittest.TestCase):
    def test_payload_goes_to_stdout_and_the_note_to_stderr(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        claude.emit(claude.translate(ev.STOP, Block('доделай')), stdout=out, stderr=err)
        self.assertEqual(out.getvalue(), '{"decision": "block", "reason": "доделай"}')
        self.assertEqual(err.getvalue(), '')

    def test_unsupported_form_prints_nothing_and_names_the_reason(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        code = claude.emit(claude.translate(ev.TOOL_ERROR, Block('нет')), stdout=out, stderr=err)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('блока там нет', err.getvalue())
        self.assertEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
