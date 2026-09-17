"""След и сверка подтверждения."""

import tempfile
import unittest
from pathlib import Path

from jarvis import confirm
from jarvis.storage import Storage
from jarvis.trace import Trace


def storage_at(root: Path, session_id: str = 'sess-1') -> Storage:
    return Storage(root, session_id)


class TraceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = storage_at(Path(self.tmp.name))
        self.trace = Trace(self.storage)

    def write(self, **overrides):
        fields = dict(
            event='prompt',
            module='probe',
            module_class='Module',
            response='silence',
            mode_enabled=True,
            mode_source='default',
            autonomous=False,
        )
        fields.update(overrides)
        return self.trace.write(**fields)

    def test_silence_is_written_too(self) -> None:
        self.write()
        self.assertEqual([line['response'] for line in self.trace.read()], ['silence'])

    def test_line_names_the_class_not_the_module(self) -> None:
        line = self.write(module='probe', module_class='ProbeModule')
        self.assertEqual(line.module, 'probe')
        self.assertEqual(line.module_class, 'ProbeModule')

    def test_mode_and_autonomy_are_visible_in_the_trace(self) -> None:
        self.write(mode_enabled=False, mode_source='session', autonomous=True)
        line = self.trace.read()[0]
        self.assertFalse(line['mode_enabled'])
        self.assertEqual(line['mode_source'], 'session')
        self.assertTrue(line['autonomous'])

    def test_trace_lives_in_the_session_zone(self) -> None:
        self.write()
        self.assertTrue((self.storage.session_dir / 'trace.jsonl').exists())

    def test_every_event_adds_a_line(self) -> None:
        self.write(event='prompt')
        self.write(event='pre-tool', response='deny', reason='нельзя')
        lines = self.trace.read()
        self.assertEqual([line['event'] for line in lines], ['prompt', 'pre-tool'])
        self.assertEqual(lines[1]['reason'], 'нельзя')


class ConfirmTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = storage_at(Path(self.tmp.name))
        confirm.request(self.storage, 'probe', 'снести ветку', 'снеси ветку')

    def test_phrase_matches_one_to_one(self) -> None:
        granted = confirm.match_prompt(self.storage, 'снеси ветку')
        self.assertEqual([g.action for g in granted], ['снести ветку'])

    def test_surrounding_whitespace_does_not_break_the_match(self) -> None:
        self.assertTrue(confirm.match_prompt(self.storage, '  снеси ветку \n'))

    def test_phrase_inside_a_longer_reply_is_not_a_confirmation(self) -> None:
        self.assertEqual(confirm.match_prompt(self.storage, 'не надо, снеси ветку потом'), [])

    def test_another_phrase_is_not_a_confirmation(self) -> None:
        self.assertEqual(confirm.match_prompt(self.storage, 'да'), [])

    def test_confirmation_covers_exactly_one_action(self) -> None:
        confirm.match_prompt(self.storage, 'снеси ветку')
        self.assertTrue(confirm.take(self.storage, 'probe', 'снести ветку'))
        self.assertFalse(confirm.take(self.storage, 'probe', 'снести ветку'))

    def test_nothing_to_take_without_a_matching_reply(self) -> None:
        self.assertFalse(confirm.take(self.storage, 'probe', 'снести ветку'))

    def test_matching_is_scoped_to_one_module(self) -> None:
        confirm.request(self.storage, 'other', 'своё', 'снеси ветку')
        granted = confirm.match_prompt(self.storage, 'снеси ветку', slug='probe')
        self.assertEqual([g.slug for g in granted], ['probe'])
        self.assertEqual([item['slug'] for item in confirm.pending(self.storage)], ['other'])

    def test_empty_phrase_is_rejected_at_the_request(self) -> None:
        with self.assertRaises(ValueError):
            confirm.request(self.storage, 'probe', 'действие', '   ')


if __name__ == '__main__':
    unittest.main()
