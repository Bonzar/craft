"""Поставщик стартового контекста: снимок базы Craft.

Отдаёт базе стартового контекста два куска markdown: правила базы — разделы
«Сущность базы #раздел», прочитанные по «Связям», — и память агента. Читает их
через `craft-sync --markdown`: connect-API Craft умеет отдавать markdown любого
блока деревом, а инструмент уже знает и базовый URL, и обход по «Связям».

Снимок либо собирается целиком, либо не отдаётся вовсе. Половина снимка —
память без правил базы — хуже пустоты: агент считал бы, что правила прочитаны,
и работал бы по памяти без них. Поэтому отказ любого из двух чтений превращает
ответ поставщика в один короткий алерт, и база печатает в контекст его, а не
обрывок.

Повторы живут здесь, а не в craft-sync (`--retries 0 --rl-retries 0`): у хука
одна политика на весь сбор, и она должна быть видна в одном месте. Повторяем
только то, что может пройти со второго раза: сетевую ошибку, свой таймаут,
пятисотки и 429 «бюджет блоков исчерпан». Остальные 4xx постоянны — 404 не
станет найденным, а 401 авторизованным, — и повтор на них только тратит время
старта сессии, поэтому на них сразу отказ.

Худший случай по времени: два чтения × три попытки × таймаут вызова плюс паузы,
то есть около 386 с. Он обязан помещаться в `timeout` строки хука, который
пишет установщик, — иначе харнес убьёт сбор на полпути.
"""

import json
import os
import re
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

# Замер 17.09.2026: правила по «Связям» — 23 блока, ~4,6 с параллельным
# обходом; память — один блок, ~1,1 с.
ATTEMPTS = 3
BACKOFF_SECONDS = (1, 3, 9)
# Коды, которые повтор может исправить: 429 — окно бюджета блоков, 5xx — беда
# на той стороне. Прочие 4xx постоянны.
RETRIED_CODES = frozenset({429})
HTTP_CODE = re.compile(r'HTTP (\d{3})')
# Сколько ждём одну попытку целиком и один запрос внутри неё.
CALL_TIMEOUT = 60
REQUEST_TIMEOUT = 30

RULES_TITLE = '# Правила базы Craft'
MEMORY_TITLE = '# Память агента'
ALERT = 'стартовый контекст: снимок Craft не собран: {reason}'


def binary() -> str | None:
    """Где `craft-sync`. Контейнер эфемерный, поэтому путь не зашит намертво."""
    named = os.environ.get(BINARY_ENV)
    if named and Path(named).expanduser().is_file():
        return str(Path(named).expanduser())
    default = Path(DEFAULT_BINARY).expanduser()
    if default.is_file():
        return str(default)
    return shutil.which(BINARY_NAME)


def attempt(tool: str, block_id: str, follow: bool) -> tuple[str, str | None]:
    """Одна попытка чтения. Текст либо причина, по которой его нет."""
    command = [
        tool, '--markdown', block_id,
        '--retries', '0', '--rl-retries', '0', '--timeout', str(REQUEST_TIMEOUT),
    ]
    if follow:
        command.append('--follow-links')
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=CALL_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired:
        return '', f'вызов не уложился в {CALL_TIMEOUT} с'
    except OSError as failure:
        return '', f'{type(failure).__name__}: {failure}'
    if done.returncode != 0:
        return '', f'craft-sync вернул {done.returncode}: {done.stderr.strip()[:300]}'
    text = done.stdout.strip()
    if not text:
        return '', 'craft-sync ничего не прочитал'
    return text, None


def permanent(reason: str) -> bool:
    """Повтор этого не исправит: 4xx кроме 429 постоянны.

    Код берётся из текста ошибки craft-sync — своих повторов у него нет, и он
    называет код как есть. Кода в тексте не видно (сеть, таймаут, битый JSON) —
    считаем, что повтор имеет смысл.
    """
    found = HTTP_CODE.search(reason or '')
    if not found:
        return False
    code = int(found.group(1))
    return 400 <= code < 500 and code not in RETRIED_CODES


def read(tool: str, block_id: str, follow: bool, sleep=time.sleep) -> tuple[str, str | None]:
    """Чтение с повторами. Все попытки впустую — последняя причина наружу."""
    problem = None
    for number in range(ATTEMPTS):
        if number:
            sleep(BACKOFF_SECONDS[min(number - 1, len(BACKOFF_SECONDS) - 1)])
        text, problem = attempt(tool, block_id, follow)
        if text:
            return text, None
        if permanent(problem):
            return '', problem
    return '', problem


def note(storage, entry: dict) -> None:
    """Журнал поставщика в зоне сессии: по строке на вызов базы."""
    if storage is None:
        return
    storage.append_line(JOURNAL_FILE, json.dumps({'at': time.time(), **entry}, ensure_ascii=False))


def provide(event, storage, sleep=time.sleep) -> str:
    """Что база кладёт в стартовый контекст от этого поставщика.

    Либо снимок целиком, либо алерт. Третьего нет: частичный снимок молча
    подменил бы правила базы их половиной.
    """
    where = getattr(event, 'event', None)
    tool = binary()
    if tool is None:
        return failed(storage, where,
                      f'{BINARY_NAME} не найден: ни в {BINARY_ENV}, ни в {DEFAULT_BINARY}, ни в PATH')
    if not os.environ.get(BASE_ENV):
        return failed(storage, where, f'в окружении нет {BASE_ENV}')

    pieces = []
    for title, block_id, follow in (
        (RULES_TITLE, RULES_ROOT, True),
        (MEMORY_TITLE, MEMORY_ROOT, False),
    ):
        text, problem = read(tool, block_id, follow, sleep=sleep)
        if problem:
            return failed(storage, where, f'{title.lstrip("# ")}: {problem}')
        pieces.append(f'{title}\n\n{text}')

    snapshot = '\n\n'.join(pieces)
    note(storage, {'event': where, 'chars': len(snapshot), 'alert': None})
    return snapshot


def failed(storage, where, reason: str) -> str:
    """Алерт вместо снимка: коротко, с причиной, и та же причина в журнал."""
    alert = ALERT.format(reason=reason)
    note(storage, {'event': where, 'chars': 0, 'alert': alert})
    return alert
