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
"""

import json

from place import Place, Verdict

# Контракт поставщиков ревью: файл объёма в зоне сессии, вид и поля строки.
VOLUME_FILE = 'turn-review-volume.jsonl'
KIND = 'entity'
PROMPT_ID_FIELD = 'prompt_id'
# Исходы, при которых место всех целей известно: тип определён. GUESSED —
# владельцы взяты всеми кандидатами, тип не определён.
DETERMINED = (Verdict.LOCATED, Verdict.NO_TARGET)
NO_TARGET_NOTE = 'у команды нет цели: тип только по тегам текста'


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
