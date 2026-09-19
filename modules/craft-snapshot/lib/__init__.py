"""Поставщик стартового контекста: снимок базы Craft.

Отдаёт базе стартового контекста два куска markdown: правила базы — разделы
«Сущность базы #раздел», прочитанные по «Связям», — и память агента. Читает их
через `craft-sync --markdown`: connect-API Craft умеет отдавать markdown любого
блока деревом, а инструмент уже знает и базовый URL, и обход по «Связям».

Правила читаются с `--container`: почти все разделы лежат в одной странице, и
её единственное глубокое чтение отвечает за них всех. Замер 17.09.2026 на живом
пространстве: 3 запроса вместо 23 при побайтово том же тексте. Обход от этого не
меняется — что не нашлось в контейнере, дочитывается по id, — так что переезд
раздела стоит запроса, а не куска снимка.

Снимок либо собирается целиком, либо не отдаётся вовсе. Половина снимка —
память без правил базы — хуже пустоты: агент считал бы, что правила прочитаны,
и работал бы по памяти без них. Поэтому отказ любого из двух чтений превращает
ответ поставщика в один короткий алерт, и база печатает в контекст его, а не
обрывок.

Повторы живут здесь, а не в craft-sync (`--retries 0 --rl-retries 0`): у хука
одна политика на весь сбор, и она должна быть видна в одном месте. Повторяем
только то, что может пройти со второго раза. Остальные 4xx постоянны — 404 не
станет найденным, а 401 авторизованным, — и повтор на них только тратит время
старта сессии, поэтому на них сразу отказ.

Лестниц две, потому что чинится разное. Сетевая икота, свой таймаут и пятисотки
проходят за секунды: три повтора с паузами 1, 3, 9 с. А 429 — это окно лимита у
connect-ссылки, и оно живёт десятками секунд: замер 17.09.2026 показал, что
после серии полных обходов лимит не отпускает и через минуту. Поэтому у 429 своя
лестница, та же, что заложена в самом craft-sync: пять повторов с паузами
5, 10, 20, 40, 60 с. Ждать столько на старте сессии дорого, но альтернатива —
отдать алерт вместо снимка там, где помогло бы ожидание.

Лестница выбирается по последней беде, а не по первой: 429 на третьем блоке не
обязан удлинять ожидание сетевой икоты, случившейся после него.

Худший случай по времени — все попытки по длинной лестнице: два чтения ×
(шесть попыток × таймаут вызова + пять пауз). Он обязан помещаться в `timeout` строки
хука, который пишет установщик, — иначе харнес убьёт сбор на полпути. Это
проверяется тестом по числам отсюда и из установщика, а не на глаз.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

from jarvis import registry

# Корни, которые читает поставщик. Первый — вход в правила базы, дальше
# инструмент идёт по «Связям»; второй — память агента, читается целиком.
RULES_ROOT = 'a6784801-9d92-875c-f146-50159368745b'
MEMORY_ROOT = 'e8132891-81f4-2d63-36f1-d3623d0147b6'
# Страница «Архив» сферы «Личное 💭»: в ней лежит 21 раздел правил из 23.
# Её одно глубокое чтение заменяет 21 запрос, а два раздела снаружи («Строить
# управляемый поток работы #алгоритм» и «Записать день #задача/15м») обход
# дочитывает сам. Регистр id — как в Craft: connect-API к нему чувствителен.
# Уедет раздел из «Архива» — обход дочитает его отдельным запросом, и снимок
# не изменится: контейнер ускоряет обход, а не задаёт его.
RULES_CONTAINER = 'B19D996C-6329-483A-A29D-F16CFB8F765B'

BASE_ENV = 'CRAFT_API_BASE'
BINARY_NAME = 'craft-sync'
JOURNAL_FILE = 'craft-snapshot.jsonl'
MODULE_DIR = Path(__file__).resolve().parents[1]

# `.env` — машинный, gitignored источник connect-ссылки Craft. В worktree его
# нет: общий git-dir живёт в главном checkout, рядом с которым и лежит файл.
# Читаем только нужную переменную, а не исполняем пользовательский `.env` как
# shell-код из хука.
DOTENV_BASE = re.compile(
    r'''^\s*(?:export\s+)?CRAFT_API_BASE\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<plain>[^\s#]*))(?:\s*#.*)?\s*$'''
)

# Замер 17.09.2026: правила по «Связям» — 23 блока, ~4,6 с параллельным
# обходом; память — один блок, ~1,1 с.
# Лестницы заданы паузами, а число попыток из них выводится: пауза стоит между
# попытками, поэтому попыток на одну больше, чем пауз. Так ни одно число не
# может оказаться мёртвым — а оказаться могло бы, задай мы длину отдельно.
# Быстрая лестница: сеть, свой таймаут, пятисотки.
BACKOFF_SECONDS = (1, 3, 9)
ATTEMPTS = len(BACKOFF_SECONDS) + 1
# Длинная лестница: 429, окно лимита у connect-ссылки. Числа — те же, что в
# самом craft-sync (`--rl-retries 5`), и взяты они оттуда не для красоты: окно
# живёт десятками секунд, и коротким повтором его не пересидеть.
BACKOFF_429_SECONDS = (5, 10, 20, 40, 60)
ATTEMPTS_429 = len(BACKOFF_429_SECONDS) + 1
# Коды, которые повтор может исправить: 429 — лимит, 5xx — беда на той стороне.
# Прочие 4xx постоянны.
BUDGET_CODE = 429
HTTP_CODE = re.compile(r'HTTP (\d{3})')
# Сколько ждём одну попытку целиком и один запрос внутри неё.
CALL_TIMEOUT = 60
REQUEST_TIMEOUT = 30

RULES_TITLE = '# Правила базы Craft'
MEMORY_TITLE = '# Память агента'
ALERT = 'стартовый контекст: снимок Craft не собран: {reason}'


def binary(storage=None) -> str:
    """Взять исполняемый файл у явно требуемого модуля `craft-sync`.

    Глобальный PATH и домашний каталог тут не являются контрактом: они могли
    остаться от другого снимка окружения. Соседний модуль либо уже собрал свой
    бинарник, либо дождётся собственного живого SessionStart-хука.
    """
    found = registry.find('craft-sync', MODULE_DIR)
    if found is None:
        raise RuntimeError('не найден требуемый модуль craft-sync')
    return found.load().binary(storage)


def repository_env() -> Path | None:
    """Найти `.env` главного checkout и не вывести ни его путь, ни содержимое.

    `git-common-dir` одинаково работает в основном checkout и в отдельном
    worktree. Вне git-репозитория это штатно `None`: облако передаёт значение
    через окружение, а поставщик тогда ничего локального не ищет.
    """
    try:
        done = subprocess.run(
            ['git', '-C', str(Path.cwd()), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except OSError:
        return None
    if done.returncode != 0:
        return None
    candidate = Path(done.stdout.strip()).parent / '.env'
    return candidate if candidate.is_file() else None


def dotenv_base(path: Path) -> str | None:
    """Извлечь прямое значение `CRAFT_API_BASE` из обычного dotenv-файла.

    Поддерживаются `KEY=value`, `export KEY=value` и одинарные/двойные
    кавычки. Сложные shell-выражения намеренно не исполняем: `.env` — секрет,
    а не программа, которую SessionStart должен запускать.
    """
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return None
    for line in lines:
        found = DOTENV_BASE.match(line)
        if found is not None:
            return next(value for value in found.group('double', 'single', 'plain') if value is not None) or None
    return None


def craft_environment() -> dict[str, str] | None:
    """Среда только для `craft-sync`, не изменение среды процесса Codex."""
    base = os.environ.get(BASE_ENV)
    if not base:
        env_file = repository_env()
        base = dotenv_base(env_file) if env_file is not None else None
    if not base:
        return None
    environment = os.environ.copy()
    environment[BASE_ENV] = base
    return environment


def attempt(tool: str, block_id: str, follow: bool, container: str = '',
            environment: dict[str, str] | None = None) -> tuple[str, str | None]:
    """Одна попытка чтения. Текст либо причина, по которой его нет."""
    command = [
        tool, '--markdown', block_id,
        '--retries', '0', '--rl-retries', '0', '--timeout', str(REQUEST_TIMEOUT),
    ]
    if follow:
        command.append('--follow-links')
    if container:
        command += ['--container', container]
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=CALL_TIMEOUT, check=False,
            env=environment,
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


def http_code(reason: str) -> int | None:
    """Код ответа из текста ошибки craft-sync. Своих повторов у него нет, и код
    он называет как есть; кода не видно (сеть, таймаут, битый JSON) — None."""
    found = HTTP_CODE.search(reason or '')
    return int(found.group(1)) if found else None


def permanent(reason: str) -> bool:
    """Повтор этого не исправит: 4xx кроме 429 постоянны."""
    code = http_code(reason)
    return code is not None and 400 <= code < 500 and code != BUDGET_CODE


def ladder(reason: str) -> tuple[int, tuple[int, ...]]:
    """Сколько попыток и с какими паузами лечить эту беду."""
    if http_code(reason) == BUDGET_CODE:
        return ATTEMPTS_429, BACKOFF_429_SECONDS
    return ATTEMPTS, BACKOFF_SECONDS


def read(tool: str, block_id: str, follow: bool, container: str = '',
         sleep=time.sleep, environment: dict[str, str] | None = None) -> tuple[str, str | None]:
    """Чтение с повторами. Все попытки впустую — последняя причина наружу.

    Лестница выбирается по последней беде: пока отвечает 429, ждём по длинной,
    а как только беда сменилась на сетевую, дальше идём по короткой.
    """
    problem = None
    number = 0
    while True:
        if number:
            attempts, pauses = ladder(problem)
            if number >= attempts:
                return '', problem
            sleep(pauses[number - 1])
        text, problem = attempt(tool, block_id, follow, container, environment)
        if text:
            return text, None
        if permanent(problem):
            return '', problem
        number += 1


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
    try:
        tool = binary(storage)
    except Exception as failure:
        return failed(storage, where, f'{BINARY_NAME} недоступен: {failure}')
    environment = craft_environment()
    if environment is None:
        return failed(storage, where, f'не задан {BASE_ENV}: ни в окружении, ни в .env главного checkout')

    pieces = []
    for title, block_id, follow, container in (
        (RULES_TITLE, RULES_ROOT, True, RULES_CONTAINER),
        (MEMORY_TITLE, MEMORY_ROOT, False, ''),
    ):
        text, problem = read(tool, block_id, follow, container, sleep=sleep, environment=environment)
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
