"""Тип каждой записи write: только из строки `entity` замка craft-type-lock.

Тип записи считает один хозяин — замок: место (страница, куда запись легла)
и содержимое (теги типа в тексте и правленая цель), те же типы, по которым
он требовал скилл и шаблон. Ревью своего поиска тегов не ведёт и берёт из
строки только выбор критериев. Это не довод, что запись верна: замок судил
место, а не содержание, и его пропуск или запрет в строку не идёт. Поэтому
запись без своей строки (замка нет, он выключен, упал) или со строкой «тип
не определён» получает критерии всех сущностей, а поручение называет её
подагенту: тип он определяет сам, по свежему чтению.

Пара «запись — строка»: та же реплика (`prompt_id`) и та же команда, строка
стоит в объёме раньше записи и ещё не отдана другой записи. Из таких
берётся последняя: запрещённая попытка оставляет свою строку, а повтор
кладёт новую.
"""

from typing import NamedTuple

from .records import EntityRecord, VolumeRecord, WriteRecord

NO_ROW = 'строки типа от замка craft-type-lock нет: замка нет, он выключен или упал'
COMMAND_PREVIEW = 80
TYPES_HEAD = ('Критерии сущностей выбраны по типу каждой записи из записей `entity` замка '
              'craft-type-lock: место (страница, куда запись легла) и содержимое (теги типа '
              'в тексте записи и правленая цель). Тип выбирает только критерии. Это не довод, '
              'что запись верна: замок судил место, а не содержание, и записанный блок ты '
              'читаешь сам.')
UNTYPED_HEAD = 'Тип записи не известен у записей:'
UNTYPED_TAIL = ('Для них взяты критерии всех сущностей. Определи сам, какие сущности задела '
                'каждая такая запись, по свежему чтению записанного блока и страницы, где он '
                'лежит, и проверь её по критериям этих сущностей. Критерии других сущностей '
                'к такой записи считай пройденными.')
ALL_TYPED = 'Тип известен у всех записей.'


class WriteType(NamedTuple):
    """Тип записи: место ∪ содержимое из строки замка или None — тип не известен."""
    number: int
    record: WriteRecord
    types: frozenset[str] | None
    why: str


def take_row(pending: list[EntityRecord], record: WriteRecord) -> EntityRecord | None:
    """Последняя ещё не отданная строка той же реплики и команды; отдаётся один раз."""
    for index in range(len(pending) - 1, -1, -1):
        row = pending[index]
        if row.prompt_id == record.prompt_id and row.command == record.command:
            return pending.pop(index)
    return None


def write_type(number: int, record: WriteRecord, row: EntityRecord | None) -> WriteType:
    if row is None:
        return WriteType(number, record, None, NO_ROW)
    if not row.determined:
        return WriteType(number, record, None, row.note)
    return WriteType(number, record, frozenset((*row.place_types, *row.content_types)), row.note)


def write_types(volume: list[VolumeRecord]) -> tuple[WriteType, ...]:
    """Тип каждой записи write объёма, по порядку записей."""
    pending: list[EntityRecord] = []
    found: list[WriteType] = []
    for record in volume:
        if isinstance(record, EntityRecord):
            pending.append(record)
        elif isinstance(record, WriteRecord):
            found.append(write_type(len(found) + 1, record, take_row(pending, record)))
    return tuple(found)


def selection_tags(volume: list[VolumeRecord]) -> frozenset[str] | None:
    """Теги, по которым выбираются критерии; None — брать все критерии по тегам.

    Объединение типов всех записей. У какой-то записи тип не известен — None:
    лишняя проверка дешевле пропущенной.
    """
    found: set[str] = set()
    for item in write_types(volume):
        if item.types is None:
            return None
        found |= item.types
    return frozenset(found)


def types_text(volume: list[VolumeRecord]) -> str:
    """Раздел поручения о типе записей: что выбрало критерии и где тип не известен."""
    untyped = [item for item in write_types(volume) if item.types is None]
    if not untyped:
        return f'{TYPES_HEAD}\n\n{ALL_TYPED}'
    lines = [f'- запись write № {item.number} «{item.record.command[:COMMAND_PREVIEW]}»: {item.why}'
             for item in untyped]
    return '\n\n'.join((TYPES_HEAD, UNTYPED_HEAD + '\n' + '\n'.join(lines), UNTYPED_TAIL))
