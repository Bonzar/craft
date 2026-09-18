"""Разбор команды оболочки: дерево и ответ «пишет ли она и куда».

Модуль самодостаточен. Исходники разбора лежат в нём самом (`src/`), данные
предиката тоже (`data/`), а бинарник он собирает сам на старте сессии — своим
хуком, в свой `bin/`. В setup-скрипте окружения о модуле нет ни строки: нужен
только Go на PATH.

Отсюда и поиск: бинарник берётся только из `bin/` модуля. Рядом с бинарником
данные больше не ищутся — где они, знает модуль, и он говорит это бинарнику
ключами `--rules` и `--commands`.

Молчаливого «только читает» здесь нет: нет бинарника, упал вызов, ответ не
JSON — наружу идёт исключение. Замок, получивший такой ответ молча, пропустил
бы удаление каталога, и человек об этом не узнал бы.
"""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

BINARY_NAME = 'shell-tree'
MODULE_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIR = MODULE_DIR / 'src'
BINARY = MODULE_DIR / 'bin' / BINARY_NAME
FINGERPRINT = MODULE_DIR / 'bin' / 'fingerprint.json'
RULES = MODULE_DIR / 'data' / 'read-only-rules.json'
COMMANDS = MODULE_DIR / 'data' / 'commands'

# Файлы, из которых собирается бинарник: по ним и считается отпечаток. Тесты
# входят в него намеренно — правка теста пересобирает бинарник зря, но список
# «что считать исходником» остаётся одной понятной строкой.
SOURCE_GLOBS = ('*.go', 'go.mod', 'go.sum')

# Маркер сборки в зоне сессии и журнал модуля там же.
MARKER_FILE = 'shell-tree-build.json'
JOURNAL_FILE = 'shell-tree.jsonl'
BUILDING = 'сборка идёт'
READY = 'готов'

# Переменная остаётся только для тестов: настоящий путь у модуля один.
BINARY_ENV = 'SHELL_TREE_BIN'

# Шаг ожидания живой сборки. Потолка у ожидания нет — ждём, пока жив её процесс.
WAIT_STEP = 0.05
# Разбор команды — работа на миллисекунды; секунды значат, что что-то пошло не
# так, а замок стоит перед вызовом инструмента.
CALL_TIMEOUT = 30


class ShellTreeError(RuntimeError):
    """Разбора не вышло. Текст говорит, что именно не получилось."""


def fingerprint(source: Path | None = None) -> str:
    """Отпечаток исходников: по нему хук решает, нужна ли сборка."""
    source = Path(source) if source is not None else SOURCE_DIR
    digest = hashlib.sha256()
    files = []
    for pattern in SOURCE_GLOBS:
        files += sorted(source.glob(pattern))
    for path in sorted(set(files)):
        digest.update(path.name.encode('utf-8'))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def still_running(pid) -> bool:
    """Жив ли процесс. Не число — считаем, что жив: гонку молча не выигрываем."""
    if not isinstance(pid, int):
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_build(storage) -> None:
    """Подождать сборку, если она идёт. Потолка нет — ждём живой процесс.

    Так же ждут друг друга копии стартового контекста: срок не знает, сколько
    займёт сборка, а живость процесса знает. Сборки нет или её процесс умер —
    ждать нечего, и решение принимает вызвавший.
    """
    if storage is None:
        return
    while True:
        if BINARY.is_file():
            return
        mark = storage.read_json(MARKER_FILE, default=None) or {}
        if mark.get('state') != BUILDING or not still_running(mark.get('pid')):
            return
        time.sleep(WAIT_STEP)


def binary(storage=None) -> str:
    """Где бинарник. Нет — исключение с тем, где его ждали и почему его нет."""
    named = os.environ.get(BINARY_ENV)  # только для тестов
    if named and Path(named).expanduser().is_file():
        return str(Path(named).expanduser())
    if BINARY.is_file():
        return str(BINARY)
    wait_for_build(storage)
    if BINARY.is_file():
        return str(BINARY)
    raise ShellTreeError(
        f'{BINARY_NAME} не собран: {BINARY} нет, и сборка не идёт. '
        'Бинарник собирает хук модуля на старте сессии; нужен Go на PATH'
    )


def parse(command: str, cwd: str = '', storage=None) -> dict:
    """Дерево команды: звенья, слова, перенаправления, подстановки, каталоги.

    cwd — каталог вызова: от него считаются относительные пути и cd. Команда с
    синтаксической ошибкой — исключение: дерева у неё нет.
    """
    return call('parse', command, cwd, storage)


def verdict(command: str, cwd: str = '', storage=None) -> dict:
    """Пишет ли команда: writes (да, нет, неизвестно), цели записи, причина.

    Синтаксическая ошибка сюда не роняет: неразобранная команда — «неизвестно»,
    и замок спросит человека.
    """
    return call('verdict', command, cwd, storage)


def call(mode: str, command: str, cwd: str, storage=None) -> dict:
    """Один вызов бинарника. Данные модуля он получает ключами, а не поиском."""
    tool = binary(storage)
    argv = [tool, mode, '--rules', str(RULES), '--commands', str(COMMANDS)]
    if cwd:
        argv += ['--cwd', cwd]
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
