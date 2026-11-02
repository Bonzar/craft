"""Тип каждой записи write: только из строки `entity` замка craft-type-lock.

Тип записи считает один хозяин — замок: место (страница, куда запись легла)
и содержимое (теги типа в тексте и правленая цель), те же типы, по которым
он требовал скилл и шаблон. Ревью своего поиска тегов не ведёт и берёт из
строки только выбор критериев. Это не довод, что запись верна: замок судил
место, а не содержание, и его пропуск или запрет в строку не идёт. Поэтому
запись без своей строки (замка нет, он выключен, упал) или со строкой «тип
не определён» получает критерии всех сущностей, а поручение называет её
подагенту: тип он определяет сам, по свежему чтению. Поручение перечисляет
каждую запись по номеру с её типом: критерий сущности спрашивает тип записи,
а не тег в её тексте.

Пара «запись — строка»: та же реплика (`prompt_id`) и та же команда, строка
ещё не отдана другой записи. Место в объёме не важно: замок кладёт строку
на `post-tool` записи, параллельно с поставщиком записей, и строка ложится
то раньше записи, то позже. Из подходящих берётся первая: строку оставляет
только случившаяся запись, поэтому одинаковые записи одной реплики и их
строки идут в одном порядке.
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
LIST_HEAD = ('Тип каждой записи `write` по её номеру в объёме (записи `write` считаются '
             'по порядку, с единицы). Тип — место ∪ содержимое. Критерий сущности проверяет '
             'запись, чей тип включает тег этой сущности, даже если в тексте записи тега нет.')
NO_TYPES = '—'
EMPTY_TYPE = 'тип пуст: сущности ни в месте, ни в содержимом'
UNDETERMINED = 'тип не определён'
UNTYPED_TAIL = (f'Тип записи не известен у записей с пометкой «{UNDETERMINED}»: для них взяты '
                'критерии всех сущностей. Определи сам, какие сущности задела каждая такая '
                'запись, по свежему чтению записанного блока и страницы, где он лежит, и '
                'проверь её по критериям этих сущностей. Критерии других сущностей к такой записи считай пройденными.')
ALL_TYPED = 'Тип известен у всех записей.'
NO_WRITES = 'Записей `write` в объёме нет.'
LOST_WRITES_PROBLEM = ('в объёме есть строки типа записей от замка craft-type-lock ({rows}), а '
                       'записей write в объёме нет: замок кладёт строку только за случившуюся '
                       'запись, значит поставщик записей write (review-craft-calls или другой '
                       'с for = ["turn-review"]) не стоит или упал. Это не ход без записей. '
                       'Скажи об этом человеку: проверить, стоит ли поставщик записей')


class WriteType(NamedTuple):
    """Тип записи: место ∪ содержимое из строки замка или None — тип не известен.

    `place` и `content` — части типа для поручения; у записи без типа пусты.
    """
    number: int
    record: WriteRecord
    types: frozenset[str] | None
    why: str
    place: tuple[str, ...] = ()
    content: tuple[str, ...] = ()


def take_row(pending: list[EntityRecord], record: WriteRecord) -> EntityRecord | None:
    """Первая ещё не отданная строка той же реплики и команды; отдаётся один раз."""
    for index, row in enumerate(pending):
        if row.prompt_id == record.prompt_id and row.command == record.command:
            return pending.pop(index)
    return None


def write_type(number: int, record: WriteRecord, row: EntityRecord | None) -> WriteType:
    if row is None:
        return WriteType(number, record, None, NO_ROW)
    if not row.determined:
        return WriteType(number, record, None, row.note)
    return WriteType(number, record, frozenset((*row.place_types, *row.content_types)), row.note,
                     row.place_types, row.content_types)


def write_types(volume: list[VolumeRecord]) -> tuple[WriteType, ...]:
    """Тип каждой записи write объёма, по порядку записей."""
    pending = [record for record in volume if isinstance(record, EntityRecord)]
    writes = [record for record in volume if isinstance(record, WriteRecord)]
    return tuple(write_type(number, record, take_row(pending, record))
                 for number, record in enumerate(writes, start=1))


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


def _tags_text(tags: tuple[str, ...]) -> str:
    return ', '.join(tags) or NO_TYPES


def type_line(item: WriteType) -> str:
    """Строка поручения о записи: номер, начало команды и тип или почему он не известен."""
    head = f'- запись write № {item.number} «{item.record.command[:COMMAND_PREVIEW]}»'
    if item.types is None:
        return f'{head}: {UNDETERMINED} — {item.why}'
    if not item.types:
        return f'{head}: {EMPTY_TYPE}'
    union = tuple(dict.fromkeys((*item.place, *item.content)))
    return (f'{head}: тип {_tags_text(union)} (место {_tags_text(item.place)}; '
            f'содержимое {_tags_text(item.content)})')


def types_text(volume: list[VolumeRecord]) -> str:
    """Раздел поручения о типе записей: тип каждой записи по номеру и где он не известен."""
    found = write_types(volume)
    if not found:
        return f'{TYPES_HEAD}\n\n{NO_WRITES}'
    listing = '\n'.join((LIST_HEAD, *(type_line(item) for item in found)))
    tail = ALL_TYPED if all(item.types is not None for item in found) else UNTYPED_TAIL
    return f'{TYPES_HEAD}\n\n{listing}\n\n{tail}'


def lost_writes_problem(volume: list[VolumeRecord]) -> str | None:
    """Строки замка есть, а записей write нет ни одной: записи некому было положить.

    Без этой проверки ход отпускался бы как «записей не было». Строку замок
    кладёт только за случившуюся запись, но поставщик записей мог упасть на
    одном вызове и устоять на других; одна строка без пары в объёме с записями
    бедой не считается, проверяется только объём без единой записи.
    """
    rows = sum(1 for record in volume if isinstance(record, EntityRecord))
    if not rows or any(isinstance(record, WriteRecord) for record in volume):
        return None
    return LOST_WRITES_PROBLEM.format(rows=rows)
