"""Библиотечная часть модуля shell-tree: вызов бинарника и разбор ответа.

Настоящий бинарник тут не нужен: проверяется то, что решено кодом на Python —
где ищется инструмент, что уходит ему на вход и что происходит, когда ответ не
тот. Сам разбор команд проверяют тесты на Go рядом с ним.
"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

from . import MODULES_DIR


def load_library():
    spec = importlib.util.spec_from_file_location(
        'shell_tree_lib', MODULES_DIR / 'shell-tree' / 'lib' / '__init__.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


library = load_library()


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
        # Настоящий бинарник, собранный в этом же окружении, из-под теста
        # убираем: иначе он ответил бы вместо заглушки, и тест перестал бы
        # проверять свой код. Дом уводится туда же, где его нет: путь по
        # умолчанию (~/.local/bin) библиотека берёт от него. В PATH остаются
        # /usr/bin и /bin — без них у самой заглушки не будет `cat`.
        for name, value in (('PATH', f"{self.root / 'пусто'}:/usr/bin:/bin"),
                            ('HOME', str(self.root / 'дом'))):
            previous_value = os.environ.get(name)
            self.addCleanup(
                lambda n=name, v=previous_value: os.environ.pop(n, None) if v is None
                else os.environ.__setitem__(n, v)
            )
            os.environ[name] = value

    def stub(self, body: str) -> Path:
        """Заглушка бинарника плюс журнал того, с чем её позвали."""
        self.calls = self.root / 'calls.log'
        self.input = self.root / 'input.txt'
        tool = self.root / 'shell-tree'
        tool.write_text(
            f'#!/bin/sh\necho "$@" >> {self.calls}\ncat > {self.input}\n{body}\n', encoding='utf-8'
        )
        tool.chmod(0o755)
        os.environ[library.BINARY_ENV] = str(tool)
        return tool

    def test_the_answer_comes_back_as_a_dictionary(self) -> None:
        self.stub('echo \'{"writes":"да","targets":[],"reason":"причина"}\'')
        answer = library.verdict('ls -la', cwd='/repo')
        self.assertEqual(answer['writes'], 'да')
        self.assertEqual(answer['reason'], 'причина')

    def test_the_command_goes_in_on_stdin_and_the_directory_by_a_flag(self) -> None:
        self.stub('echo \'{"cwd":"/repo","links":[]}\'')
        library.parse('cd /tmp && rm -rf build', cwd='/repo')
        self.assertEqual(self.input.read_text(encoding='utf-8'), 'cd /tmp && rm -rf build')
        self.assertEqual(self.calls.read_text(encoding='utf-8').strip(), 'parse --cwd /repo')

    def test_the_rules_path_is_passed_on_when_given(self) -> None:
        self.stub('echo \'{"writes":"нет","targets":[],"reason":""}\'')
        library.verdict('ls', rules='/где/то/read-only-rules.json')
        self.assertIn('--rules /где/то/read-only-rules.json',
                      self.calls.read_text(encoding='utf-8'))

    def test_no_binary_is_an_error_that_says_where_it_was_looked_for(self) -> None:
        with self.assertRaises(library.ShellTreeError) as raised:
            library.verdict('rm -rf /')
        said = str(raised.exception)
        self.assertIn(library.BINARY_ENV, said)
        self.assertIn(library.DEFAULT_BINARY, said)
        self.assertIn('PATH', said)

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

    def test_the_binary_from_the_environment_wins(self) -> None:
        self.stub('echo \'{"writes":"нет","targets":[],"reason":"из переменной"}\'')
        self.assertEqual(library.verdict('ls')['reason'], 'из переменной')

    def test_the_binary_is_found_in_the_path_too(self) -> None:
        self.stub('echo \'{"writes":"нет","targets":[],"reason":"из PATH"}\'')
        os.environ.pop(library.BINARY_ENV)
        os.environ['PATH'] = str(self.root)
        self.assertEqual(library.verdict('ls')['reason'], 'из PATH')


if __name__ == '__main__':
    unittest.main()
