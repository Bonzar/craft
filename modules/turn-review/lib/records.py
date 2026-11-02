"""Записи объёма ревью: тип на каждый вид — схема строк из контракта поставщиков.

Поставщики пишут строки объёма сами, простыми словарями по контракту из
README модуля, и эту lib не импортируют. Здесь схема строки на каждый вид:
размеченное объединение по `kind` — write, read, skill, prompt, answer,
anchor, entity. Объём с диска читается в эти типы через parse_record: файл пишут
чужие модули, и строка, которая типом не собирается, отвергается на границе
чтения с причиной, а не прячется.

Номера хода в строке нет. Поставщик кладёт id реплики харнеса `prompt_id`,
а номер хода по нему знает только ревью: см. turns.claim_prompt_turn.
"""

from dataclasses import dataclass, field, fields
from typing import ClassVar

KIND_KEY = 'kind'
PROMPT_ID_KEY = 'prompt_id'
# Slug скилла у записи skill: он же slug модуля, чья папка скилла.
SKILL_KEY = 'skill'

KIND_WRITE = 'write'
KIND_READ = 'read'
KIND_ANCHOR = 'anchor'
KIND_SKILL = 'skill'
KIND_PROMPT = 'prompt'
KIND_ANSWER = 'answer'
KIND_ENTITY = 'entity'
TAG_PREFIX = '#'

# Ответ инструмента и ответ Влада кладутся как есть: строка, объект, список или null.
JSON_TYPES = (str, dict, list)
# Поле с этой пометкой в строке объёма отсутствует, пока оно None.
ABSENT_WHEN_NONE = 'absent_when_none'


def _absent_when_none():
    return field(default=None, metadata={ABSENT_WHEN_NONE: True})


def _is_json(value) -> bool:
    return value is None or isinstance(value, JSON_TYPES)


def _is_texts(value) -> bool:
    return isinstance(value, tuple) and all(isinstance(item, str) for item in value)


def _is_tags(value) -> bool:
    """Теги типа в нижнем регистре: «#» и хотя бы один знак после."""
    return _is_texts(value) and all(
        item.startswith(TAG_PREFIX) and len(item) > len(TAG_PREFIX) and item == item.lower()
        for item in value)


def _is_bool(value) -> bool:
    return type(value) is bool


def _is_text(value) -> bool:
    return isinstance(value, str)


def _is_optional_text(value) -> bool:
    return value is None or isinstance(value, str)


TEXT = (_is_text, 'строка')
JSON = (_is_json, 'строка, объект, список или null')


def _as_tuple(value):
    """Список из строки объёма — в кортеж; иное как есть, его отвергнет проверка поля."""
    return tuple(value) if isinstance(value, list) else value


@dataclass(frozen=True, kw_only=True)
class _Record:
    """Общее у всех видов: вид и id реплики. Проверка полей — в CHECKS вида.

    `prompt_id` нет или null — харнес id реплики не дал: ход записи
    неизвестен, и запись идёт на ревью, а не отпускается как прошлая.
    """

    kind: ClassVar[str]
    # (поле, проверка, что ждали) — по ним __post_init__ отвергает запись.
    CHECKS: ClassVar[tuple] = ()
    prompt_id: str | None = _absent_when_none()

    def __post_init__(self) -> None:
        for name, is_valid, expected in ((PROMPT_ID_KEY, _is_optional_text, 'строка или null'),
                                         *self.CHECKS):
            value = getattr(self, name)
            if not is_valid(value):
                raise ValueError(f'запись {KIND_KEY} {self.kind}: поле «{name}» не {expected}: '
                                 f'{value!r}')


@dataclass(frozen=True, kw_only=True)
class WriteRecord(_Record):
    """Запись в Craft: команда как есть. Её тип — в строке `entity` замка."""

    kind: ClassVar[str] = KIND_WRITE
    CHECKS: ClassVar[tuple] = (('tool', *TEXT), ('command', *TEXT), ('result', *JSON))
    tool: str
    command: str
    result: str | dict | list | None


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
    CHECKS: ClassVar[tuple] = ((SKILL_KEY, *TEXT),)
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


@dataclass(frozen=True, kw_only=True)
class EntityRecord(_Record):
    """Тип записи от замка craft-type-lock: только выбор критериев, не довод.

    `command` — команда записи как есть: по ней и `prompt_id` строка находит
    свою запись `write`. `targets` — id целей записи. `place_types` — теги типа
    страниц, куда запись легла; `content_types` — теги типа в тексте записи и
    у правленой цели; пустой список — такой сущности нет. `determined` —
    место всех целей известно; `note` — что замок увидел или почему не смог.
    """

    kind: ClassVar[str] = KIND_ENTITY
    TAGS_EXPECTED: ClassVar[str] = 'кортеж тегов «#…» в нижнем регистре'
    CHECKS: ClassVar[tuple] = (('command', *TEXT), ('targets', _is_texts, 'кортеж строк'),
                               ('place_types', _is_tags, TAGS_EXPECTED),
                               ('content_types', _is_tags, TAGS_EXPECTED),
                               ('determined', _is_bool, 'true или false'), ('note', *TEXT))
    command: str
    targets: tuple[str, ...]
    place_types: tuple[str, ...]
    content_types: tuple[str, ...]
    determined: bool
    note: str


VolumeRecord = (WriteRecord | ReadRecord | SkillRecord | PromptRecord | AnswerRecord | AnchorRecord
                | EntityRecord)
RECORD_TYPES = (WriteRecord, ReadRecord, SkillRecord, PromptRecord, AnswerRecord, AnchorRecord,
                EntityRecord)
KINDS = frozenset(record_type.kind for record_type in RECORD_TYPES)
RECORD_OF_KIND = {record_type.kind: record_type for record_type in RECORD_TYPES}
# Поля-кортежи: в строке объёма они списки. Ответы JSON остаются как есть.
TUPLE_FIELDS = frozenset({'ids', 'targets', 'place_types', 'content_types'})


def parse_record(row) -> VolumeRecord:
    """Запись из строки объёма: обратное record_row. Строка не та — ValueError с причиной.

    Вид по `kind`, поля — как у типа: лишнее или недостающее поле отвергается,
    списки становятся кортежами, а значения проверяет сам тип.
    """
    if not isinstance(row, dict):
        # ValueError, а не TypeError: порча строки, как и в остальных ветках.
        raise ValueError('не объект')  # noqa: TRY004
    kind = row.get(KIND_KEY)
    # Список или объект в kind словарь не возьмёт ключом: проверка до поиска.
    record_type = RECORD_OF_KIND.get(kind) if isinstance(kind, str) else None
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
    """Запись объёма — одна из размеченных по kind. Иное — ValueError: на диск не идёт."""
    if not isinstance(record, RECORD_TYPES):
        # ValueError, а не TypeError: так названо правило границы объёма.
        raise ValueError(f'в объём ложится запись с {KIND_KEY} из '  # noqa: TRY004
                         f'{", ".join(sorted(KINDS))}, а пришло {record!r}')


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
