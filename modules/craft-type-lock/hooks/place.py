"""Место записи в Craft: под тогглом «Решения» или нет.

Часть замка craft-type-lock. Родителя блока connect-API не отдаёт, поэтому
цель записи ищется на страницах, которые агент читал в сессии: каждая
запрашивается заново на глубину API_DEPTH, и среди её блоков ищутся id из
команды записи. Точка входа замка — hooks/module.py, здесь только суждение.
"""

import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import namedtuple

BASE_ENV = 'CRAFT_API_BASE'
# Без User-Agent connect-API отвечает 403 (замер 01.11.2026 из контейнера).
USER_AGENT = 'jarvis-craft-type-lock'
API_TIMEOUT_SEC = 10
# Потолок на всю проверку места: страниц в списке до MAX_READS, а хук не
# должен держать запись минутами, когда API медленный.
DEADLINE_SEC = 12
# Решения этапа лежат внутри блока этапа, а этап агент читает со страницы
# проекта на глубину 2. Глубже решение не ищем: ниже этапа решений нет.
API_DEPTH = 2
BLOCK_ID = re.compile(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}')
ANCHOR_FLAG = re.compile(r'--(id|ids|siblingId|targetId|targetSiblingId)\s+(\S+)')
# Голова команды — до полезной нагрузки: внутри JSON флаги не ищем.
PAYLOAD_FLAG = re.compile(r'\s--(?:json|markdown)\b')
QUOTES = '\'"'
POSITION_FLAG = re.compile(r'--position\s+(\S+)')
POSITION_BEFORE = 'before'
ADD_COMMAND = 'blocks add'
CONTENT_KEY = 'content'
LEVEL_KEY = 'indentationLevel'
LIST_STYLE_KEY = 'listStyle'
TOGGLE_STYLE = 'toggle'
MARKDOWN_KEY = 'markdown'
DECISION_TOGGLE = re.compile(r'^[\s+#]*Решения(\s+(проекта|сферы))?\s*$')



# --- место записи: под тогглом «Решения» или нет ---------------------------

# Три исхода проверки места. Два первых — суждение, третий — замок судить не
# может, и это не «не решение»: смешивать их нельзя.
PLACE_DECISION = 'decision'
PLACE_OTHER = 'other'
PLACE_UNKNOWN = 'unknown'
# Цели нет ни на одной прочитанной странице: запись в непрочитанное.
PLACE_UNREAD = 'unread'
Place = namedtuple('Place', 'verdict reason')


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
    секции или разделитель.
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
        if isinstance(child, dict) and str(child.get('id', '')).lower() == target:
            return children, index
    for child in children:
        found = find_block(child, target)
        if found is not None:
            return found
    return None


def anchors_of(command: str) -> tuple[list[tuple[str, str]], str | None]:
    """Пары (флаг, id цели) из головы команды, до полезной нагрузки.

    Флаг с чем-то, что на id не похоже, — ошибка разбора: второй элемент
    называет его, и место такой записи замок не судит.
    """
    head = PAYLOAD_FLAG.split(command, 1)[0]
    anchors, broken = [], None
    for flag, raw in ANCHOR_FLAG.findall(head):
        for value in raw.strip(QUOTES).split(','):
            value = value.strip(QUOTES).lower()
            if BLOCK_ID.fullmatch(value):
                anchors.append((flag, value))
            else:
                broken = f'--{flag} {value[:24]!r}'
    return anchors, broken


