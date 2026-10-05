"""Поставщик снимка Craft: повторы, алерт вместо половины снимка, склейка.

Живой Craft тут не нужен и не годится — проверяется то, что решено кодом:
сколько раз поставщик пробует, что отдаёт при отказе и что никогда не отдаёт
половину.
"""

import importlib.util
import os
import subprocess
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
        # Тесты не должны зависеть от настоящего секретного `.env` владельца
        # репозитория: конкретный путь подставляет только тест dotenv.
        self.env_lookup = unittest.mock.patch.object(provider, 'repository_env', return_value=None)
        self.env_lookup.start()
        self.addCleanup(self.env_lookup.stop)

    def stub(self, body: str) -> Path:
        """Заглушка craft-sync плюс журнал её вызовов."""
        self.calls = self.root / 'calls.log'
        self.calls.unlink(missing_ok=True)  # заглушка ставится не раз за тест
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

    def test_a_network_error_walks_the_short_ladder(self) -> None:
        self.stub('echo "HTTP 503: шлюз лёг" >&2\nexit 1')
        journal = Journal()
        self.provide(journal)
        # Пауза стоит между попытками, поэтому попыток на одну больше, чем пауз,
        # и все паузы лестницы отрабатывают. Дальше первого чтения не идём.
        self.assertEqual(len(self.call_lines()), provider.ATTEMPTS)
        self.assertEqual(self.slept, list(provider.BACKOFF_SECONDS))
        self.assertIn('HTTP 503', journal.lines[0])

    def test_the_short_ladder_is_the_one_from_the_readme(self) -> None:
        # Числа названы решением, а не выведены кодом: 1, 3, 9 с.
        self.assertEqual(provider.BACKOFF_SECONDS, (1, 3, 9))
        self.assertEqual(provider.ATTEMPTS, 4)

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

    def test_a_permanent_error_is_not_retried(self) -> None:
        # 404 не станет найденным со второго раза, 401 — авторизованным:
        # повтор здесь только тратит время старта сессии.
        for stderr in ('! markdown: X: not found (HTTP 404)', 'HTTP 401: кто ты',
                       'HTTP 400: так нельзя', 'HTTP 403: нет доступа'):
            with self.subTest(stderr=stderr):
                self.slept.clear()
                self.stub(f'echo "{stderr}" >&2\nexit 1')
                text = self.provide()
                self.assertEqual(len(self.call_lines()), 1, 'постоянную ошибку повторили')
                self.assertEqual(self.slept, [])
                self.assertTrue(text.startswith('стартовый контекст: снимок Craft не собран:'))

    def test_a_budget_error_walks_the_long_ladder(self) -> None:
        # 429 — окно лимита у connect-ссылки: оно живёт десятками секунд, и
        # короткой лестницей его не пересидеть. Ждём по длинной.
        self.stub('echo "HTTP 429: Rate limit exceeded" >&2\nexit 1')
        self.provide()
        self.assertEqual(len(self.call_lines()), provider.ATTEMPTS_429)
        self.assertEqual(self.slept, list(provider.BACKOFF_429_SECONDS))
        self.assertGreater(provider.ATTEMPTS_429, provider.ATTEMPTS)

    def test_the_long_ladder_is_the_one_from_craft_sync(self) -> None:
        # Те же числа, что у `--rl-retries` в самом инструменте: 5, 10, 20, 40, 60 с.
        self.assertEqual(provider.BACKOFF_429_SECONDS, (5, 10, 20, 40, 60))
        self.assertEqual(provider.ATTEMPTS_429, 6)

    def test_the_ladder_follows_the_last_problem_and_not_the_first(self) -> None:
        # Первый ответ — 429, дальше сеть: длинная пауза отрабатывает один раз,
        # а считаются попытки уже по короткой лестнице.
        self.stub(f'''
            n=$(wc -l < {self.root / 'calls.log'})
            if [ "$n" -le 1 ]; then echo "HTTP 429: Rate limit exceeded" >&2
            else echo "сеть отвалилась" >&2; fi
            exit 1
        ''')
        text = self.provide()
        self.assertEqual(self.slept, [provider.BACKOFF_429_SECONDS[0], *provider.BACKOFF_SECONDS[1:]])
        self.assertEqual(len(self.call_lines()), provider.ATTEMPTS)
        self.assertIn('сеть отвалилась', text)  # наружу идёт последняя беда

    def test_a_timeout_is_retried(self) -> None:
        # Бесконечный shell-цикл не оставляет дочерний `sleep` с теми же
        # pipe-дескрипторами: запись вызова успевает попасть в журнал до
        # таймаута, и счётчик попыток не зависит от планировщика процессов.
        self.stub('while :; do :; done')
        # При 0,2 с финальный дочерний процесс иногда не успевает исполнить
        # файл-заглушку — но это не значит, что provider не начал попытку.
        # Проверяем его границу, а не гонку стартов операционной системы.
        with unittest.mock.patch.object(provider, 'CALL_TIMEOUT', 0.2), \
                unittest.mock.patch.object(provider, 'attempt', wraps=provider.attempt) as attempted:
            text = self.provide()
        self.assertEqual(attempted.call_count, provider.ATTEMPTS)
        self.assertIn('не уложился', text)

    def test_the_alert_is_short_and_names_the_reason(self) -> None:
        self.stub('echo "сервер лёг" >&2\nexit 1')
        text = self.provide()
        self.assertLess(len(text), 400, text)
        self.assertIn('сервер лёг', text)

    def test_no_binary_is_an_alert_and_not_a_crash(self) -> None:
        missing = unittest.mock.Mock()
        missing.load.return_value.binary.side_effect = RuntimeError('не собран')
        with unittest.mock.patch.object(provider.registry, 'find', return_value=missing):
            text = provider.provide(Event(), Journal(), sleep=self.slept.append)
        self.assertIn('недоступен', text)
        self.assertIn('не собран', text)
        self.assertTrue(text.startswith('стартовый контекст: снимок Craft не собран:'))

    def test_without_the_api_base_nothing_is_called_at_all(self) -> None:
        self.stub('echo был-вызов')
        os.environ.pop('CRAFT_API_BASE')
        text = self.provide()
        self.assertIn('CRAFT_API_BASE', text)
        self.assertEqual(self.call_lines(), [])

    def test_api_base_is_loaded_from_the_worktree_dotenv(self) -> None:
        os.environ.pop('CRAFT_API_BASE')
        dotenv = self.root / '.env'
        dotenv.write_text('export CRAFT_API_BASE="https://example/connect-secret"\n', encoding='utf-8')
        self.env_lookup.stop()
        with unittest.mock.patch.object(provider, 'repository_env', return_value=dotenv):
            self.stub('test "$CRAFT_API_BASE" = "https://example/connect-secret" || exit 9\necho "прочитано $2"')
            text = self.provide()
        self.assertIn(provider.RULES_TITLE, text)
        self.assertIn(provider.MEMORY_TITLE, text)
        self.assertEqual(len(self.call_lines()), 2)

    def test_dotenv_is_not_interpreted_as_shell_code(self) -> None:
        dotenv = self.root / '.env'
        marker = self.root / 'must-not-exist'
        dotenv.write_text(
            f'CRAFT_API_BASE=$(touch {marker})\n', encoding='utf-8'
        )
        self.assertIsNone(provider.dotenv_base(dotenv))
        self.assertFalse(marker.exists())

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

    def test_the_rules_are_read_through_their_container(self) -> None:
        # Почти все разделы правил лежат в одной странице: её глубокое чтение
        # заменяет два десятка запросов. Память — один блок, ей контейнер незачем.
        self.stub('echo "прочитано $2"')
        self.provide()
        self.assertIn(f'--container {provider.RULES_CONTAINER}', self.call_lines()[0])
        self.assertNotIn('--container', self.call_lines()[1])

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
        # Худший случай — оба чтения целиком по длинной лестнице: каждая попытка
        # упирается в свой таймаут, и между ними отстаиваются все паузы.
        worst = 2 * (provider.ATTEMPTS_429 * provider.CALL_TIMEOUT
                     + sum(provider.BACKOFF_429_SECONDS))
        self.assertLess(worst, installer.HOOK_TIMEOUT_SEC, f'худший случай {worst} с')


