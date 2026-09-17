"""Режим из трёх источников и признак автономии."""

import json
import tempfile
import unittest
from pathlib import Path

from jarvis import mode
from jarvis.autonomy import ENV_AUTONOMOUS, is_autonomous
from jarvis.storage import Storage


class ModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.storage = Storage(self.root / 'state', 'sess-1')
        self.personal = self.root / 'personal.json'
        self.source = self.root / 'source.json'

    def write(self, path: Path, table: dict) -> None:
        path.write_text(json.dumps(table), encoding='utf-8')

    def read(self, slug: str = 'probe') -> mode.ModeDecision:
        return mode.read(slug, self.storage, self.personal, self.source)

    def test_silent_everywhere_means_enabled(self) -> None:
        decision = self.read()
        self.assertTrue(decision.enabled)
        self.assertEqual(decision.source, mode.SOURCE_DEFAULT)

    def test_source_config_decides_when_the_others_are_silent(self) -> None:
        self.write(self.source, {'probe': False})
        self.assertEqual(self.read(), mode.ModeDecision(False, mode.SOURCE_CONFIG))

    def test_personal_config_beats_the_source_config(self) -> None:
        self.write(self.source, {'probe': False})
        self.write(self.personal, {'probe': True})
        self.assertEqual(self.read(), mode.ModeDecision(True, mode.SOURCE_PERSONAL))

    def test_session_beats_both_configs(self) -> None:
        self.write(self.source, {'probe': True})
        self.write(self.personal, {'probe': True})
        mode.set_session('probe', False, self.storage)
        self.assertEqual(self.read(), mode.ModeDecision(False, mode.SOURCE_SESSION))

    def test_a_source_that_speaks_about_another_module_does_not_decide(self) -> None:
        self.write(self.personal, {'other': False})
        self.write(self.source, {'probe': False})
        self.assertEqual(self.read(), mode.ModeDecision(False, mode.SOURCE_CONFIG))

    def test_switching_one_module_off_leaves_the_rest_alone(self) -> None:
        mode.set_session('probe', False, self.storage)
        self.assertFalse(self.read('probe').enabled)
        self.assertTrue(self.read('other').enabled)

    def test_broken_config_is_rejected_rather_than_ignored(self) -> None:
        self.source.write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.read()

    def test_only_a_real_boolean_decides(self) -> None:
        for value in (True, False):
            self.write(self.personal, {'probe': value})
            self.assertEqual(self.read().enabled, value)

    def test_the_string_false_is_an_error_not_an_enabled_module(self) -> None:
        # Раньше bool("false") давало True и молча оставляло замок включённым.
        self.write(self.personal, {'probe': 'false'})
        with self.assertRaises(ValueError) as caught:
            self.read()
        self.assertIn('probe', str(caught.exception))

    def test_zero_and_null_are_errors_not_a_disabled_module(self) -> None:
        for value in (0, None, '', 1, 'true'):
            self.write(self.personal, {'probe': value})
            with self.assertRaises(ValueError, msg=repr(value)):
                self.read()

    def test_a_bad_value_for_another_module_does_not_break_this_one(self) -> None:
        self.write(self.personal, {'other': 'false', 'probe': True})
        self.assertTrue(self.read('probe').enabled)


class AutonomyTest(unittest.TestCase):
    def test_flag_set_to_one_means_no_human(self) -> None:
        self.assertTrue(is_autonomous({ENV_AUTONOMOUS: '1'}))

    def test_anything_else_means_a_human_is_there(self) -> None:
        self.assertFalse(is_autonomous({}))
        self.assertFalse(is_autonomous({ENV_AUTONOMOUS: '0'}))
        self.assertFalse(is_autonomous({ENV_AUTONOMOUS: 'true'}))


if __name__ == '__main__':
    unittest.main()
