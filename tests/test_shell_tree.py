"""Библиотечная часть модуля shell-tree: вызов бинарника и ожидание сборки.

Настоящий бинарник тут не нужен: проверяется то, что решено кодом на Python —
где ищется бинарник, что уходит ему на вход, чего ждут, когда его нет, и что
происходит, когда ответ не тот. Сам разбор команд проверяют тесты на Go рядом с
исходниками, сборку — `tests/test_shell_tree_build.py`.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from . import MODULES_DIR

MODULE = MODULES_DIR / 'shell-tree'


def load_library():
    spec = importlib.util.spec_from_file_location(
        'shell_tree_lib', MODULE / 'lib' / '__init__.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


library = load_library()


class Marker:
    """Хранилище ровно в той части, которой пользуется библиотека."""

    def __init__(self, mark=None) -> None:
        self.mark = mark
        self.reads = 0

    def read_json(self, name, zone='session', default=None):
        self.reads += 1
        return self.mark if self.mark is not None else default


class LibraryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        previous = os.environ.get(library.BINARY_ENV)
        self.addCleanup(
            lambda: os.environ.pop(library.BINARY_ENV, None) if previous is None
            else os.environ.__setitem__(library.BINARY_ENV, previous)
        )
        os.environ.pop(library.BINARY_ENV, None)
        # Собранный бинарник модуля из-под теста убирается: иначе он ответил бы
        # вместо заглушки, и тест перестал бы проверять свой код.
        self.addCleanup(lambda binary=library.BINARY: setattr(library, 'BINARY', binary))
        library.BINARY = self.root / 'нет-бинарника'

    def stub(self, body: str) -> Path:
        """Заглушка бинарника плюс журнал того, с чем её позвали."""
        self.calls = self.root / 'calls.log'
        self.input = self.root / 'input.txt'
        tool = self.root / 'shell-tree'
        tool.write_text(
            f'#!/bin/sh\necho "$@" >> {self.calls}\ncat > {self.input}\n{body}\n',
            encoding='utf-8',
        )
        tool.chmod(0o755)
        os.environ[library.BINARY_ENV] = str(tool)
        return tool

    # --- вызов ---

    def test_the_answer_comes_back_as_a_dictionary(self) -> None:
        self.stub('echo \'{"writes":"да","targets":[],"reason":"причина"}\'')
        answer = library.verdict('rm -rf /', cwd='/repo')
        self.assertEqual(answer['writes'], 'да')
        self.assertEqual(answer['reason'], 'причина')

    def test_the_command_goes_in_on_stdin(self) -> None:
        self.stub('echo \'{"cwd":"/repo","links":[]}\'')
        library.parse('cd /tmp && rm -rf build', cwd='/repo')
        self.assertEqual(self.input.read_text(encoding='utf-8'), 'cd /tmp && rm -rf build')

    def test_the_data_of_the_module_is_named_by_flags(self) -> None:
        # Рядом с собой бинарник данные не ищет: где они, говорит библиотека.
        self.stub('echo \'{"writes":"нет","targets":[],"reason":""}\'')
        library.verdict('ls', cwd='/repo')
        called = self.calls.read_text(encoding='utf-8')
        self.assertIn(f'--rules {MODULE / "data" / "read-only-rules.json"}', called)
        self.assertIn(f'--commands {MODULE / "data" / "commands"}', called)
        self.assertIn('--cwd /repo', called)

    def test_broken_json_is_an_error_and_not_an_empty_answer(self) -> None:
        self.stub('echo "не json вовсе"')
        with self.assertRaises(library.ShellTreeError) as raised:
            library.verdict('rm -rf /')
        self.assertIn('не JSON', str(raised.exception))

    def test_a_failed_call_carries_its_reason_out(self) -> None:
        self.stub('echo "команда не разобралась: кавычка" >&2\nexit 1')
        with self.assertRaises(library.ShellTreeError) as raised:
            library.parse('cat "не закрытая')
        self.assertIn('кавычка', str(raised.exception))

    # --- бинарника нет ---

    def test_no_binary_is_an_error_that_says_where_it_was_waited_for(self) -> None:
        with self.assertRaises(library.ShellTreeError) as raised:
            library.verdict('rm -rf /')
        said = str(raised.exception)
        self.assertIn(str(library.BINARY), said)
        self.assertIn('хук модуля', said)

    def test_a_live_build_is_waited_for_without_a_ceiling(self) -> None:
        # Маркер жив, и бинарник появляется позже: ожидание держится на живом
        # процессе, а не на секундах.
        storage = Marker({'state': library.BUILDING, 'pid': os.getpid()})
        appear = threading.Timer(0.2, lambda: library.BINARY.write_text('бинарник'))
        appear.start()
        self.addCleanup(appear.cancel)
        started = time.monotonic()
        library.wait_for_build(storage)
        waited = time.monotonic() - started
        self.assertTrue(library.BINARY.is_file(), 'дождались, а бинарника нет')
        self.assertGreater(waited, 0.1, 'ожидания не было вовсе')
        self.assertGreater(storage.reads, 1, 'маркер читался один раз — это не ожидание')

    def test_a_dead_build_is_not_waited_for(self) -> None:
        done = subprocess.Popen([sys.executable, '-c', 'pass'])
        done.wait()
        storage = Marker({'state': library.BUILDING, 'pid': done.pid})
        started = time.monotonic()
        library.wait_for_build(storage)
        self.assertLess(time.monotonic() - started, 1, 'ждали процесс, которого нет')
        self.assertFalse(library.BINARY.exists())

    def test_without_a_storage_nothing_is_waited_for(self) -> None:
        started = time.monotonic()
        library.wait_for_build(None)
        self.assertLess(time.monotonic() - started, 1)

    # --- отпечаток исходников ---

    def test_the_fingerprint_changes_with_the_sources(self) -> None:
        source = self.root / 'src'
        source.mkdir()
        (source / 'main.go').write_text('package main\n', encoding='utf-8')
        first = library.fingerprint(source)
        self.assertEqual(first, library.fingerprint(source), 'отпечаток неустойчив')
        (source / 'main.go').write_text('package main\n\nfunc main() {}\n', encoding='utf-8')
        self.assertNotEqual(first, library.fingerprint(source))

    def test_the_fingerprint_ignores_what_is_not_a_source(self) -> None:
        source = self.root / 'src'
        source.mkdir()
        (source / 'main.go').write_text('package main\n', encoding='utf-8')
        first = library.fingerprint(source)
        (source / 'shell-tree').write_text('собранный бинарник', encoding='utf-8')
        self.assertEqual(first, library.fingerprint(source))


if __name__ == '__main__':
    unittest.main()