class WorktreeEnvTest(unittest.TestCase):
    """Настоящий Git-worktree: секрет соседа не становится фолбеком."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.worktree = self.root / 'worktree'
        self.source.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Snapshot test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('commit', '--allow-empty', '-qm', 'fixture')
        self.git('worktree', 'add', '--detach', str(self.worktree), 'HEAD')
        (self.source / '.env').write_text('CRAFT_API_BASE=https://source.invalid\n')

    def git(self, *args) -> None:
        subprocess.run(['git', '-C', str(self.source), *args], check=True, capture_output=True)

    def test_the_current_worktree_wins_over_the_main_checkout(self) -> None:
        local = self.worktree / '.env'
        local.write_text('CRAFT_API_BASE=https://worktree.invalid\n')
        nested = self.worktree / 'nested'
        nested.mkdir()
        with unittest.mock.patch.object(provider.Path, 'cwd', return_value=nested):
            found = provider.repository_env()
        self.assertEqual(found.resolve(), local.resolve())
        self.assertEqual(provider.dotenv_base(found), 'https://worktree.invalid')

    def test_missing_worktree_dotenv_never_reads_the_main_checkout(self) -> None:
        with unittest.mock.patch.object(provider.Path, 'cwd', return_value=self.worktree):
            self.assertIsNone(provider.repository_env())


if __name__ == '__main__':
    unittest.main()
