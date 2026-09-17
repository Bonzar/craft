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
        for claude_name, unified in claude.EVENT_BY_CLAUDE.items():
            self.assertEqual(claude.CLAUDE_BY_EVENT[unified], claude_name)

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

    def test_permission_decisions_are_not_available_outside_pre_tool(self) -> None:
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
