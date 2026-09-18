"""Разбор команды оболочки: дерево и ответ «пишет ли она и куда».

Модули и замки живут на Python, а полного дерева команды из стандартной
библиотеки не собрать: там есть только токены shlex, и по ним не видно ни
перенаправлений, ни подстановок, ни строки внутри `bash -c`. Поэтому разбор
стоит бинарником на Go рядом с craft-sync, а здесь — вызов и разбор ответа.

Бинарник ищется как у craft-snapshot: переменная окружения, потом известный
путь, потом PATH. Контейнер облачной сессии эфемерный, и жёсткий путь в нём
живёт до первой пересборки.

Ошибку этот модуль не глотает: нет бинарника, не разобрался JSON, упал вызов —
наружу идёт исключение с тем, что именно не вышло. Молчаливого ответа «команда
только читает» здесь нет и быть не может: замок, получивший такой ответ, пропустит
удаление каталога, и человек об этом не узнает.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

BINARY_ENV = 'SHELL_TREE_BIN'
BINARY_NAME = 'shell-tree'
DEFAULT_BINARY = '~/.local/bin/shell-tree'
# Разбор команды — работа на миллисекунды; секунды здесь означают, что что-то
# пошло не так, и ждать их дольше незачем: замок стоит перед вызовом инструмента.
CALL_TIMEOUT = 10


class ShellTreeError(RuntimeError):
    """Разбор не состоялся. Текст говорит, что именно не вышло."""


def binary() -> str:
    """Где бинарник. Не нашёлся — исключение с тем, где искали."""
    named = os.environ.get(BINARY_ENV)
    if named and Path(named).expanduser().is_file():
        return str(Path(named).expanduser())
    default = Path(DEFAULT_BINARY).expanduser()
    if default.is_file():
        return str(default)
    found = shutil.which(BINARY_NAME)
    if found:
        return found
    raise ShellTreeError(
        f'{BINARY_NAME} не найден: ни в {BINARY_ENV}, ни в {DEFAULT_BINARY}, ни в PATH'
    )


def parse(command: str, cwd: str = '', rules: str = '', commands: str = '') -> dict:
    """Дерево команды: звенья, слова, перенаправления, подстановки, каталоги.

    cwd — каталог вызова: от него считаются относительные пути и cd. Команда с
    синтаксической ошибкой — исключение: дерева у неё нет.
    """
    return call('parse', command, cwd, rules, commands)


def verdict(command: str, cwd: str = '', rules: str = '', commands: str = '') -> dict:
    """Пишет ли команда: writes (да, нет, неизвестно), цели записи, причина.

    Предикат собран из двух источников данных: вендоренной базы команд
    (`commands`, по умолчанию рядом с бинарником) и списков «только читает»
    (`rules`, там же).

    Синтаксическая ошибка сюда не роняет: неразобранная команда — это
    «неизвестно», и замок спросит человека.
    """
    return call('verdict', command, cwd, rules, commands)


def call(mode: str, command: str, cwd: str, rules: str, commands: str = '') -> dict:
    """Один вызов бинарника. Ответ — JSON на stdout, команда — на stdin."""
    tool = binary()
    argv = [tool, mode]
    if cwd:
        argv += ['--cwd', cwd]
    if rules:
        argv += ['--rules', rules]
    if commands:
        argv += ['--commands', commands]
    try:
        done = subprocess.run(
            argv, input=command, capture_output=True, text=True,
            timeout=CALL_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        raise ShellTreeError(f'{BINARY_NAME} {mode} не уложился в {CALL_TIMEOUT} с') from None
    except OSError as failure:
        raise ShellTreeError(f'{BINARY_NAME} {mode} не запустился: {failure}') from failure
    if done.returncode != 0:
        raise ShellTreeError(
            f'{BINARY_NAME} {mode} вернул {done.returncode}: {done.stderr.strip()[:300]}'
        )
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as failure:
        raise ShellTreeError(
            f'ответ {BINARY_NAME} {mode} — не JSON: {failure}; начало ответа: {done.stdout[:200]!r}'
        ) from failure