def landing(command: str, tree: dict, flag: str, anchor: str) -> tuple[list, int] | None:
    """Соседи и индекс блока, рядом с которым окажется запись; нет — None.

    Сосед по --siblingId и --targetSiblingId: запись встаёт после него, при
    --position before перед ним, то есть рядом с предыдущим блоком. Перед
    первым блоком предыдущего нет: индекс -1, и «Решений» там не бывает.
    Родитель по --id у `blocks add` — запись встаёт в конец его детей, при
    before в начало. Любой другой --id — сам блок: правка, перенос, удаление.
    """
    if str(tree.get('id', '')).lower() == anchor:
        siblings, index = [tree], 0
    else:
        found = find_block(tree, anchor)
        if found is None:
            return None
        siblings, index = found
    position = POSITION_FLAG.search(command)
    before = position is not None and position.group(1) == POSITION_BEFORE
    if flag in ('siblingId', 'targetSiblingId'):
        return (siblings, index - 1) if before else (siblings, index)
    if command.lstrip().startswith(ADD_COMMAND):
        children = siblings[index].get(CONTENT_KEY)
        if isinstance(children, list) and children:
            return children, 0 if before else len(children) - 1
    return siblings, index


def fetch_tree(base: str, page_id: str, timeout: float) -> dict:
    query = urllib.parse.urlencode({'id': page_id, 'maxDepth': API_DEPTH})
    request = urllib.request.Request(
        f'{base.rstrip("/")}/blocks?{query}',
        headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        tree = json.load(response)
    if isinstance(tree, list):
        tree = tree[0] if tree else {}
    if not isinstance(tree, dict):
        raise ValueError('ответ API не блок')
    return tree


def judge_on_page(command: str, tree: dict, anchors: list[tuple[str, str]]) -> Place | None:
    """Суждение по одной странице: None, если ни одной цели на ней нет.

    Целей бывает несколько (`blocks move --id … --targetSiblingId …`), и
    решение — любая из них под «Решениями».
    """
    seen = []
    for flag, anchor in anchors:
        place = landing(command, tree, flag, anchor)
        if place is None:
            continue
        seen.append(anchor[:8])
        if under_decisions(*place):
            return Place(PLACE_DECISION, f'цель {anchor[:8]} под тогглом «Решения» на странице {tree.get("id", "")[:8]}')
    if not seen:
        return None
    return Place(PLACE_OTHER, f'цель {", ".join(seen)} не под «Решениями» на странице {tree.get("id", "")[:8]}')


def decision_zone(command: str, reads: list[str], base: str | None, clock=time.monotonic) -> Place:
    """Ложится ли запись под «Решения».

    `unknown` — замок судить не может, и это не вина хода: нет
    `CRAFT_API_BASE`, id цели не разобран, API не ответил или не уложился в
    DEADLINE_SEC. Замок тогда молчит с причиной в следе: лишний запрет
    остановил бы верную запись, а решение без скилла поймает ревью хода.

    Цель, которой нет ни на одной прочитанной странице, — вина хода: по
    механике Craft страница цели читается до записи. Это не `unknown`,
    а отдельный исход, по которому замок запрещает запись.
    """
    anchors, broken = anchors_of(command)
    if broken:
        return Place(PLACE_UNKNOWN, f'id цели не разобран: {broken}')
    if not anchors:
        return Place(PLACE_OTHER, '')
    if not base:
        return Place(PLACE_UNKNOWN, f'{BASE_ENV} не задан, место записи не проверить')
    if not reads:
        return Place(PLACE_UNREAD, 'в сессии нет чтений Craft')
    deadline, failures = clock() + DEADLINE_SEC, []
    for page_id in reads:
        left = deadline - clock()
        if left <= 0:
            return Place(PLACE_UNKNOWN, f'не уложился в {DEADLINE_SEC} с, страниц проверено {len(failures)}')
        try:
            tree = fetch_tree(base, page_id, min(API_TIMEOUT_SEC, left))
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
            failures.append(f'{page_id[:8]}: {error}')
            continue
        verdict = judge_on_page(command, tree, anchors)
        if verdict is not None:
            return verdict
    if failures:
        return Place(PLACE_UNKNOWN, 'API Craft не ответил: ' + '; '.join(failures))
    return Place(PLACE_UNREAD, 'цель записи не найдена среди страниц, прочитанных в сессии')


