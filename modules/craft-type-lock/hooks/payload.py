"""Что команда записи делает с местом: отступ новых блоков и смена состояния.

Часть замка craft-type-lock. Владелец места зависит от отступа, с которым
блок встанет на страницу (hooks/owner.py), а у правки блока с типом — от
того, меняет ли она его состояние: закрытие, отмена или возврат задевают и
владельца. Здесь разбирается одна команда: `--json` и `--markdown`, флаг
`--state`, позиция. Флаги и их значения даёт разбор слов command-tree-craft
(`parse_command`): текст нагрузки флагом не становится. Пачку режет
вызывающий и судит каждую команду отдельно.
"""

import json
import re
from typing import NamedTuple

ADD_VERB = 'blocks add'
UPDATE_VERB = 'blocks update'
MOVE_VERB = 'blocks move'
# Глаголы, которые уносят блок с его места: владелец места тоже задет.
REMOVING_VERBS = (MOVE_VERB, 'blocks delete', 'tasks delete', 'documents delete')
JSON_FLAG = 'json'
MARKDOWN_FLAG = 'markdown'
STATE_FLAG = 'state'
POSITION_FLAG = 'position'
INDENT_KEY = 'indentationLevel'
MARKDOWN_KEY = 'markdown'
TASK_INFO_KEY = 'taskInfo'
STATE_KEY = 'state'
# `--markdown` нормализует отступ к 0, `--json` без поля отступа — тоже 0
# (правило craft-tool.md: update без indentationLevel сбрасывает его в 0).
DEFAULT_LEVEL = 0
QUOTES = '\'"'
# «В конец» ставит место после всех детей родителя, «перед» — перед соседом.
END_POSITION = 'end'
BEFORE_POSITION = 'before'
CHECKBOX = re.compile(r'^\s*(?:[-*+]\s+)?\[([ xX])\]')
# Хвосты тега, которые закрывают сущность: появились или ушли — смена состояния.
CLOSING_TAILS = ('готово', 'отменено')
CLOSING_TAIL = re.compile(r'#[^\s#/]+/(' + '|'.join(CLOSING_TAILS) + r')(?![^\W_])')


class Write(NamedTuple):
    """Что команда делает: глагол, отступы новых блоков, смена состояния.

    levels — отступы, с которыми встанут новые блоки `blocks add`; None —
    не разобрать или команда не `blocks add`. at_end — место в конце детей
    родителя (позиция `end` или без позиции), before — перед соседом.
    explicit_state — флаг `--state` или `taskInfo.state` в полезной
    нагрузке. texts — новые тексты блоков из `blocks update`: их чекбокс и
    закрывающий хвост сверяются с текстом цели.
    """
    verb: str
    levels: frozenset[int] | None
    at_end: bool
    before: bool
    explicit_state: bool
    texts: tuple[str, ...]

    @property
    def removes(self) -> bool:
        return self.verb in REMOVING_VERBS

    @property
    def moves(self) -> bool:
        return self.verb == MOVE_VERB


class Payload(NamedTuple):
    blocks: list[dict] | None
    markdown: str | None
    broken: bool


def payload_of(parsed) -> Payload:
    """Полезная нагрузка одной команды: блоки из `--json` и текст `--markdown`."""
    markdown, raw = parsed.value(MARKDOWN_FLAG), parsed.value(JSON_FLAG)
    if raw is None:
        return Payload(None, markdown, parsed.has(JSON_FLAG))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return Payload(None, markdown, True)
    blocks = data if isinstance(data, list) else [data]
    if not all(isinstance(block, dict) for block in blocks):
        return Payload(None, markdown, True)
    return Payload(blocks, markdown, False)


def levels_of(payload: Payload) -> set[int] | None:
    """Отступы верхних блоков нагрузки; не разобрать или блоков нет — None."""
    if payload.broken:
        return None
    if payload.blocks is None:
        return {DEFAULT_LEVEL} if payload.markdown is not None else None
    levels = {block.get(INDENT_KEY, DEFAULT_LEVEL) for block in payload.blocks}
    if not levels or not all(type(level) is int and level >= 0 for level in levels):
        return None
    return levels


def has_state(payload: Payload) -> bool:
    return any(isinstance(block.get(TASK_INFO_KEY), dict) and STATE_KEY in block[TASK_INFO_KEY]
               for block in payload.blocks or [])


def texts_of(payload: Payload) -> list[str]:
    texts = [block[MARKDOWN_KEY] for block in payload.blocks or []
             if isinstance(block.get(MARKDOWN_KEY), str)]
    return [*texts, payload.markdown] if payload.markdown is not None else texts


def write_of(verb: str, parsed) -> Write:
    """Разбор одной команды: глагол и её слова от command-tree-craft (`parse_command`)."""
    payload = payload_of(parsed)
    levels = levels_of(payload) if verb == ADD_VERB else None
    updates = verb == UPDATE_VERB
    explicit = parsed.has(STATE_FLAG) or (updates and has_state(payload))
    position = parsed.value(POSITION_FLAG)
    position = position.strip(QUOTES) if position is not None else None
    return Write(verb, None if levels is None else frozenset(levels),
                 position in (None, END_POSITION), position == BEFORE_POSITION, explicit,
                 tuple(texts_of(payload)) if updates else ())


def mark_of(markdown: str) -> tuple[str | None, frozenset[str]]:
    """Состояние в тексте: знак чекбокса и закрывающие хвосты тегов."""
    box = CHECKBOX.match(markdown)
    return (box.group(1).lower() if box else None,
            frozenset(match.lower() for match in CLOSING_TAIL.findall(markdown)))


def changes_state(write: Write, markdown) -> bool:
    """Меняет ли команда состояние блока с текстом markdown."""
    if write.explicit_state:
        return True
    current = mark_of(markdown if isinstance(markdown, str) else '')
    return any(mark_of(text) != current for text in write.texts)
