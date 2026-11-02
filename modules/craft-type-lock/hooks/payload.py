"""Что команда записи делает с местом: отступ новых блоков и смена состояния.

Часть замка craft-type-lock. Владелец места зависит от отступа, с которым
блок встанет на страницу (hooks/owner.py), а у правки блока с типом — от
того, меняет ли она его состояние: закрытие, отмена или возврат задевают и
владельца. Здесь разбирается полезная нагрузка команды: `--json` и
`--markdown` каждой команды пачки, флаг `--state`, позиция.
"""

import json
import re
import shlex
from collections.abc import Callable
from typing import NamedTuple

ADD_VERB = 'blocks add'
UPDATE_VERB = 'blocks update'
MOVE_VERB = 'blocks move'
DELETE_VERB = 'blocks delete'
# Глаголы, которые уносят блок с его места: владелец места тоже задет.
REMOVING_VERBS = (MOVE_VERB, DELETE_VERB)
JSON_FLAG = '--json'
MARKDOWN_FLAG = '--markdown'
FLAG_GLUE = '='
INDENT_KEY = 'indentationLevel'
MARKDOWN_KEY = 'markdown'
TASK_INFO_KEY = 'taskInfo'
STATE_KEY = 'state'
# `--markdown` нормализует отступ к 0, `--json` без поля отступа — тоже 0
# (правило craft-tool.md: update без indentationLevel сбрасывает его в 0).
DEFAULT_LEVEL = 0
STATE_FLAG = re.compile(r'--state(?:\s+|=)\S+')
POSITION_FLAG = re.compile(r'--position(?:\s+|=)(\S+)')
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
    не разобрать. at_end — место в конце детей родителя (позиция `end` или
    без позиции), before — перед соседом. explicit_state — флаг `--state` или `taskInfo.state` в
    полезной нагрузке. texts — новые тексты блоков из `blocks update`: их
    чекбокс и закрывающий хвост сверяются с текстом цели.
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


def flag_value(words: list[str], flag: str) -> str | None:
    for index, word in enumerate(words):
        if word == flag and index + 1 < len(words):
            return words[index + 1]
        if word.startswith(flag + FLAG_GLUE):
            return word[len(flag) + len(FLAG_GLUE):]
    return None


def payload_of(command: str) -> Payload:
    """Полезная нагрузка одной команды: блоки из `--json` и текст `--markdown`."""
    try:
        words = shlex.split(command)
    except ValueError:
        return Payload(None, None, True)
    markdown, raw = flag_value(words, MARKDOWN_FLAG), flag_value(words, JSON_FLAG)
    if raw is None:
        return Payload(None, markdown, False)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return Payload(None, markdown, True)
    blocks = data if isinstance(data, list) else [data]
    if not all(isinstance(block, dict) for block in blocks):
        return Payload(None, markdown, True)
    return Payload(blocks, markdown, False)


def levels_of(payload: Payload) -> set[int] | None:
    """Отступы верхних блоков нагрузки; не разобрать — None."""
    if payload.broken:
        return None
    if payload.blocks is None:
        return {DEFAULT_LEVEL} if payload.markdown is not None else None
    levels = {block.get(INDENT_KEY, DEFAULT_LEVEL) for block in payload.blocks}
    if not all(type(level) is int and level >= 0 for level in levels):
        return None
    return levels


def has_state(payload: Payload) -> bool:
    return any(isinstance(block.get(TASK_INFO_KEY), dict) and STATE_KEY in block[TASK_INFO_KEY]
               for block in payload.blocks or [])


def texts_of(payload: Payload) -> list[str]:
    texts = [block[MARKDOWN_KEY] for block in payload.blocks or []
             if isinstance(block.get(MARKDOWN_KEY), str)]
    return [*texts, payload.markdown] if payload.markdown is not None else texts


def write_of(command: str, split: Callable[[str], list[str]], verb_of: Callable[[str], str]) -> Write:
    """Разбор всей команды, пачка через «;» — по каждой команде."""
    commands = split(command) or [command]
    levels: set[int] | None = set()
    explicit, texts = STATE_FLAG.search(command) is not None, []
    for part in commands:
        verb, payload = verb_of(part), payload_of(part)
        if verb == ADD_VERB:
            found = levels_of(payload)
            levels = None if found is None or levels is None else levels | found
        if verb == UPDATE_VERB:
            explicit = explicit or has_state(payload)
            texts.extend(texts_of(payload))
    found = POSITION_FLAG.search(command)
    position = found.group(1).strip(QUOTES) if found else None
    return Write(verb_of(commands[0]), None if levels is None else frozenset(levels),
                 position in (None, END_POSITION), position == BEFORE_POSITION, explicit, tuple(texts))


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
