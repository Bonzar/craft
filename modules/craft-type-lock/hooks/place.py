"""В какую сущность ложится запись в Craft: тип страницы цели.

Часть замка craft-type-lock. Родителя блока connect-API не отдаёт, поэтому
цель записи ищется на страницах, которые агент читал в сессии: каждая
запрашивается заново на глубину API_DEPTH, и в её дереве ищется путь от
корня до цели. Сущность записи — ближайший к цели блок этого пути, в тексте
которого стоит тег типа: заголовок раздела, этапа, задачи. Цели из команды
разбирает библиотека command-tree-craft, поиск тегов в тексте блока даёт
точка входа замка hooks/module.py; здесь только суждение о месте.
"""

import enum
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, NamedTuple

BASE_ENV = 'CRAFT_API_BASE'
# Без User-Agent connect-API отвечает 403 (замер 01.11.2026 из контейнера).
USER_AGENT = 'jarvis-craft-type-lock'
API_TIMEOUT_SEC = 10
# Потолок на всю проверку места: прочитанных страниц в списке до двадцати, а
# хук не должен держать запись минутами, когда API медленный.
DEADLINE_SEC = 12
# Этап агент читает со страницы проекта на глубину 2: блок этапа на первом
# уровне, его «Решения» и «Описание» на втором. Глубже замок на одном чтении
# не видит: цель на вложенной странице находится, когда прочитана она сама.
API_DEPTH = 2
# Регистр id API различает (замер 01.11.2026: у документа с id в верхнем
# регистре нижний отвечает 404, и наоборот). Id хранятся и запрашиваются как
# есть, сравниваются без учёта регистра.
CONTENT_KEY = 'content'
MARKDOWN_KEY = 'markdown'
ID_KEY = 'id'
NOT_FOUND = 404
SHORT_ID = 8
TITLE_PREVIEW = 60


class Verdict(enum.Enum):
    """Исходы проверки места.

    LOCATED — все цели найдены, сущности записи известны (их может не быть:
    страница без типа). NO_TARGET — у команды нет цели, судить нечего.
    UNREAD — цели нет ни на одной прочитанной странице, запись в
    непрочитанное. Два последних — замок судить не может, и это не вина
    хода. UNAVAILABLE — окружение: нет базы API, API не ответил, не уложились
    в срок; предупреждение в ход выводится раз за сессию. UNPARSED — в
    команде не разобран ни один id цели или часть, предупреждать не о чем.
    Сущности уже найденных целей приходят при любом исходе.
    """
    LOCATED = 'located'
    NO_TARGET = 'no-target'
    UNREAD = 'unread'
    UNAVAILABLE = 'unavailable'
    UNPARSED = 'unparsed'


class Entity(NamedTuple):
    """Сущность, в которую ложится запись: блок с тегом типа в тексте."""
    block_id: str
    title: str
    tags: tuple[str, ...]


class Place(NamedTuple):
    verdict: Verdict
    reason: str
    entities: tuple[Entity, ...] = ()


# Теги типа в тексте блока: даёт module.py по справочнику модулей сущностей.
TagsOf = Callable[[str], list[str]]


def same_id(left: str, right: str) -> bool:
    """Один ли это блок: регистр хранится, но не различается."""
    return left.lower() == right.lower()


def short(block_id: str) -> str:
    return block_id[:SHORT_ID]


def block_id_of(block) -> str:
    return str(block.get(ID_KEY, '')) if isinstance(block, dict) else ''


def find_path(tree: dict, target: str) -> list[dict] | None:
    """Блоки от корня дерева до target включительно; target нет — None."""
    if same_id(block_id_of(tree), target):
        return [tree]
    children = tree.get(CONTENT_KEY) if isinstance(tree, dict) else None
    if not isinstance(children, list):
        return None
    for child in children:
        if not isinstance(child, dict):
            continue
        found = find_path(child, target)
        if found is not None:
            return [tree, *found]
    return None


def entity_of(path: list[dict], anchor, tags_of: TagsOf) -> Entity | None:
    """Ближайший к цели блок с тегом типа среди тех, куда ляжет запись.

    Цель-сосед (--siblingId, --targetSiblingId) — запись встаёт рядом с ней, в
    её родителя: сам сосед в счёт не идёт. Цель-родитель (--id у `blocks
    add`, --targetId у `blocks move`) и цель-сам-блок (правка, перенос,
    удаление) — запись ложится в неё саму. Роль цели даёт command-tree-craft.
    Тега нет ни у одного блока пути — страница без типа, None.
    """
    chain = path[:-1] if anchor.is_sibling else path
    for block in reversed(chain):
        markdown = block.get(MARKDOWN_KEY)
        tags = tags_of(markdown) if isinstance(markdown, str) else []
        if tags:
            title = ' '.join(markdown.split())[:TITLE_PREVIEW]
            return Entity(block_id_of(block), title, tuple(tags))
    return None


