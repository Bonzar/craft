"""Место записи в Craft: под тогглом «Решения» или нет.

Часть замка craft-type-lock. Родителя блока connect-API не отдаёт, поэтому
цель записи ищется на страницах, которые агент читал в сессии: каждая
запрашивается заново на глубину API_DEPTH, и среди её блоков ищутся id из
команды записи. Точка входа замка — hooks/module.py, здесь только суждение.
"""

import enum
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple

BASE_ENV = 'CRAFT_API_BASE'
# Без User-Agent connect-API отвечает 403 (замер 01.11.2026 из контейнера).
USER_AGENT = 'jarvis-craft-type-lock'
API_TIMEOUT_SEC = 10
# Потолок на всю проверку места: прочитанных страниц в списке до двадцати, а
# хук не должен держать запись минутами, когда API медленный.
DEADLINE_SEC = 12
# Решения этапа лежат внутри блока этапа, а этап агент читает со страницы
# проекта на глубину 2. Глубже замок на одном чтении не видит: решение на
# вложенной странице находится, когда прочитана сама эта страница.
API_DEPTH = 2
# Регистр id API различает (замер 01.11.2026: у документа с id в верхнем
# регистре нижний отвечает 404, и наоборот). Id хранятся и запрашиваются как
# есть, сравниваются без учёта регистра.
BLOCK_ID = re.compile(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}')
# Флаг цели в любом месте команды, через пробел или через «=».
ANCHOR_FLAG = re.compile(r'--(id|ids|siblingId|targetId|targetSiblingId)(?:\s+|=)(\S+)')
QUOTES = '\'"'
POSITION_FLAG = re.compile(r'--position(?:\s+|=)(\S+)')
POSITION_BEFORE = 'before'
# Команды, у которых цель есть всегда: нет распознанной цели — замок не судит.
TARGETED_COMMANDS = re.compile(r'^\s*(blocks\s+(add|update|move|delete)|tasks\s+update)\b')
# Флаг родителя, в чьих детях окажется запись: у add это --id, у move --targetId.
CONTAINER_FLAG = {'blocks add': 'id', 'blocks move': 'targetId'}
SIBLING_FLAGS = ('siblingId', 'targetSiblingId')
CONTENT_KEY = 'content'
LEVEL_KEY = 'indentationLevel'
LIST_STYLE_KEY = 'listStyle'
TOGGLE_STYLE = 'toggle'
MARKDOWN_KEY = 'markdown'
DECISION_TOGGLE = re.compile(r'^[\s+#]*Решения(\s+(проекта|сферы))?\s*$')


class Verdict(enum.Enum):
    """Пять исходов проверки места.

    DECISION и OTHER — суждение о месте. UNREAD — тоже суждение: цели нет ни
    на одной прочитанной странице, запись в непрочитанное. Два последних —
    замок судить не может, и это не вина хода. UNAVAILABLE — окружение: нет
    базы API, API не ответил, не уложились в срок; предупреждение в ход
    выводится раз за сессию, разовый отказ это или постоянный.
    UNPARSED — в одной команде не разобран ни один id цели или часть,
    предупреждать не о чем.
    """
    DECISION = 'decision'
    OTHER = 'other'
    UNREAD = 'unread'
    UNAVAILABLE = 'unavailable'
    UNPARSED = 'unparsed'


class Place(NamedTuple):
    verdict: Verdict
    reason: str


def same_id(left: str, right: str) -> bool:
    """Один ли это блок: регистр хранится, но не различается."""
    return left.lower() == right.lower()


class Anchor(NamedTuple):
    flag: str
    block_id: str


def is_decision_toggle(block: dict) -> bool:
    markdown = block.get(MARKDOWN_KEY) if isinstance(block, dict) else None
    return (isinstance(block, dict) and block.get(LIST_STYLE_KEY) == TOGGLE_STYLE
            and isinstance(markdown, str) and DECISION_TOGGLE.match(markdown) is not None)


def level_of(block: dict) -> int:
    level = block.get(LEVEL_KEY) if isinstance(block, dict) else None
    return level if isinstance(level, int) else 0


def under_decisions(siblings: list, index: int) -> bool:
    """Лежит ли блок siblings[index] под тогглом «Решения».

    Решения стоят соседями тоггла на +1 от него. Идём назад до первого блока
    без отступа: это сам тоггл, если блок под ним, иначе заголовок другой
    секции или разделитель. Индекс -1 — перед первым блоком, там «Решений»
    не бывает.
    """
    for position in range(index, -1, -1):
        block = siblings[position]
        if is_decision_toggle(block):
            return True
        if level_of(block) == 0:
            return False
    return False


def find_block(tree: dict, target: str) -> tuple[list, int] | None:
    """Соседи и индекс блока target внутри дерева страницы; нет — None."""
    children = tree.get(CONTENT_KEY) if isinstance(tree, dict) else None
    if not isinstance(children, list):
        return None
    for index, child in enumerate(children):
        if isinstance(child, dict) and same_id(str(child.get('id', '')), target):
            return children, index
    for child in children:
        found = find_block(child, target)
        if found is not None:
            return found
    return None


def anchors_of(command: str) -> tuple[list[Anchor], list[str]]:
    """Цели записи из команды и значения флагов, на id не похожие.

    Флаг ищется по всей команде: полезная нагрузка после --json бывает и
    впереди флагов. Внутри JSON такие флаги не встречаются.
    """
    anchors, broken = [], []
    for flag, raw in ANCHOR_FLAG.findall(command):
        for value in raw.strip(QUOTES).split(','):
            value = value.strip(QUOTES)
            if BLOCK_ID.fullmatch(value):
                anchors.append(Anchor(flag, value))
            else:
                broken.append(f'--{flag} {value[:24]!r}')
    return anchors, broken


