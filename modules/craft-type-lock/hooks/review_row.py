"""Строка типа записи в объём ревью хода: замок — поставщик записей turn-review.

На каждой записи в Craft, место которой замок судил, в объём ложится строка
`entity`: команда и цели записи, типы места (владельцев: места новой
строки, источника и цели переноса, сменившей состояние сущности) и типы
содержимого — те же, по которым замок требовал скилл и шаблон, — или то, что
место не определено.
Ревью берёт из неё только выбор критериев, а не довод, что запись верна:
пропуск или запрет замка в строку не идёт. Строка пишется по контракту
поставщиков из README turn-review простым словарём через append_line
хранилища ядра; lib ревью замок не импортирует.

Строка ложится только за запись, которая случилась: на `pre-tool` замок её
считает и откладывает, а в объём кладёт на `post-tool` той же записи. Запрет
и отказ инструмента `post-tool` не дают, и строки без записи `write` в объёме
не остаётся: ревью такой ход остановило бы зря.
"""

import json

from place import Place, Verdict
from state import BrokenFile

# Контракт поставщиков ревью: файл объёма в зоне сессии, вид и поля строки.
VOLUME_FILE = 'turn-review-volume.jsonl'
KIND = 'entity'
PROMPT_ID_FIELD = 'prompt_id'
# Исходы, при которых место всех целей известно: тип определён. GUESSED —
# владельцы взяты всеми кандидатами, тип не определён.
DETERMINED = (Verdict.LOCATED, Verdict.NO_TARGET)
NO_TARGET_NOTE = 'у команды нет цели: тип только по тегам текста'
# Отложенные строки: пропущенная запись ждёт своего `post-tool`. Запрещённая
# ждёт напрасно, поэтому файл держит только последние MAX_PENDING.
PENDING_FILE = 'craft-type-lock-rows.json'
MAX_PENDING = 20
# Id вызова инструмента в сыром событии Claude: один и тот же на pre и post.
TOOL_USE_FIELD = 'tool_use_id'


def prompt_id_of(event) -> str | None:
    """Id реплики харнеса из сырого события; нет его или он не строка — None."""
    value = event.raw.get(PROMPT_ID_FIELD)
    return value if isinstance(value, str) and value else None


def tags_of(entities) -> list[str]:
    return sorted({tag for entity in entities for tag in entity.tags})


def entity_row(event, command: str, anchors, place: Place, text_tags: list[str]) -> dict | None:
    """Строка `entity` для записи; вызов подагента — None: его записей в объёме нет.

    `command` — команда как есть: по ней и `prompt_id` ревью находит запись
    `write`, к которой строка относится, без своего разбора команды.
    """
    if event.agent_id:
        return None
    prompt_id = prompt_id_of(event)
    head = {'kind': KIND} if prompt_id is None else {'kind': KIND, PROMPT_ID_FIELD: prompt_id}
    return {**head,
            'command': command,
            'targets': [anchor.block_id for anchor in anchors],
            'place_types': tags_of(place.entities),
            'content_types': sorted({*text_tags, *tags_of(place.edited)}),
            'determined': place.verdict in DETERMINED,
            'note': place.reason or NO_TARGET_NOTE}


def append_row(storage, row: dict | None) -> None:
    if row is not None:
        storage.append_line(VOLUME_FILE, json.dumps(row, ensure_ascii=False))


def row_key(event, command: str) -> str:
    """Ключ отложенной строки: id вызова инструмента, без него — реплика и команда."""
    call = event.raw.get(TOOL_USE_FIELD)
    if isinstance(call, str) and call:
        return call
    return f'{prompt_id_of(event) or ""}\n{command}'


def hold_row(storage, key: str, row: dict | None) -> None:
    """Отложить строку до `post-tool` записи. Зовётся под локом состояния замка."""
    if row is None:
        return
    held = read_held(storage)
    held.pop(key, None)
    held[key] = row
    storage.write_json(PENDING_FILE, dict(list(held.items())[-MAX_PENDING:]))


def release_row(storage, key: str) -> bool:
    """Запись случилась: отложенная строка — в объём. Зовётся под локом состояния."""
    held = read_held(storage)
    row = held.pop(key, None)
    if row is None:
        return False
    storage.write_json(PENDING_FILE, held)
    append_row(storage, row)
    return True


def read_held(storage) -> dict:
    path = storage.path(PENDING_FILE)
    try:
        held = storage.read_json(PENDING_FILE, default={})
    except json.JSONDecodeError as error:
        raise BrokenFile(path, f'файл отложенных строк замка {path} не JSON: {error}. Удали его') from error
    if not isinstance(held, dict) or not all(isinstance(row, dict) for row in held.values()):
        raise BrokenFile(path, f'файл отложенных строк замка {path} испорчен. Удали его')
    return held
