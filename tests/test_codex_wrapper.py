"""Обёртка Codex: перевод события и перевод ответа.

Полная обёртка — этап 7; здесь ровно то, что несёт этап 3: старт сессии и
«после сжатия», обе формы через один SessionStart харнеса.
"""

import io
import unittest

from jarvis import events as ev
from jarvis.response import Block, Context, Question, Silence
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
