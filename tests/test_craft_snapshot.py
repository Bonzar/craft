"""Поставщик снимка Craft: повторы, алерт вместо половины снимка, склейка.

Живой Craft тут не нужен и не годится — проверяется то, что решено кодом:
сколько раз поставщик пробует, что отдаёт при отказе и что никогда не отдаёт
половину.
"""

import importlib.util
import os
import tempfile
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


class ProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.slept: list[float] = []
        for name, value in (('CRAFT_SYNC_BIN', None), ('CRAFT_API_BASE', 'https://example/api')):
            previous = os.environ.get(name)
            self.addCleanup(
                lambda n=name, v=previous: os.environ.pop(n, None) if v is None
                else os.environ.__setitem__(n, v)
            )
            if value is not None:
                os.environ[name] = value

    def stub(self, body: str) -> Path:
        """Заглушка craft-sync плюс журнал её вызовов."""
        self.calls = self.root / 'calls.log'
        path = self.root / 'craft-sync'
        path.write_text(f'#!/bin/sh\necho "$@" >> {self.calls}\n{body}\n', encoding='utf-8')
        path.chmod(0o755)
        os.environ['CRAFT_SYNC_BIN'] = str(path)
        return path

    def call_lines(self) -> list[str]:
        return self.calls.read_text(encoding='utf-8').splitlines() if self.calls.exists() else []

    def provide(self, journal=None) -> str:
        return provider.provide(Event(), journal or Journal(), sleep=self.slept.append)

    # --- повторы ---

    def test_a_failing_call_is_retried_three_times_with_growing_pauses(self) -> None:
        self.stub('echo "не достучались" >&2\nexit 1')
        journal = Journal()
        self.provide(journal)
        # Три попытки на первом же чтении, дальше поставщик не идёт.
        self.assertEqual(len(self.call_lines()), provider.ATTEMPTS)
        self.assertEqual(self.slept, list(provider.BACKOFF_SECONDS[:provider.ATTEMPTS - 1]))
        self.assertIn('craft-sync вернул 1', journal.lines[0])

    def test_a_call_that_succeeds_is_not_retried(self) -> None:
        self.stub('echo "прочитано $2"')
        self.provide()
        self.assertEqual(len(self.call_lines()), 2)  # два чтения, по одной попытке
        self.assertEqual(self.slept, [])

    def test_a_late_success_is_taken(self) -> None:
        # Первая попытка падает, вторая отвечает: снимок собирается.
        self.stub(f'''
            n=$(wc -l < {self.root / 'calls.log'})
            if [ "$n" -le 1 ]; then echo сеть >&2; exit 1; fi
            echo "прочитано $2"
        ''')
        text = self.provide()
        self.assertIn(provider.RULES_TITLE, text)
        self.assertIn(provider.MEMORY_TITLE, text)
        self.assertNotIn('не собран', text)

    # --- алерт вместо половины снимка ---

    def test_a_snapshot_is_never_delivered_in_half(self) -> None:
        # Правила читаются, память падает — отдать первую половину нельзя:
        # агент решил бы, что правила базы у него полные.
        self.stub(f'''
            if [ "$2" = "{provider.MEMORY_ROOT}" ]; then echo нет >&2; exit 1; fi
            echo "правила базы целиком"
        ''')
        journal = Journal()
        text = self.provide(journal)
        self.assertTrue(text.startswith('стартовый контекст: снимок Craft не собран:'), text)
        self.assertNotIn('правила базы целиком', text)
        self.assertIn('Память агента', text)  # названа причина: что именно не прочиталось
        self.assertIn('alert', journal.lines[0])

    def test_the_alert_is_short_and_names_the_reason(self) -> None:
        self.stub('echo "сервер лёг" >&2\nexit 1')
        text = self.provide()
        self.assertLess(len(text), 400, text)
        self.assertIn('сервер лёг', text)

    def test_no_binary_is_an_alert_and_not_a_crash(self) -> None:
        os.environ['CRAFT_SYNC_BIN'] = str(self.root / 'нет-такого')
        with unittest.mock.patch.object(provider, 'DEFAULT_BINARY', str(self.root / 'нет-и-тут')), \
                unittest.mock.patch.dict(os.environ, {'PATH': str(self.root)}):
            text = provider.provide(Event(), Journal(), sleep=self.slept.append)
        self.assertIn('не найден', text)
        self.assertTrue(text.startswith('стартовый контекст: снимок Craft не собран:'))

    def test_without_the_api_base_nothing_is_called_at_all(self) -> None:
        self.stub('echo был-вызов')
        os.environ.pop('CRAFT_API_BASE')
        text = self.provide()
        self.assertIn('CRAFT_API_BASE', text)
        self.assertEqual(self.call_lines(), [])

    # --- склейка и аргументы ---

    def test_both_roots_are_read_under_their_own_titles(self) -> None:
        self.stub('echo "прочитано $2"')
        text = self.provide()
        self.assertIn(f'{provider.RULES_TITLE}\n\nпрочитано {provider.RULES_ROOT}', text)
        self.assertIn(f'{provider.MEMORY_TITLE}\n\nпрочитано {provider.MEMORY_ROOT}', text)

    def test_retries_of_craft_sync_itself_are_switched_off(self) -> None:
        # Политика повторов одна и живёт здесь, а не в инструменте.
        self.stub('echo "прочитано $2"')
        self.provide()
        for line in self.call_lines():
            self.assertIn('--retries 0', line)
            self.assertIn('--rl-retries 0', line)
            self.assertIn(f'--timeout {provider.REQUEST_TIMEOUT}', line)
        self.assertIn('--follow-links', self.call_lines()[0])
        self.assertNotIn('--follow-links', self.call_lines()[1])

    def test_the_worst_case_fits_the_hook_timeout(self) -> None:
        # Худший случай обязан помещаться в timeout строки хука, иначе харнес
        # убьёт сбор на полпути; число берём из установщика, а не на глаз.
        import importlib.machinery
        import importlib.util as iu
        from . import INSTALLER
        spec = iu.spec_from_loader(
            'jarvis_install_for_timeout',
            importlib.machinery.SourceFileLoader('jarvis_install_for_timeout', str(INSTALLER)),
        )
        installer = iu.module_from_spec(spec)
        spec.loader.exec_module(installer)
        worst = 2 * (provider.ATTEMPTS * provider.CALL_TIMEOUT + sum(provider.BACKOFF_SECONDS))
        self.assertLess(worst, installer.HOOK_TIMEOUT_SEC, f'худший случай {worst} с')


if __name__ == '__main__':
    unittest.main()
