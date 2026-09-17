"""Поставщик снимка Craft: бюджет сбора и поведение при отказе.

Живой Craft тут не нужен и не годится — проверяется то, что решено кодом:
сколько поставщик готов ждать и что он отдаёт, когда не дождался.
"""

import importlib.util
import os
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

from . import MODULES_DIR


def load_provider():
    spec = importlib.util.spec_from_file_location(
        'craft_snapshot_lib', MODULES_DIR / 'craft-snapshot' / 'lib' / '__init__.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


provider = load_provider()


class Event:
    event = 'session-start'


class Journal:
    """Хранилище ровно в той части, которой пользуется поставщик."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def append_line(self, name, line, zone='session'):
        self.lines.append(line)


class BudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name, value in (('CRAFT_SYNC_BIN', None), ('CRAFT_API_BASE', 'https://example/api')):
            previous = os.environ.get(name)
            self.addCleanup(
                lambda n=name, v=previous: os.environ.pop(n, None) if v is None else os.environ.__setitem__(n, v)
            )
            if value is not None:
                os.environ[name] = value

    def stub(self, body: str) -> Path:
        path = self.root / 'craft-sync'
        path.write_text(body, encoding='utf-8')
        path.chmod(0o755)
        os.environ['CRAFT_SYNC_BIN'] = str(path)
        return path

    def test_the_whole_collection_fits_the_budget(self) -> None:
        # Вызовов два, и два независимых таймаута сложились бы в сумму, которой
        # не хватает ни харнесу, ни остальным копиям базы.
        self.stub('#!/bin/sh\nsleep 30\n')
        journal = Journal()
        started = time.monotonic()
        with unittest.mock.patch.object(provider, 'TOTAL_BUDGET', 1.0):
            text = provider.provide(Event(), journal)
        spent = time.monotonic() - started
        self.assertEqual(text, '')
        self.assertLess(spent, 3.0, 'сбор вышел за общий бюджет')
        self.assertTrue(any('Timeout' in line or 'бюджет' in line for line in journal.lines), journal.lines)

    def test_a_failing_call_gives_empty_text_and_a_reason(self) -> None:
        self.stub('#!/bin/sh\necho "не достучались" >&2\nexit 1\n')
        journal = Journal()
        self.assertEqual(provider.provide(Event(), journal), '')
        self.assertIn('craft-sync вернул 1', journal.lines[0])

    def test_no_binary_is_a_reason_and_not_a_crash(self) -> None:
        # Ни переменной, ни пути по умолчанию, ни PATH: бинарника нет нигде.
        os.environ['CRAFT_SYNC_BIN'] = str(self.root / 'нет-такого')
        journal = Journal()
        with unittest.mock.patch.object(provider, 'DEFAULT_BINARY', str(self.root / 'нет-и-тут')), \
                unittest.mock.patch.dict(os.environ, {'PATH': str(self.root)}):
            self.assertEqual(provider.provide(Event(), journal), '')
        self.assertIn('не найден', journal.lines[0])

    def test_without_the_api_base_it_does_not_call_anything(self) -> None:
        self.stub('#!/bin/sh\necho был-вызов\n')
        os.environ.pop('CRAFT_API_BASE')
        journal = Journal()
        self.assertEqual(provider.provide(Event(), journal), '')
        self.assertIn('CRAFT_API_BASE', journal.lines[0])

    def test_what_both_calls_read_is_glued_under_its_own_title(self) -> None:
        self.stub('#!/bin/sh\necho "прочитано $2"\n')  # $1 — --markdown, $2 — id блока
        text = provider.provide(Event(), Journal())
        self.assertIn(provider.RULES_TITLE, text)
        self.assertIn(provider.MEMORY_TITLE, text)
        self.assertIn(provider.RULES_ROOT, text)
        self.assertIn(provider.MEMORY_ROOT, text)


if __name__ == '__main__':
    unittest.main()
