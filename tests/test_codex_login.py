"""Раскладка входа Codex: путь от старта сессии до файла, без самого харнеса.

Модуль ставится установщиком и запускается тем же процессом и тем же событием,
каким его запустил бы Claude. Настоящего входа тут нет и быть не может: в
переменной поддельное значение, и один из тестов проверяет, что оно не доехало
до следа.
"""

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

from jarvis.storage import Storage
from jarvis.trace import Trace

from . import INSTALLER, MODULES_DIR

SLUG = 'codex-login'
ENTRY = (SLUG, 'hooks', 'module.py')

# Поддельный вход: настоящий в тесты не попадает никогда. Маркер внутри —
# по нему тест ищет утечку значения в след.
MARKER = 'ПОДДЕЛЬНЫЙ-ВХОД-КОДЕКСА-7719'
FAKE_AUTH = json.dumps(
    {'OPENAI_API_KEY': None, 'tokens': {'access_token': MARKER}, 'last_refresh': '2026-09-18T00:00:00Z'},
    ensure_ascii=False,
)


def session_event(session_id: str, source: str = 'startup') -> str:
    return json.dumps({
        'hook_event_name': 'SessionStart',
        'session_id': session_id,
        'cwd': '/work',
        'source': source,
    })


class CodexLoginTest(unittest.TestCase):
    """Каждому тесту свой дом: важно, что лежит в нём до запуска и после."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.settings_root = Path(cls.tmp.name) / 'settings'
        subprocess.run(
            [
                sys.executable, str(INSTALLER),
                '--settings-dir', str(cls.settings_root),
                '--modules', str(MODULES_DIR),
            ],
            check=True,
            capture_output=True,
        )
        cls.entry = cls.settings_root.joinpath('jarvis', 'modules', *ENTRY)

    def setUp(self) -> None:
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = Path(home.name)
        self.auth = self.home / '.codex' / 'auth.json'

    def fire(self, session_id: str, auth: str | None = FAKE_AUTH, source: str = 'startup',
             home: Path | None = None, codex_home: str | None = None):
        environment = {'PATH': '/usr/bin:/bin', 'HOME': str(home or self.home)}
        if auth is not None:
            environment['CODEX_AUTH_JSON'] = auth
        if codex_home is not None:
            environment['CODEX_HOME'] = codex_home
        return subprocess.run(
            [sys.executable, str(self.entry), '--harness', 'claude', '--event', 'session-start'],
            input=session_event(session_id, source),
            text=True,
            capture_output=True,
            env=environment,
        )

    def trace(self, session_id: str) -> list[dict]:
        state = self.home / '.local' / 'state' / 'jarvis'
        return Trace(Storage(state, session_id)).read()

    def reason(self, session_id: str) -> str:
        lines = self.trace(session_id)
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(lines[0]['module'], SLUG)
        self.assertEqual(lines[0]['response'], 'silence')
        return lines[0]['reason']

    # --- четыре исхода ---

    def test_a_the_variable_lays_the_file_out_when_it_is_missing(self) -> None:
        result = self.fire('sess-lay')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '', 'ответ харнесу — молчание')
        self.assertEqual(self.auth.read_text(encoding='utf-8'), FAKE_AUTH)
        self.assertEqual(self.reason('sess-lay'), 'разложен')

    def test_b_the_same_content_is_left_where_it_lies(self) -> None:
        # Права сразу узкие: этот тест про содержимое, права проверяют соседи.
        self.auth.parent.mkdir(parents=True, mode=0o700)
        self.auth.write_text(FAKE_AUTH, encoding='utf-8')
        self.auth.chmod(0o600)
        before = self.auth.stat().st_ino

        self.fire('sess-same')

        self.assertEqual(self.reason('sess-same'), 'уже на месте')
        # Запись идёт переименованием поверх, поэтому новый файл — это новый
        # inode. Тот же inode и значит «не трогали».
        self.assertEqual(self.auth.stat().st_ino, before)

    def test_c_different_content_is_replaced(self) -> None:
        self.auth.parent.mkdir(parents=True)
        self.auth.write_text('{"tokens": {"access_token": "ЧУЖОЙ"}}', encoding='utf-8')

        self.fire('sess-other')

        self.assertEqual(self.reason('sess-other'), 'разложен')
        self.assertEqual(self.auth.read_text(encoding='utf-8'), FAKE_AUTH)

    def test_d_without_the_variable_nothing_appears(self) -> None:
        result = self.fire('sess-no-env', auth=None)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertFalse(self.auth.exists(), 'файла быть не должно')
        self.assertEqual(self.reason('sess-no-env'), 'переменной нет')

    def test_e_an_empty_variable_counts_as_no_variable(self) -> None:
        self.fire('sess-empty', auth='')

        self.assertFalse(self.auth.exists())
        self.assertEqual(self.reason('sess-empty'), 'переменной нет')

    def test_f_a_failure_lands_in_the_trace_with_its_reason(self) -> None:
        # Каталог входа занят файлом: раскладка упирается в файловую систему.
        self.auth.parent.write_text('не каталог', encoding='utf-8')

        result = self.fire('sess-failed')

        self.assertEqual(result.returncode, 0, 'упавшая раскладка не роняет старт сессии')
        self.assertEqual(result.stdout, '')
        self.assertTrue(self.reason('sess-failed').startswith('не удалось: '),
                        self.reason('sess-failed'))

    # --- права и секрет ---

    def test_g_the_directory_is_700_and_the_file_is_600(self) -> None:
        self.fire('sess-modes')

        self.assertEqual(stat.S_IMODE(self.auth.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.auth.stat().st_mode), 0o600)

    def test_h_the_value_never_lands_in_the_trace(self) -> None:
        for session, auth in (('sess-secret-lay', FAKE_AUTH), ('sess-secret-same', FAKE_AUTH)):
            self.fire(session, auth=auth)
        journal = self.home / '.local' / 'state' / 'jarvis'
        written = '\n'.join(
            path.read_text(encoding='utf-8')
            for path in journal.rglob('*.jsonl')
        )
        self.assertNotEqual(written.strip(), '', 'след должен быть непустым, иначе тест ничего не ловит')
        self.assertNotIn(MARKER, written)
        self.assertNotIn(FAKE_AUTH, written)

    # --- CODEX_HOME ---

    def test_k_codex_home_wins_over_home(self) -> None:
        elsewhere = self.home / 'другой-дом'

        self.fire('sess-codex-home', codex_home=str(elsewhere))

        self.assertEqual((elsewhere / 'auth.json').read_text(encoding='utf-8'), FAKE_AUTH)
        self.assertFalse(self.auth.exists(), 'в HOME/.codex писать было незачем')
        self.assertEqual(self.reason('sess-codex-home'), 'разложен')
        self.assertEqual(stat.S_IMODE(elsewhere.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((elsewhere / 'auth.json').stat().st_mode), 0o600)

    def test_l_an_empty_codex_home_falls_back_to_home(self) -> None:
        self.fire('sess-codex-home-empty', codex_home='')

        self.assertEqual(self.auth.read_text(encoding='utf-8'), FAKE_AUTH)
        self.assertEqual(self.reason('sess-codex-home-empty'), 'разложен')

    # --- сравнение байтами ---

    def test_m_a_file_that_is_not_utf8_counts_as_different(self) -> None:
        self.auth.parent.mkdir(parents=True)
        self.auth.write_bytes(b'\xff\xfe\x00 broken bytes')

        self.fire('sess-broken')

        self.assertEqual(self.reason('sess-broken'), 'разложен')
        self.assertEqual(self.auth.read_text(encoding='utf-8'), FAKE_AUTH)

    # --- права при совпавшем содержимом ---

    def test_n_wide_file_permissions_are_narrowed(self) -> None:
        self.auth.parent.mkdir(parents=True, mode=0o700)
        self.auth.write_text(FAKE_AUTH, encoding='utf-8')
        self.auth.chmod(0o644)

        self.fire('sess-wide-file')

        self.assertEqual(self.reason('sess-wide-file'), 'уже на месте, права поправлены')
        self.assertEqual(stat.S_IMODE(self.auth.stat().st_mode), 0o600)

    def test_o_a_wide_directory_is_narrowed_too(self) -> None:
        self.auth.parent.mkdir(parents=True, mode=0o755)
        self.auth.write_text(FAKE_AUTH, encoding='utf-8')
        self.auth.chmod(0o600)

        self.fire('sess-wide-dir')

        self.assertEqual(self.reason('sess-wide-dir'), 'уже на месте, права поправлены')
        self.assertEqual(stat.S_IMODE(self.auth.parent.stat().st_mode), 0o700)

    def test_p_narrow_permissions_are_left_alone(self) -> None:
        self.auth.parent.mkdir(parents=True, mode=0o700)
        self.auth.write_text(FAKE_AUTH, encoding='utf-8')
        self.auth.chmod(0o400)

        self.fire('sess-narrow-file')

        self.assertEqual(self.reason('sess-narrow-file'), 'уже на месте')
        self.assertEqual(stat.S_IMODE(self.auth.stat().st_mode), 0o400,
                         'уже узкие права не расширяются до 600')

    # --- события, на которых модуль не работает ---

    def test_i_after_compact_is_silent_and_lays_nothing_out(self) -> None:
        result = self.fire('sess-compact', source='compact')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertFalse(self.auth.exists(), 'после сжатия файл уже лежит, второй заход не нужен')
        self.assertEqual(self.trace('sess-compact'), [], 'хода на этом событии у модуля не было')

    def test_j_the_installer_registers_one_session_start_line(self) -> None:
        settings = json.loads((self.settings_root / 'settings.json').read_text(encoding='utf-8'))
        lines = [
            (event, hook['args'])
            for event, groups in settings['hooks'].items()
            for group in groups
            for hook in group['hooks']
            if any(f'/{SLUG}/hooks/' in str(argument) for argument in hook.get('args', ()))
        ]
        self.assertEqual(len(lines), 1, lines)
        event, args = lines[0]
        self.assertEqual(event, 'SessionStart')
        self.assertEqual(args[1:], ['--harness', 'claude', '--event', 'session-start'])


def load_module():
    """Функции модуля без запуска: точка входа под `if __name__`, ядро в пути."""
    loader = SourceFileLoader('codex_login', str(MODULES_DIR / SLUG / 'hooks' / 'module.py'))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


class NarrowTest(unittest.TestCase):
    """Сужение прав отдельно: исход «не удалось» держится на этом OSError.

    Заставить chmod упасть целым прогоном не выходит — тесты идут под root, а
    ему файловая система не отказывает. Поэтому проверяется само звено: что
    беда уходит наверх OSError'ом, а его ловит тот же except, что и остальные
    ошибки раскладки (прогон этого исхода — в тесте про занятый каталог).
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_wider_permissions_are_narrowed(self) -> None:
        path = self.root / 'wide'
        path.write_text('x', encoding='utf-8')
        path.chmod(0o644)

        self.assertTrue(self.module.narrow(path, 0o600))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_equal_and_narrower_permissions_are_left_alone(self) -> None:
        for mode in (0o600, 0o400):
            with self.subTest(oct(mode)):
                path = self.root / f'mode-{mode:o}'
                path.write_text('x', encoding='utf-8')
                path.chmod(mode)

                self.assertFalse(self.module.narrow(path, 0o600))
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def test_a_failure_goes_up_as_oserror(self) -> None:
        with self.assertRaises(OSError):
            self.module.narrow(self.root / 'нет такого', 0o600)


if __name__ == '__main__':
    unittest.main()
