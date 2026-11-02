"""Записи объёма ревью: тип на каждый вид и фабрики, которыми их строят поставщики.

Запись — размеченное объединение по `kind`: write, read, skill, prompt,
answer, anchor. Обязательные поля у каждого вида свои, их описывают
поставщики в data/review-records.md. Тип проверяет поля при каждой сборке,
поэтому запись без `kind` или с полем не того типа до объёма не доходит.

Номер хода `turn` фабрики не принимают: его ставит только append_turn_record.
Объём с диска читается в те же типы через parse_record: файл мог испортить
кто угодно, и строка, которая типом не собирается, отвергается на границе
чтения с причиной, а не прячется.
"""

from dataclasses import dataclass, field, fields
from typing import ClassVar

KIND_KEY = 'kind'
RECORD_TURN_KEY = 'turn'
TYPES_KEY = 'types'
# Справочник тегов у поставщика не собрался: `types` у записи null, здесь текст ошибки.
TYPES_ERROR_KEY = 'types_error'
# Slug скилла у записи skill: он же slug модуля, чья папка скилла.
SKILL_KEY = 'skill'

KIND_WRITE = 'write'
KIND_READ = 'read'
KIND_ANCHOR = 'anchor'
KIND_SKILL = 'skill'
KIND_PROMPT = 'prompt'
KIND_ANSWER = 'answer'

# Ответ инструмента и ответ Влада кладутся как есть: строка, объект, список или null.
JSON_TYPES = (str, dict, list)
# Поле с этой пометкой в строке объёма отсутствует, пока оно None.
ABSENT_WHEN_NONE = 'absent_when_none'


def _absent_when_none():
    return field(default=None, metadata={ABSENT_WHEN_NONE: True})


def _is_turn(value) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _is_json(value) -> bool:
    return value is None or isinstance(value, JSON_TYPES)


def _is_texts(value) -> bool:
    return isinstance(value, tuple) and all(isinstance(item, str) for item in value)


def _is_text(value) -> bool:
    return isinstance(value, str)


def _is_optional_text(value) -> bool:
    return value is None or isinstance(value, str)


def _is_optional_texts(value) -> bool:
    return value is None or _is_texts(value)


TEXT = (_is_text, 'строка')
JSON = (_is_json, 'строка, объект, список или null')


def _as_tuple(value):
    """Список из события — в кортеж; иное как есть, его отвергнет проверка поля."""
    return tuple(value) if isinstance(value, list) else value


@dataclass(frozen=True, kw_only=True)
class _Record:
    """Общее у всех видов: вид и номер хода. Проверка полей — в CHECKS вида."""

    kind: ClassVar[str]
    # (поле, проверка, что ждали) — по ним __post_init__ отвергает запись.
    CHECKS: ClassVar[tuple] = ()
    turn: int | None = _absent_when_none()

    def __post_init__(self) -> None:
        for name, is_valid, expected in ((RECORD_TURN_KEY, _is_turn, 'целое число или null'),
                                         *self.CHECKS):
            value = getattr(self, name)
            if not is_valid(value):
                raise ValueError(f'запись {KIND_KEY} {self.kind}: поле «{name}» не {expected}: '
                                 f'{value!r}')


@dataclass(frozen=True, kw_only=True)
class WriteRecord(_Record):
    """Запись в Craft. `types` None — справочник тегов не собрался, его ошибка в `types_error`."""

    kind: ClassVar[str] = KIND_WRITE
    CHECKS: ClassVar[tuple] = (('tool', *TEXT), ('command', *TEXT), ('result', *JSON),
                               (TYPES_KEY, _is_optional_texts, 'кортеж строк или null'),
                               (TYPES_ERROR_KEY, _is_optional_text, 'строка или null'))
    tool: str
    command: str
    result: str | dict | list | None
    types: tuple[str, ...] | None
    types_error: str | None = _absent_when_none()

    def __post_init__(self) -> None:
        super().__post_init__()
        # Теги неизвестны ровно тогда, когда есть ошибка справочника: иначе
        # критерии по тегам молча не выбрали бы запись.
        if (self.types is None) != (self.types_error is not None):
            raise ValueError(f'запись {KIND_KEY} {self.kind}: «{TYPES_KEY}» null бывает только '
                             f'с «{TYPES_ERROR_KEY}», а пришло {self.types!r} и '
                             f'{self.types_error!r}')


@dataclass(frozen=True, kw_only=True)
class ReadRecord(_Record):
    """Чтение Craft: команда и id прочитанных блоков."""

    kind: ClassVar[str] = KIND_READ
    CHECKS: ClassVar[tuple] = (('command', *TEXT), ('ids', _is_texts, 'кортеж строк'))
    command: str
    ids: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class SkillRecord(_Record):
    """Вызов скилла: его slug."""

    kind: ClassVar[str] = KIND_SKILL
    CHECKS: ClassVar[tuple] = (('skill', *TEXT),)
    skill: str


