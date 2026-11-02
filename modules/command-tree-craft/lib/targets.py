"""Цели записи Craft: в какие блоки и рядом с какими ляжет команда.

Флаг цели ищется по всей команде, через пробел или через «=»: полезная
нагрузка после --json бывает и впереди флагов. Внутри JSON такие флаги не
встречаются. Роль цели зависит от флага и глагола команды: сосед, родитель
или сам блок.
"""

import enum
import re
from typing import NamedTuple

from .ids import BLOCK_ID, is_block_id, unique_ids

ANCHOR_FLAG = re.compile(r'--(id|ids|siblingId|targetId|targetSiblingId)(?:\s+|=)(\S+)')
QUOTES = '\'"'
LIST_SEPARATOR = ','
POSITION_FLAG = re.compile(r'--position(?:\s+|=)(\S+)')
POSITION_BEFORE = 'before'
# Команды, у которых цель есть всегда.
TARGETED_COMMANDS = re.compile(r'^\s*(blocks\s+(add|update|move|delete)|tasks\s+update)\b')
# Флаг родителя, в чьих детях окажется запись: у add это --id, у move --targetId.
CONTAINER_FLAG = {'blocks add': 'id', 'blocks move': 'targetId'}
SIBLING_FLAGS = ('siblingId', 'targetSiblingId')
# Id блока в JSON-полезной нагрузке: `blocks update --json '{"id": …}'`.
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

    anchors — разобранные цели по флагам; broken — значения флагов цели, на
    id не похожие, описанием `--флаг 'значение'`; before — `--position
    before`; targeted — команда из тех, у которых цель есть всегда.
    """
    anchors: list[Anchor]
    broken: list[str]
    before: bool
    targeted: bool


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


def is_before(command: str) -> bool:
    """Стоит ли `--position before`: запись встаёт перед целью."""
    position = POSITION_FLAG.search(command)
    return position is not None and position.group(1).strip(QUOTES) == POSITION_BEFORE


def write_targets(command: str) -> Targets:
    """Цели одной команды записи: флаги цели, позиция, есть ли цель всегда."""
    verb = verb_of(command)
    anchors, broken = [], []
    for flag, raw in ANCHOR_FLAG.findall(command):
        for value in raw.strip(QUOTES).split(LIST_SEPARATOR):
            value = value.strip(QUOTES)
            if is_block_id(value):
                anchors.append(Anchor(flag, value, role_of(flag, verb)))
            else:
                broken.append(f'--{flag} {value[:BROKEN_PREVIEW]!r}')
    return Targets(anchors, broken, is_before(command),
                   TARGETED_COMMANDS.match(command) is not None)


def target_ids(command: str) -> list[str]:
    """Id всех целей команды: из флагов цели и из полей `"id"` JSON, без повторов."""
    flagged = [anchor.block_id for anchor in write_targets(command).anchors]
    return unique_ids([*flagged, *JSON_ID.findall(command)])
