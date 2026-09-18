"""Библиотечная часть модуля shell-tree: вызов bash-classify и наш ответ.

Живая утилита тут не нужна и не годится: проверяется то, что решено кодом на
Python — где ищется утилита, что уходит ей на вход, что происходит, когда ответ
не тот, и что модуль дописывает поверх её JSON (рабочий каталог звена, вид цели,
страховка от двух её слепых пятен). Сам разбор команд проверяет корпус —
`tests/test_shell_tree_corpus.py`, он идёт живой утилитой.
"""

import importlib.util
import json
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


def answer(classification='READONLY', commands=(), **extra) -> dict:
    """Ответ bash-classify той формы, какую он даёт на живом."""
    out = {'expression': extra.pop('expression', ''), 'classification': classification,
           'risk': 'LOW', 'directories': [], 'commands': list(commands)}
    out.update(extra)
    return out


def command(argv, classification='READONLY', rule=None, **extra) -> dict:
    out = {'command': [argv[0]], 'argv': list(argv), 'classification': classification,
           'risk': 'LOW', 'matched_rule': rule, 'options': [], 'positionals': [],
           'inner_commands': []}
    out.update(extra)
    return out


class LibraryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Настоящая утилита из этого окружения из-под теста убирается: иначе она
        # ответила бы вместо заглушки. Дом уводится туда, где её нет: путь по
        # умолчанию (~/.local/bin) библиотека берёт от него. В PATH остаются
        # /usr/bin и /bin — без них у самой заглушки не будет интерпретатора.
        for name, value in ((library.BINARY_ENV, None),
                            ('PATH', f'{self.root / "пусто"}:/usr/bin:/bin'),
                            ('HOME', str(self.root / 'дом'))):
            previous = os.environ.get(name)
            self.addCleanup(
                lambda n=name, v=previous: os.environ.pop(n, None) if v is None
                else os.environ.__setitem__(n, v)
            )
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def stub(self, body: str) -> Path:
        """Заглушка bash-classify плюс журнал того, что ей дали на вход."""
        self.input = self.root / 'input.txt'
        self.calls = self.root / 'calls.log'
        self.input.unlink(missing_ok=True)  # заглушка ставится не раз за тест
        self.calls.unlink(missing_ok=True)
        tool = self.root / 'bash-classify'
        tool.write_text(
            '#!/bin/sh\n'
            f'cat >> {self.calls}\n'
            f'cp {self.calls} {self.input}\n'
            f'{body}\n',
            encoding='utf-8',
        )
        tool.chmod(0o755)
        os.environ[library.BINARY_ENV] = str(tool)
        return tool

    def answers(self, *bodies: dict) -> Path:
        """Заглушка, отвечающая по очереди: первый вызов — первый ответ."""
        self.input = self.root / 'input.txt'
        self.calls = self.root / 'calls.log'
        self.input.unlink(missing_ok=True)  # счётчик вызовов начинается заново
        self.calls.unlink(missing_ok=True)
        for number, body in enumerate(bodies, start=1):
            (self.root / f'answer{number}.json').write_text(
                json.dumps(body, ensure_ascii=False), encoding='utf-8')
        tool = self.root / 'bash-classify'
        tool.write_text(
            '#!/bin/sh\n'
            f'cat >> {self.input}\n'
            f'echo x >> {self.calls}\n'
            f'number=$(wc -l < {self.calls} | tr -d " ")\n'
            f'cat {self.root}/answer"$number".json\n',
            encoding='utf-8',
        )
        tool.chmod(0o755)
        os.environ[library.BINARY_ENV] = str(tool)
        return tool

    # --- вызов утилиты ---

    def test_the_answer_comes_back_as_a_dictionary(self) -> None:
        self.stub(f"cat {self.root / 'answer.json'}")
        (self.root / 'answer.json').write_text(json.dumps(answer('DANGEROUS')), encoding='utf-8')
        got = library.verdict('rm -rf /')
        self.assertEqual(got['writes'], library.WRITES_YES)
        self.assertEqual(got['classification'], 'DANGEROUS')

    def test_the_command_goes_in_on_stdin(self) -> None:
        self.answers(answer('READONLY'))
        library.verdict('ls -la')
        self.assertEqual(self.input.read_text(encoding='utf-8'), 'ls -la')

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

    def test_an_answer_without_a_classification_is_an_error(self) -> None:
        self.stub('echo \'{"expression":"ls"}\'')
        with self.assertRaises(library.ShellTreeError) as raised:
            library.verdict('ls')
        self.assertIn('классификации', str(raised.exception))

    def test_a_failed_call_carries_its_reason_out(self) -> None:
        self.stub('echo "внутренняя ошибка разбора" >&2\nexit 2')
        with self.assertRaises(library.ShellTreeError) as raised:
            library.verdict('ls')
        self.assertIn('внутренняя ошибка разбора', str(raised.exception))

    def test_an_empty_command_is_answered_without_calling_the_tool(self) -> None:
        self.answers(answer('DANGEROUS'))
        got = library.verdict('   ')
        self.assertEqual(got['writes'], library.WRITES_NO)
        self.assertFalse(self.calls.exists(), 'пустую строку незачем отдавать утилите')

    # --- сопоставление классификации с ответом ---

    def test_every_classification_has_its_answer(self) -> None:
        for classification, writes in (
            ('READONLY', library.WRITES_NO),
            ('LOCAL_EFFECTS', library.WRITES_YES),
            ('EXTERNAL_EFFECTS', library.WRITES_YES),
            ('DANGEROUS', library.WRITES_YES),
            ('UNKNOWN', library.WRITES_UNKNOWN),
        ):
            with self.subTest(classification=classification):
                self.answers(answer(classification))
                self.assertEqual(library.verdict('что-то')['writes'], writes)

    def test_parse_warnings_make_the_answer_unknown(self) -> None:
        self.answers(answer('READONLY', parse_warnings=['tree-sitter reported a syntax error']))
        got = library.verdict('cat "не закрытая')
        self.assertEqual(got['writes'], library.WRITES_UNKNOWN)
        self.assertIn('syntax error', got['reason'])

    def test_a_heredoc_can_never_answer_no(self) -> None:
        # Замер 18.09.2026: рядом с heredoc bash-classify теряет `>`.
        self.answers(answer('READONLY', [command(['cat'])],
                            redirects=[{'operator': '<<', 'target': 'EOF'}]))
        got = library.verdict('cat <<EOF > f.txt')
        self.assertEqual(got['writes'], library.WRITES_UNKNOWN)
        self.assertTrue(got['notes'], 'страховка обязана объявиться в notes')

    # --- то, что модуль дописывает сам ---

    def test_the_directory_of_a_link_is_carried_through_cd(self) -> None:
        self.answers(answer('DANGEROUS', [
            command(['cd', '/tmp']),
            command(['rm', '-rf', 'build'], 'DANGEROUS', 'rm',
                    options=['-rf'], positionals=['build']),
        ], expression='cd /tmp && rm -rf build'))
        got = library.verdict('cd /tmp && rm -rf build', cwd='/repo')
        self.assertEqual(got['targets'], [{'path': '/tmp/build', 'kind': 'каталог', 'via': 'rm'}])

    def test_a_directory_change_behind_a_subshell_is_not_carried(self) -> None:
        # `(cd /tmp) && rm b` и `cd /tmp && rm b` в JSON неразличимы, поэтому
        # каталог не проводится вовсе: лучше без каталога, чем с чужим.
        self.answers(answer('DANGEROUS', [
            command(['cd', '/tmp']),
            command(['rm', 'b'], 'DANGEROUS', 'rm', positionals=['b']),
        ], expression='(cd /tmp) && rm b'))
        got = library.verdict('(cd /tmp) && rm b', cwd='/repo')
        self.assertEqual([item['path'] for item in got['targets']], ['b'])
        self.assertTrue(any('относительно каталога вызова' in note for note in got['notes']))

    def test_a_path_only_the_shell_knows_is_not_named_as_a_target(self) -> None:
        self.answers(answer('LOCAL_EFFECTS', [command(['cat'], 'LOCAL_EFFECTS', 'cat',
                                                      write_paths=['$OUT'])],
                            write_paths=['$OUT'], expression='cat > $OUT'))
        got = library.verdict('cat > $OUT', cwd='/repo')
        self.assertEqual(got['writes'], library.WRITES_YES)
        self.assertEqual(got['targets'], [], 'имя файла из $OUT выдумывать нечем')

    def test_a_shell_string_is_given_to_the_tool_a_second_time(self) -> None:
        # Замер 18.09.2026: перенаправление внутри строки `bash -c` теряется,
        # поэтому саму строку отдаём утилите отдельным вызовом.
        outer = answer('READONLY', [command(
            ['bash', '-c', 'cat > README.md'], 'READONLY', 'bash',
            inner_commands=[command(['cat'], 'READONLY', 'cat',
                                    delegation_mode='flag_value_is_expression',
                                    delegation_source='-c')],
        )], expression='bash -c "cat > README.md"')
        inner = answer('LOCAL_EFFECTS', [command(['cat'], 'LOCAL_EFFECTS', 'cat',
                                                 write_paths=['README.md'])],
                       write_paths=['README.md'], expression='cat > README.md')
        self.answers(outer, inner)
        got = library.verdict('bash -c "cat > README.md"', cwd='/repo')
        self.assertEqual(got['writes'], library.WRITES_YES)
        self.assertEqual([item['path'] for item in got['targets']], ['/repo/README.md'])
        self.assertIn('cat > README.md', self.input.read_text(encoding='utf-8'))

    def test_the_kind_of_a_target_comes_from_the_rule(self) -> None:
        for argv, rule, positionals, kind in (
            (['mkdir', 'build'], 'mkdir', ['build'], 'каталог'),
            (['kill', '4242'], 'kill', ['4242'], 'процесс'),
            (['git', 'push', 'origin'], 'git.push', ['origin'], 'сеть'),
            (['git', 'commit'], 'git.commit', [], 'git'),
            (['touch', 'x'], 'touch', ['x'], 'файл'),
        ):
            with self.subTest(rule=rule):
                self.answers(answer('LOCAL_EFFECTS', [
                    command(argv, 'LOCAL_EFFECTS', rule, positionals=positionals)]))
                got = library.verdict(' '.join(argv), cwd='/repo')
                self.assertEqual([item['kind'] for item in got['targets']], [kind])

    def test_parse_gives_the_tools_own_json_back(self) -> None:
        self.answers(answer('READONLY', [command(['ls'], 'READONLY', 'ls')]))
        got = library.parse('ls', cwd='/repo')
        self.assertEqual(got['classification'], 'READONLY')
        self.assertEqual(got['commands'][0]['matched_rule'], 'ls')
        self.assertEqual(got['cwd_walk']['cwd'], '/repo')


if __name__ == '__main__':
    unittest.main()