@dataclass(frozen=True, kw_only=True)
class PromptRecord(_Record):
    """Реплика Влада: текст, автор и время."""

    kind: ClassVar[str] = KIND_PROMPT
    CHECKS: ClassVar[tuple] = (('text', *TEXT), ('author', *TEXT), ('at', *TEXT))
    text: str
    author: str
    at: str


@dataclass(frozen=True, kw_only=True)
class AnswerRecord(_Record):
    """Ответ Влада кнопками: текст вопросов, ответ, автор и время."""

    kind: ClassVar[str] = KIND_ANSWER
    CHECKS: ClassVar[tuple] = (('question', *TEXT), ('answer', *JSON), ('author', *TEXT),
                               ('at', *TEXT))
    question: str
    answer: str | dict | list | None
    author: str
    at: str


@dataclass(frozen=True, kw_only=True)
class AnchorRecord(_Record):
    """Выбор якоря: ответ кнопкой или реплика. Вопрос есть только у выбора кнопкой."""

    kind: ClassVar[str] = KIND_ANCHOR
    CHECKS: ClassVar[tuple] = (('answer', *JSON),
                               ('question', _is_optional_text, 'строка или null'))
    answer: str | dict | list | None
    question: str | None = _absent_when_none()


VolumeRecord = WriteRecord | ReadRecord | SkillRecord | PromptRecord | AnswerRecord | AnchorRecord
RECORD_TYPES = (WriteRecord, ReadRecord, SkillRecord, PromptRecord, AnswerRecord, AnchorRecord)
KINDS = frozenset(record_type.kind for record_type in RECORD_TYPES)


def make_write_record(*, tool: str, command: str, result, types,
                      types_error: str | None = None) -> WriteRecord:
    return WriteRecord(tool=tool, command=command, result=result, types=_as_tuple(types),
                       types_error=types_error)


def make_read_record(*, command: str, ids) -> ReadRecord:
    return ReadRecord(command=command, ids=_as_tuple(ids))


def make_skill_record(*, skill: str) -> SkillRecord:
    return SkillRecord(skill=skill)


def make_prompt_record(*, text: str, author: str, at: str) -> PromptRecord:
    return PromptRecord(text=text, author=author, at=at)


def make_answer_record(*, question: str, answer, author: str, at: str) -> AnswerRecord:
    return AnswerRecord(question=question, answer=answer, author=author, at=at)


def make_anchor_record(*, answer, question: str | None = None) -> AnchorRecord:
    return AnchorRecord(answer=answer, question=question)


RECORD_OF_KIND = {record_type.kind: record_type for record_type in RECORD_TYPES}
# Поля-кортежи: в строке объёма они списки. Ответы JSON остаются как есть.
TUPLE_FIELDS = frozenset({TYPES_KEY, 'ids'})


def parse_record(row) -> VolumeRecord:
    """Запись из строки объёма: обратное record_row. Строка не та — ValueError с причиной.

    Вид по `kind`, поля — как у типа: лишнее или недостающее поле отвергается,
    списки становятся кортежами, а значения проверяет сам тип.
    """
    if not isinstance(row, dict):
        # ValueError, а не TypeError: порча строки, как и в остальных ветках.
        raise ValueError('не объект')  # noqa: TRY004
    record_type = RECORD_OF_KIND.get(row.get(KIND_KEY))
    if record_type is None:
        raise ValueError(f'{KIND_KEY} {row.get(KIND_KEY)!r} не из {", ".join(sorted(KINDS))}')
    values = {name: value for name, value in row.items() if name != KIND_KEY}
    extra = sorted(set(values) - {item.name for item in fields(record_type)})
    if extra:
        raise ValueError(f'запись {KIND_KEY} {record_type.kind}: лишние поля {", ".join(extra)}')
    values = {name: _as_tuple(value) if name in TUPLE_FIELDS else value
              for name, value in values.items()}
    try:
        return record_type(**values)
    except TypeError as error:
        # Недостающее поле dataclass называет TypeError: на границе это та же порча строки.
        raise ValueError(f'запись {KIND_KEY} {record_type.kind}: {error}') from error


def check_record(record) -> None:
    """Запись объёма — одна из размеченных по kind. Иное — ValueError на границе поставщика."""
    if not isinstance(record, RECORD_TYPES):
        # ValueError, а не TypeError: так названо правило границы поставщика.
        raise ValueError(f'в объём ложится запись с {KIND_KEY} из '  # noqa: TRY004
                         f'{", ".join(sorted(KINDS))}, собранная фабрикой lib, а пришло {record!r}')


def record_row(record: VolumeRecord) -> dict:
    """Строка объёма из записи: `kind` первым, кортежи списками, пустые поля-пометки опущены."""
    check_record(record)
    row: dict = {KIND_KEY: record.kind}
    for item in fields(record):
        value = getattr(record, item.name)
        if value is None and item.metadata.get(ABSENT_WHEN_NONE):
            continue
        row[item.name] = list(value) if isinstance(value, tuple) else value
    return row
