"""Владелец места записи: ближайший уровень с типом.

Часть замка craft-type-lock. Уровень владения в Craft — и настоящие дети
страницы, и отступ: на странице блок с отступом больше, чем у ближайшего
предыдущего соседа с меньшим отступом, принадлежит этому соседу. Задачи на
+1 после этапа — этапу, этапы на +1 после вехи — вехе. Владелец — ближайший
уровень с типом: блок или страница, в собственном тексте которых стоит тег
типа. Уровень без типа — переключатель, заголовок, текст, страница или
подстраница без тега — уровнем не считается: подъём идёт через него к
ближайшему владельцу с типом в том же документе. До корня документа типа нет
— владельца нет.

Где лежит блок, отдаёт дерево прочитанного (hooks/tree.py), а без него —
connect-API (hooks/api.py): путь страниц из поиска, а без него — из деревьев
прочитанных страниц.
"""

from collections.abc import Callable
from typing import NamedTuple

from api import FETCH_ERRORS, ID_KEY, NOT_FOUND, Api, same_id

MARKDOWN_KEY = 'markdown'
CONTENT_KEY = 'content'
TYPE_KEY = 'type'
PAGE_TYPE = 'page'
# Поле отступа блока; нет поля — уровень 0 (замер 02.11.2026).
INDENT_KEY = 'indentationLevel'
SHORT_ID = 8
TITLE_PREVIEW = 60

# Теги типа в тексте блока: даёт module.py по справочнику модулей сущностей.
TagsOf = Callable[[str], list[str]]


class Entity(NamedTuple):
    """Сущность: блок с тегом типа в тексте."""
    block_id: str
    title: str
    tags: tuple[str, ...]


class Spot(NamedTuple):
    """Где лежит блок: сам блок и страницы от корня до той, где он лежит.

    whole — путь от корня документа (из поиска): пустой путь значит, что
    блок — корень. Путь из дерева прочитанной страницы начинается с её корня.
    """
    block: dict
    pages: list[dict]
    whole: bool


class Start(NamedTuple):
    """Место, от которого ищется владелец.

    pages — id страниц от верха известного пути до страницы места; whole —
    верх пути — корень документа. anchor — блок страницы, рядом с которым
    место, или None: место в начале или в конце страницы. inclusive — сам
    anchor стоит перед местом (или, без anchor, перед местом все дети
    страницы). top — блок, от которого путь ищется выше, когда pages пуст.
    """
    pages: list[str]
    whole: bool
    anchor: str | None
    inclusive: bool
    top: dict


class Unseen(Exception):
    """Страница над блоком не видна: ни поиск, ни прочитанные деревья её не дали."""


class Missing(Exception):
    """Цели нет среди детей страницы, где её нашли, и после повторного запроса.

    Это не сбой сети: страница уже не та, что агент читал, и запись судится
    как запись в непрочитанное.
    """


def short(block_id: str) -> str:
    return block_id[:SHORT_ID]


def is_page(block: dict) -> bool:
    return block.get(TYPE_KEY) == PAGE_TYPE


def level_of(block: dict) -> int:
    level = block.get(INDENT_KEY, 0)
    if type(level) is not int or level < 0:
        raise ValueError(f'у блока {short(block[ID_KEY])} отступ не число: {level!r}')
    return level


def position(children: list[dict], anchor: str) -> int | None:
    """Место anchor среди детей; нет его — None."""
    return next((index for index, child in enumerate(children) if same_id(child[ID_KEY], anchor)), None)


def entity_of(block: dict, tags_of: TagsOf) -> Entity | None:
    markdown = block.get(MARKDOWN_KEY)
    tags = tags_of(markdown) if isinstance(markdown, str) else []
    if not tags:
        return None
    return Entity(block[ID_KEY], ' '.join(markdown.split())[:TITLE_PREVIEW], tuple(tags))


def fetch_failure(block_id: str, error: Exception) -> str:
    # 404 в обоих регистрах: блока нет или он вне шаринга connect-ссылки.
    # Это не сеть, и оператору важно это различать.
    if getattr(error, 'code', None) == NOT_FOUND:
        return f'{short(block_id)}: не найдена в API в обоих регистрах'
    return f'{short(block_id)}: {error}'


def find_path(tree: dict, target: str) -> list[dict] | None:
    """Блоки от корня дерева до target включительно; target нет — None."""
    if same_id(tree.get(ID_KEY), target):
        return [tree]
    children = tree.get(CONTENT_KEY)
    if not isinstance(children, list):
        return None
    for child in children:
        if not isinstance(child, dict):
            continue
        found = find_path(child, target)
        if found is not None:
            return [tree, *found]
    return None