def other_case(block_id: str) -> str:
    """Тот же id в другом регистре: верхний для нижнего и наоборот."""
    return block_id.lower() if block_id != block_id.lower() else block_id.upper()


def fetch_tree(base: str, page_id: str, timeout: float) -> dict:
    """Страница на глубину API_DEPTH. 404 повторяется в другом регистре id:
    агент мог набрать id не в том регистре, в каком его знает API."""
    try:
        tree = fetch_once(base, page_id, timeout)
    except urllib.error.HTTPError as error:
        if error.code != NOT_FOUND:
            raise
        tree = fetch_once(base, other_case(page_id), timeout)
    if isinstance(tree, list):
        tree = tree[0] if tree else None
    if not isinstance(tree, dict) or not tree.get(ID_KEY):
        raise ValueError('ответ API не блок')
    return tree


def fetch_once(base: str, page_id: str, timeout: float):
    query = urllib.parse.urlencode({'id': page_id, 'maxDepth': API_DEPTH})
    request = urllib.request.Request(
        f'{base.rstrip("/")}/blocks?{query}',
        headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def locate(tree: dict, pending: list, tags_of: TagsOf) -> tuple[list, list]:
    """Одна страница: какие цели на ней нашлись (с сущностью или None) и какие нет."""
    found, left = [], []
    for anchor in pending:
        path = find_path(tree, anchor.block_id)
        if path is None:
            left.append(anchor)
        else:
            found.append((anchor, entity_of(path, anchor, tags_of)))
    return found, left


def fetch_failure(page_id: str, error: Exception) -> str:
    # 404 в обоих регистрах: страницы нет или она вне шаринга connect-ссылки.
    # Это не сеть, и оператору важно это различать.
    if isinstance(error, urllib.error.HTTPError) and error.code == NOT_FOUND:
        return f'{short(page_id)}: не найдена в API в обоих регистрах'
    return f'{short(page_id)}: {error}'


class Search(NamedTuple):
    found: list
    pending: list
    failures: list[str]
    late: bool


def search(anchors: list, reads: list[str], base: str, tags_of: TagsOf, clock) -> Search:
    """Прочитанные страницы свежие первыми, пока все цели не найдены."""
    deadline, failures, found, pending = clock() + DEADLINE_SEC, [], [], list(anchors)
    for page_id in reads:
        if not pending:
            break
        left = deadline - clock()
        if left <= 0:
            return Search(found, pending, failures, True)
        try:
            tree = fetch_tree(base, page_id, min(API_TIMEOUT_SEC, left))
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
            failures.append(fetch_failure(page_id, error))
            continue
        here, pending = locate(tree, pending, tags_of)
        found.extend(here)
    return Search(found, pending, failures, False)


def describe(found: list) -> tuple[str, tuple[Entity, ...]]:
    """Причина для следа и сущности без повторов, в порядке целей."""
    entities, parts = {}, []
    for anchor, entity in found:
        if entity is None:
            parts.append(f'цель {short(anchor.block_id)} на странице без типа')
            continue
        entities.setdefault(entity.block_id.lower(), entity)
        parts.append(f'цель {short(anchor.block_id)} в сущности {short(entity.block_id)} '
                     f'({", ".join(entity.tags)})')
    return '; '.join(parts), tuple(entities.values())


def page_types(targets, reads: list[str], base: str | None, tags_of: TagsOf,
               clock=time.monotonic) -> Place:
    """В какие сущности ложится запись.

    targets — цели команды записи от command-tree-craft (write_targets).
    Целей в команде бывает несколько, сущности собираются со всех. Цель,
    которой нет ни на одной прочитанной странице, — UNREAD: по механике
    Craft страница цели читается до записи.
    """
    anchors, broken = targets.anchors, targets.broken
    if not anchors:
        if broken or targets.targeted:
            return Place(Verdict.UNPARSED, 'цель в команде не разобрана: ' + (', '.join(broken) or 'флага цели нет'))
        return Place(Verdict.NO_TARGET, '')
    if not base:
        return Place(Verdict.UNAVAILABLE, f'{BASE_ENV} не задан, место записи не проверить')
    if not reads:
        return Place(Verdict.UNREAD, 'в сессии нет чтений Craft')
    result = search(anchors, reads, base, tags_of, clock)
    reason, entities = describe(result.found)
    if result.late:
        return Place(Verdict.UNAVAILABLE, f'не уложился в {DEADLINE_SEC} с', entities)
    if result.pending and result.failures:
        return Place(Verdict.UNAVAILABLE, 'API Craft не ответил: ' + '; '.join(result.failures), entities)
    if result.pending:
        names = ', '.join(short(anchor.block_id) for anchor in result.pending)
        return Place(Verdict.UNREAD, f'цель {names} не найдена среди страниц, прочитанных в сессии', entities)
    if broken:
        return Place(Verdict.UNPARSED, f'часть целей не разобрана: {", ".join(broken)}; {reason}', entities)
    return Place(Verdict.LOCATED, reason, entities)