def verb_of(command: str) -> str:
    words = command.split()
    return ' '.join(words[:2]) if len(words) >= 2 else ''


def landing(command: str, tree: dict, anchor: Anchor) -> tuple[list, int] | None:
    """Соседи и индекс блока, рядом с которым окажется запись; нет — None.

    Сосед по --siblingId и --targetSiblingId: запись встаёт после него, при
    --position before перед ним, то есть рядом с предыдущим блоком, у первого
    блока это индекс -1. Родитель по --id у `blocks add` и по --targetId у
    `blocks move` — запись встаёт в конец его детей, при before в начало,
    то есть перед первым ребёнком, индекс -1. Любой другой флаг — сам блок:
    правка, перенос, удаление.
    """
    if same_id(str(tree.get('id', '')), anchor.block_id):
        siblings, index = [tree], 0
    else:
        found = find_block(tree, anchor.block_id)
        if found is None:
            return None
        siblings, index = found
    position = POSITION_FLAG.search(command)
    before = position is not None and position.group(1).strip(QUOTES) == POSITION_BEFORE
    if anchor.flag in SIBLING_FLAGS:
        return (siblings, index - 1) if before else (siblings, index)
    if anchor.flag == CONTAINER_FLAG.get(verb_of(command)):
        children = siblings[index].get(CONTENT_KEY)
        if isinstance(children, list) and children:
            return children, -1 if before else len(children) - 1
    return siblings, index


def other_case(block_id: str) -> str:
    """Тот же id в другом регистре: верхний для нижнего и наоборот."""
    return block_id.lower() if block_id != block_id.lower() else block_id.upper()


def fetch_tree(base: str, page_id: str, timeout: float) -> dict:
    """Страница на глубину API_DEPTH. 404 повторяется в другом регистре id:
    агент мог набрать id не в том регистре, в каком его знает API."""
    try:
        tree = fetch_once(base, page_id, timeout)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        tree = fetch_once(base, other_case(page_id), timeout)
    if isinstance(tree, list):
        tree = tree[0] if tree else None
    if not isinstance(tree, dict) or not tree.get('id'):
        raise ValueError('ответ API не блок')
    return tree


def fetch_once(base: str, page_id: str, timeout: float):
    query = urllib.parse.urlencode({'id': page_id, 'maxDepth': API_DEPTH})
    request = urllib.request.Request(
        f'{base.rstrip("/")}/blocks?{query}',
        headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def short(block_id: str) -> str:
    return block_id[:8]


def judge(command: str, tree: dict, pending: list[Anchor]) -> tuple[bool, list[Anchor]]:
    """Одна страница: нашлось ли решение и какие цели на ней ещё не найдены."""
    left = []
    for anchor in pending:
        place = landing(command, tree, anchor)
        if place is None:
            left.append(anchor)
        elif under_decisions(*place):
            return True, left
    return False, left


def decision_zone(command: str, reads: list[str], base: str | None, clock=time.monotonic) -> Place:
    """Ложится ли запись под «Решения».

    Целей в команде бывает несколько, и решение — любая из них под
    «Решениями»: страницы идут свежие первыми, пока все цели не найдены.
    Цель, которой нет ни на одной прочитанной странице, — UNREAD: по
    механике Craft страница цели читается до записи. UNAVAILABLE — нет
    CRAFT_API_BASE, API не ответил, проверка не уложилась в DEADLINE_SEC.
    UNPARSED — у команды с целью не разобран ни один id или часть id.
    """
    anchors, broken = anchors_of(command)
    if not anchors:
        if broken or TARGETED_COMMANDS.match(command):
            return Place(Verdict.UNPARSED, 'цель в команде не разобрана: ' + (', '.join(broken) or 'флага цели нет'))
        return Place(Verdict.OTHER, '')
    if not base:
        return Place(Verdict.UNAVAILABLE, f'{BASE_ENV} не задан, место записи не проверить')
    if not reads:
        return Place(Verdict.UNREAD, 'в сессии нет чтений Craft')
    deadline, failures, pending = clock() + DEADLINE_SEC, [], list(anchors)
    for page_id in reads:
        left = deadline - clock()
        if left <= 0:
            return Place(Verdict.UNAVAILABLE, f'не уложился в {DEADLINE_SEC} с')
        try:
            tree = fetch_tree(base, page_id, min(API_TIMEOUT_SEC, left))
        except urllib.error.HTTPError as error:
            # 404 в обоих регистрах: страницы нет или она вне шаринга
            # connect-ссылки. Это не сеть, и оператору важно это различать.
            missing = error.code == 404
            failures.append(f'{short(page_id)}: ' + ('не найдена в API в обоих регистрах' if missing else str(error)))
            continue
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
            failures.append(f'{short(page_id)}: {error}')
            continue
        found, pending = judge(command, tree, pending)
        if found:
            return Place(Verdict.DECISION, f'цель под тогглом «Решения» на странице {short(page_id)}')
        if not pending:
            break
    if pending and failures:
        return Place(Verdict.UNAVAILABLE, 'API Craft не ответил: ' + '; '.join(failures))
    if pending:
        names = ', '.join(short(anchor.block_id) for anchor in pending)
        return Place(Verdict.UNREAD, f'цель {names} не найдена среди страниц, прочитанных в сессии')
    if broken:
        return Place(Verdict.UNPARSED, 'часть целей не разобрана: ' + ', '.join(broken))
    names = ', '.join(short(anchor.block_id) for anchor in anchors)
    return Place(Verdict.OTHER, f'цель {names} не под «Решениями»')
