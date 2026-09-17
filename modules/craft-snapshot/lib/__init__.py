"""Поставщик стартового контекста: снимок базы Craft.

Отдаёт базе стартового контекста два куска markdown: правила базы — разделы
«Сущность базы #раздел», прочитанные по «Связям», — и память агента. Читает их
через `craft-sync --markdown`: connect-API Craft умеет отдавать markdown любого
блока деревом, а инструмент уже знает и базовый URL, и повторы, и таймауты.

Хуку нельзя висеть: он держит старт сессии. Поэтому повторов здесь нет
(`--retries 0 --rl-retries 0`), таймаут короткий, а ошибка API — это пустой
текст и строка в журнале сессии, а не падение. Пустой стартовый контекст хуже
полного, но лучше, чем сессия, которая не стартует.

В репозитории снимка не лежит и лежать не может: это живая память о жизни
Влада, и гвард `tools/no-snapshot-files.js` следит за этим отдельно.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

# Корни, которые читает поставщик. Первый — вход в правила базы, дальше
# инструмент идёт по «Связям»; второй — память агента, читается целиком.
RULES_ROOT = 'a6784801-9d92-875c-f146-50159368745b'
MEMORY_ROOT = 'e8132891-81f4-2d63-36f1-d3623d0147b6'

BINARY_ENV = 'CRAFT_SYNC_BIN'
BASE_ENV = 'CRAFT_API_BASE'
BINARY_NAME = 'craft-sync'
DEFAULT_BINARY = '~/.local/bin/craft-sync'
JOURNAL_FILE = 'craft-snapshot.jsonl'

# Замер 17.09.2026: правила по «Связям» читаются ~13 с, память ~1,3 с.
CALL_TIMEOUT = 20
PROCESS_TIMEOUT = 45

RULES_TITLE = '# Правила базы Craft'
MEMORY_TITLE = '# Память агента'


def binary() -> str | None:
    """Где `craft-sync`. Контейнер эфемерный, поэтому путь не зашит намертво."""
    named = os.environ.get(BINARY_ENV)
    if named and Path(named).expanduser().is_file():
        return str(Path(named).expanduser())
    default = Path(DEFAULT_BINARY).expanduser()
    if default.is_file():
        return str(default)
    return shutil.which(BINARY_NAME)


def read(tool: str, block_id: str, follow: bool) -> tuple[str, str | None]:
    """Один вызов инструмента. Вернёт текст либо причину, по которой его нет."""
    command = [
        tool, '--markdown', block_id,
        '--retries', '0', '--rl-retries', '0', '--timeout', str(CALL_TIMEOUT),
    ]
    if follow:
        command.append('--follow-links')
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=PROCESS_TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as failure:
        return '', f'{type(failure).__name__}: {failure}'
    if done.returncode != 0:
        return '', f'craft-sync вернул {done.returncode}: {done.stderr.strip()[:300]}'
    return done.stdout.strip(), (done.stderr.strip()[:300] or None)


def note(storage, entry: dict) -> None:
    """Журнал поставщика в зоне сессии: по строке на вызов базы."""
    if storage is None:
        return
    storage.append_line(JOURNAL_FILE, json.dumps({'at': time.time(), **entry}, ensure_ascii=False))


def provide(event, storage) -> str:
    """Что база кладёт в стартовый контекст от этого поставщика."""
    tool = binary()
    if tool is None:
        note(storage, {'event': getattr(event, 'event', None), 'chars': 0,
                       'error': f'{BINARY_NAME} не найден: ни в {BINARY_ENV}, ни в {DEFAULT_BINARY}, ни в PATH'})
        return ''
    if not os.environ.get(BASE_ENV):
        note(storage, {'event': getattr(event, 'event', None), 'chars': 0,
                       'error': f'в окружении нет {BASE_ENV}: читать нечем'})
        return ''

    pieces = []
    problems = {}
    for title, block_id, follow in (
        (RULES_TITLE, RULES_ROOT, True),
        (MEMORY_TITLE, MEMORY_ROOT, False),
    ):
        text, problem = read(tool, block_id, follow)
        if problem:
            problems[block_id] = problem
        if text:
            pieces.append(f'{title}\n\n{text}')
    snapshot = '\n\n'.join(pieces)
    note(storage, {'event': getattr(event, 'event', None), 'chars': len(snapshot),
                   'problems': problems or None})
    return snapshot