def is_read(spot: Spot, reads: list[str]) -> bool:
    """Блок на прочитанной странице: он сам или страница на пути к нему прочитана."""
    ids = [spot.block[ID_KEY], *(page[ID_KEY] for page in spot.pages)]
    return any(same_id(block_id, read) for block_id in ids for read in reads)


def spot_in_reads(api: Api, block_id: str, reads: list[str]) -> tuple[Spot | None, list[str]]:
    """Запасной путь: блок в деревьях прочитанных страниц, свежие первыми.

    Дерево, где блок — корень, страницы над ним не знает: ищется дальше, а
    без лучшего отдаётся оно. Второе — страницы, которые API не отдал.
    """
    rooted, failures = None, []
    for page_id in reads:
        try:
            path = find_path(api.tree(page_id), block_id)
        except FETCH_ERRORS as error:
            failures.append(fetch_failure(page_id, error))
            continue
        if path is None:
            continue
        spot = Spot(path[-1], [block for block in path[:-1] if is_page(block)], False)
        if spot.pages:
            return spot, failures
        rooted = rooted or spot
    return rooted, failures


def cached_spot(api: Api, block: dict, reads: list[str]) -> Spot | None:
    """Путь страниц из дерева прочитанного (hooks/tree.py), без запроса.

    Годится, когда доходит до корня документа или проходит через прочитанную
    страницу: иначе страница, прочитанная выше известного куска, осталась бы
    не видна, и запись ушла бы в непрочитанное зря.
    """
    if api.read_tree is None:
        return None
    pages, whole = api.read_tree.chain(block[ID_KEY])
    if not pages and not whole:
        return None
    spot = Spot(block, [{ID_KEY: page_id} for page_id in pages], whole)
    return spot if whole or is_read(spot, reads) else None


def locate_spot(api: Api, block: dict, reads: list[str]) -> tuple[Spot | None, list[str]]:
    """Путь страниц из прочитанного, без него — из поиска, а дальше — из деревьев
    прочитанных страниц."""
    cached = cached_spot(api, block, reads)
    if cached is not None:
        return cached, []
    path = api.page_path(block)
    if path is not None:
        return Spot(block, path, True), []
    return spot_in_reads(api, block[ID_KEY], reads)


class Walk:
    """Подъём от места к владельцу с типом. Запросы одной проверки — общие."""

    def __init__(self, api: Api, reads: list[str], tags_of: TagsOf) -> None:
        self.api = api
        self.reads = reads
        self.tags_of = tags_of

    def above(self, top: dict) -> tuple[list[str], bool]:
        """Путь страниц над блоком top. Не виден — Unseen."""
        spot, failures = locate_spot(self.api, top, self.reads)
        if spot is None or (not spot.whole and not spot.pages):
            detail = f': {"; ".join(failures)}' if failures else ''
            raise Unseen(f'страница над целью {short(top[ID_KEY])} не видна{detail}')
        return [page[ID_KEY] for page in spot.pages], spot.whole

    def preceding(self, page_id: str, anchor: str | None, inclusive: bool) -> list[dict]:
        """Дети страницы, стоящие перед местом.

        anchor нет среди детей — список устарел: он запрашивается заново один
        раз, а без anchor и тогда — Missing.
        """
        children = self.api.children(page_id, anchor)
        if anchor is None:
            return children if inclusive else []
        index = position(children, anchor)
        if index is None:
            children = self.api.children(page_id, anchor, fresh=True)
            index = position(children, anchor)
        if index is None:
            raise Missing(f'блок {short(anchor)} не найден среди детей страницы {short(page_id)}')
        return children[:index + 1] if inclusive else children[:index]

    def by_indent(self, page_id: str, anchor: str | None, inclusive: bool,
                  level: int) -> tuple[Entity | None, int]:
        """Владелец по отступу на одной странице и уровень, с которым идти выше."""
        for block in reversed(self.preceding(page_id, anchor, inclusive)):
            block_level = level_of(block)
            if block_level >= level:
                continue
            found = entity_of(block, self.tags_of)
            if found is not None:
                return found, block_level
            level = block_level
            if level == 0:
                break
        return None, level

    def owner(self, start: Start, level: int) -> Entity | None:
        """Ближайший владелец с типом над местом на уровне level; нет — None."""
        pages, whole = (start.pages, start.whole) if start.pages or start.whole else self.above(start.top)
        anchor, inclusive = start.anchor, start.inclusive
        while pages:
            page_id = pages[-1]
            if level > 0:
                found, level = self.by_indent(page_id, anchor, inclusive, level)
                if found is not None:
                    return found
            page = self.api.block(page_id)
            found = entity_of(page, self.tags_of)
            if found is not None:
                return found
            pages = pages[:-1]
            if not pages and not whole:
                pages, whole = self.above(page)
            anchor, inclusive, level = page_id, False, level_of(page)
        return None
