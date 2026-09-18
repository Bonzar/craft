"""Хук модуля shell-tree: сборка бинарника на старте сессии.

Настоящий Go тут не нужен и не годится: проверяется то, что решено кодом —
когда хук зовёт сборку, когда не зовёт вовсе, что пишет в журнал и какой маркер
оставляет. Вместо `go` на PATH стоит заглушка, которая записывает, с чем её
позвали, и делает файл: так видно и число вызовов, и аргументы.

Хук запускается тем же процессом и тем же событием, каким его запустил бы
Claude, — из установленного модуля, как в жизни.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import CORE_SOURCE, INSTALLER, MODULES_DIR

SLUG = 'shell-tree'
SOURCE_FILE = 'main.go'


def session_event(session_id: str) -> str:
    return json.dumps({
        'hook_event_name': 'SessionStart',
        'session_id': session_id,
        'cwd': '/work',
        'source': 'startup',
    })


class BuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'дом'
        self.home.mkdir()
        self.settings = self.root / 'настройки'
        self.stubs = self.root / 'заглушки'
        self.stubs.mkdir()
        self.calls = self.root / 'go-calls.log'

        # Источник: настоящий модуль, но исходники подменены крошечными — их
        # отпечаток считается так же, а собирать их будет заглушка.
        source = self.root / 'источник'
        source.mkdir()
        shutil.copytree(MODULES_DIR / SLUG, source / SLUG,
                        ignore=shutil.ignore_patterns('src', 'bin', '__pycache__'))
        self.source = source / SLUG / 'src'
        self.source.mkdir()
        (self.source / SOURCE_FILE).write_text('package main\n', encoding='utf-8')
        (self.source / 'go.mod').write_text('module shell-tree\n', encoding='utf-8')
        subprocess.run(
            [sys.executable, str(INSTALLER), '--settings-dir', str(self.settings),
             '--modules', str(source), '--core', str(CORE_SOURCE)],
            check=True, capture_output=True,
        )
        self.module = self.settings / 'jarvis' / 'modules' / SLUG
        self.binary = self.module / 'bin' / 'shell-tree'

    def stub_go(self, body: str = '') -> None:
        """Заглушка `go`: пишет, с чем её позвали, и делает файл, как настоящий."""
        tool = self.stubs / 'go'
        tool.write_text(
            '#!/bin/sh\n'
            f'echo "$@" >> {self.calls}\n'
            f'{body}\n'
            '# последний аргумент после -o — куда класть бинарник\n'
            'while [ $# -gt 0 ]; do\n'
            '  if [ "$1" = "-o" ]; then shift; printf "бинарник" > "$1"; chmod +x "$1"; fi\n'
            '  shift\n'
            'done\n',
            encoding='utf-8',
        )
        tool.chmod(0o755)

    def installed_line(self, harness_event: str) -> dict:
        """Строка хука ровно в том виде, в каком её записал установщик."""
        settings = json.loads((self.settings / 'settings.json').read_text(encoding='utf-8'))
        lines = [
            hook for group in settings['hooks'].get(harness_event, [])
            for hook in group.get('hooks', [])
            if SLUG in ' '.join(hook.get('args', []))
        ]
        self.assertEqual(len(lines), 1, settings['hooks'].get(harness_event))
        return lines[0]

    def fire(self, session_id: str, path: str | None = None) -> subprocess.CompletedProcess:
        # Хук запускается строкой из settings.json, а не собранной здесь: иначе
        # тест проверяет свою строку, а живая сессия — установщикову, и разрыв
        # между ними (например, незнакомый хуку ключ) остаётся невидимым.
        line = self.installed_line('SessionStart')
        done = subprocess.run(
            [line['command'], *line['args']],
            input=session_event(session_id), text=True, capture_output=True,
            env={'PATH': path if path is not None else f'{self.stubs}:/usr/bin:/bin',
                 'HOME': str(self.home)},
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done

    def journal(self, session_id: str) -> list[dict]:
        path = self.home / '.local' / 'state' / 'jarvis' / session_id / 'shell-tree.jsonl'
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

    def marker(self, session_id: str) -> dict:
        path = self.home / '.local' / 'state' / 'jarvis' / session_id / 'shell-tree-build.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

    def go_calls(self) -> list[str]:
        return self.calls.read_text(encoding='utf-8').splitlines() if self.calls.exists() else []

    # --- сборка ---

    def test_the_hook_builds_when_there_is_no_binary(self) -> None:
        self.stub_go()
        done = self.fire('первая')
        self.assertTrue(self.binary.is_file(), 'бинарника нет, а хук отработал')
        self.assertEqual(done.stdout.strip(), '', 'харнесу хук отвечает молчанием')
        self.assertEqual(len(self.go_calls()), 1)
        self.assertIn('-ldflags=-s -w', self.go_calls()[0])
        built = [line for line in self.journal('первая') if line['action'] == 'собран']
        self.assertEqual(len(built), 1, self.journal('первая'))
        self.assertIsInstance(built[0]['seconds'], float, 'время сборки — замер, он в журнале')
        self.assertEqual(self.marker('первая')['state'], 'готов')

    def test_a_matching_fingerprint_builds_nothing(self) -> None:
        self.stub_go()
        self.fire('первая')
        self.fire('вторая')
        self.assertEqual(len(self.go_calls()), 1, 'вторая сессия собрала заново')
        self.assertEqual([line['action'] for line in self.journal('вторая')], ['сборка не нужна'])

    def test_changed_sources_build_again(self) -> None:
        self.stub_go()
        self.fire('первая')
        (self.module / 'src' / SOURCE_FILE).write_text(
            'package main\n\nfunc main() {}\n', encoding='utf-8')
        self.fire('вторая')
        self.assertEqual(len(self.go_calls()), 2, 'исходники изменились, а сборки не было')
        self.assertEqual([line['action'] for line in self.journal('вторая')], ['собран'])

    def test_without_go_the_hook_says_why_and_stays_silent(self) -> None:
        # PATH ведёт в пустой каталог, а не в /usr/bin: на машине сборки Go
        # лежит именно там, и «PATH без Go» надо делать, а не предполагать.
        empty = self.root / 'пусто'
        empty.mkdir()
        done = self.fire('без-go', path=str(empty))
        self.assertEqual(done.stdout.strip(), '')
        self.assertFalse(self.binary.exists())
        entries = self.journal('без-go')
        self.assertEqual([line['action'] for line in entries], ['нет go'], entries)
        self.assertIn('go на PATH', entries[0]['reason'])

    def test_a_failed_build_does_not_break_the_session(self) -> None:
        self.stub_go('echo "сломалось" >&2\nexit 1')
        done = self.fire('поломка')
        self.assertEqual(done.stdout.strip(), '')
        entries = self.journal('поломка')
        self.assertEqual([line['action'] for line in entries], ['сборка упала'], entries)
        self.assertIn('сломалось', entries[0]['reason'])

    # --- раскладка ---

    def test_the_installer_puts_the_sources_and_the_data_in_place(self) -> None:
        for part in ('src', 'data', 'lib', 'hooks'):
            self.assertTrue((self.module / part).is_dir(), f'части {part} нет в установленном модуле')
        self.assertTrue((self.module / 'data' / 'read-only-rules.json').is_file())
        self.assertTrue((self.module / 'data' / 'commands').is_dir())
        self.assertFalse((self.module / 'bin').exists(), 'bin/ делает хук, а не установщик')

    def test_the_installer_writes_a_session_start_line(self) -> None:
        line = self.installed_line('SessionStart')
        self.assertIn('--event', line['args'])
        self.assertIn('session-start', line['args'])
        self.assertIn('--harness', line['args'], 'строка хука не называет харнес')


if __name__ == '__main__':
    unittest.main()
