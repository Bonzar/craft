"""Раскладка входа Codex: путь от старта сессии до файла, без самого харнеса.

Модуль ставится установщиком и запускается тем же процессом и тем же событием,
каким его запустил бы Claude. Настоящего входа тут нет и быть не может: в
переменной поддельное значение, и один из тестов проверяет, что оно не доехало
до следа.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
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
             home: Path | None = None):
        environment = {'PATH': '/usr/bin:/bin', 'HOME': str(home or self.home)}
        if auth is not None:
            environment['CODEX_AUTH_JSON'] = auth
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
        self.auth.parent.mkdir(parents=True)
        self.auth.write_text(FAKE_AUTH, encoding='utf-8')
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


if __name__ == '__main__':
    unittest.main()
