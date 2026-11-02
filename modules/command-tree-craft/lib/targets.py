"""Цели записи Craft: в какие блоки и рядом с какими ляжет команда.

Флаги цели берутся из слов команды (flags.py), через пробел или через «=»:
полезная нагрузка после --json бывает и впереди флагов, а текст нагрузки
флагом не становится. Роль цели зависит от флага и глагола команды: сосед,
родитель или сам блок. Команда судится одна: пачку режет split_commands.
"""

import enum
import json
import re
from typing import NamedTuple

from .flags import Parsed, parse_command
from .ids import BLOCK_ID, is_block_id, unique_ids

# Флаги, значение которых — id цели. `--task` — сама задача у `tasks
# update/delete`, `--document` — сам документ у `documents move/delete`.
ANCHOR_FLAGS = ('id', 'ids', 'siblingId', 'targetId', 'targetSiblingId', 'task', 'document')
QUOTES = '\'"'
LIST_SEPARATOR = ','
POSITION_FLAG = 'position'
POSITION_BEFORE = 'before'
# Флаг справки: команда со справкой ничего не пишет.
HELP_FLAG = 'help'
# Заметка дня по дате: `blocks add --date today` — цель есть, но не id блока.
DATE_FLAG = 'date'
# Команды, у которых цель есть всегда.
TARGETED_VERBS = ('blocks add', 'blocks update', 'blocks move', 'blocks delete',
                  'tasks update', 'tasks delete', 'documents move', 'documents delete')
# Флаг родителя, в чьих детях окажется запись: у add это --id, у move --targetId.
CONTAINER_FLAG = {'blocks add': 'id', 'blocks move': 'targetId'}
SIBLING_FLAGS = ('siblingId', 'targetSiblingId')
# Цель правки бывает только в нагрузке: `blocks update --json '{"id": …}'`.
JSON_FLAG = 'json'
JSON_TARGET_VERBS = ('blocks update',)
JSON_ID_KEY = 'id'
# Id блока в JSON-полезной нагрузке где угодно: для target_ids.
JSON_ID = re.compile(rf'"id"\s*:\s*"({BLOCK_ID.pattern})"')
# Длина значения флага в описании неразобранной цели.
BROKEN_PREVIEW = 24
VERB_WORDS = 2


class Role(enum.Enum):
    """Чем цель приходится записи."""
    SIBLING = 'sibling'      # запись встаёт рядом с ней
    CONTAINER = 'container'  # запись встаёт в её дети
    SELF = 'self'            # правка, перенос или удаление самого блока


class Anchor(NamedTuple):
    flag: str
    block_id: str
    role: Role

    @property
    def is_sibling(self) -> bool:
        return self.role is Role.SIBLING

    @property
    def is_container(self) -> bool:
        return self.role is Role.CONTAINER


class Targets(NamedTuple):
    """Цели одной команды записи.

    anchors — разобранные цели по флагам, а без них — id из JSON правки;
    broken — значения флагов цели, на id не похожие, описанием `--флаг
    'значение'`; before — `--position before`; targeted — команда из тех, у
    которых цель есть всегда; dated — цель — заметка дня по `--date`.
    """
    anchors: list[Anchor]
    broken: list[str]
    before: bool
    targeted: bool
    dated: bool = False


def verb_of(command: str) -> str:
    """Глагол команды — первые два слова: `blocks add`, `tasks update`."""
    words = command.split()
    return ' '.join(words[:VERB_WORDS]) if len(words) >= VERB_WORDS else ''


def role_of(flag: str, verb: str) -> Role:
    if flag in SIBLING_FLAGS:
        return Role.SIBLING
    if flag == CONTAINER_FLAG.get(verb):
        return Role.CONTAINER
    return Role.SELF


def before_of(parsed: Parsed) -> bool:
    position = parsed.value(POSITION_FLAG)
    return position is not None and position.strip(QUOTES) == POSITION_BEFORE


def is_before(command: str) -> bool:
    """Стоит ли `--position before` словом команды: запись встаёт перед целью."""
    return before_of(parse_command(command))


def flag_anchors(parsed: Parsed, verb: str) -> tuple[list[Anchor], list[str]]:
    anchors, broken = [], []
    for flag, raw in parsed.flags:
        if flag not in ANCHOR_FLAGS:
            continue
        for value in (raw or '').strip(QUOTES).split(LIST_SEPARATOR):
            value = value.strip().strip(QUOTES)
            if is_block_id(value):
                anchors.append(Anchor(flag, value, role_of(flag, verb)))
            else:
                broken.append(f'--{flag} {value[:BROKEN_PREVIEW]!r}')
    return anchors, broken


def json_anchors(parsed: Parsed) -> list[Anchor]:
    """Id верхних блоков нагрузки `--json`: цель правки без флага. Не JSON — пусто."""
    raw = parsed.value(JSON_FLAG)
    try:
        data = json.loads(raw) if raw is not None else None
    except json.JSONDecodeError:
        return []
    blocks = data if isinstance(data, list) else [data]
    ids = [block.get(JSON_ID_KEY) for block in blocks if isinstance(block, dict)]
    return [Anchor(JSON_FLAG, block_id, Role.SELF) for block_id in unique_ids(
        item for item in ids if is_block_id(item))]


def write_targets(command: str) -> Targets:
    """Цели одной команды записи: флаги цели, позиция, есть ли цель всегда."""
    verb, parsed = verb_of(command), parse_command(command)
    if parsed.has(HELP_FLAG):
        return Targets([], [], False, False)
    anchors, broken = flag_anchors(parsed, verb)
    if not anchors and not broken and verb in JSON_TARGET_VERBS:
        anchors = json_anchors(parsed)
    return Targets(anchors, broken, before_of(parsed), verb in TARGETED_VERBS, parsed.has(DATE_FLAG))


def target_ids(command: str) -> list[str]:
    """Id всех целей команды: из флагов цели и из полей `"id"` JSON, без повторов."""
    flagged = [anchor.block_id for anchor in write_targets(command).anchors]
    return unique_ids([*flagged, *JSON_ID.findall(command)])
