"""Какие сущности задевает запись в Craft: место записи и правленая сущность.

Часть замка craft-type-lock. Место записи — ближайшая страница, внутри
которой запись окажется: страница-блок (у Craft детьми владеют только
страницы, задача с телом тоже страница) или корень документа. Выше этой
страницы замок не смотрит: блок на подстранице без тега внутри проекта —
запись в подстраницу, а не в проект. Тега типа у страницы нет — сущности
места нет. Правленая сущность — сама цель правки, переноса или удаления,
когда в её тексте стоит тег типа: `tasks update` по задаче правит задачу.

Где лежит цель, отдаёт connect-API (hooks/api.py): сам блок и путь страниц
до него. Цель должна лежать на странице, которую агент читал в сессии: сама
цель или одна из страниц на пути к ней среди прочитанных. Цели из команды
разбирает библиотека command-tree-craft, поиск тегов в тексте даёт точка
входа замка hooks/module.py; здесь только суждение о месте.
"""

import enum
import time
from collections.abc import Callable
from typing import NamedTuple

from api import BASE_ENV, DEADLINE_SEC, FETCH_ERRORS, ID_KEY, NOT_FOUND, Api, Late, same_id

__all__ = ['BASE_ENV', 'Entity', 'Place', 'Verdict', 'page_types']

MARKDOWN_KEY = 'markdown'
CONTENT_KEY = 'content'
TYPE_KEY = 'type'
PAGE_TYPE = 'page'
SHORT_ID = 8
TITLE_PREVIEW = 60


class Verdict(enum.Enum):
    """Исходы проверки места.

    LOCATED — все цели найдены, сущности записи известны (их может не быть:
    страница без типа). NO_TARGET — у команды нет цели, судить нечего.
    UNREAD — цель не лежит ни на одной прочитанной странице: запись в
    непрочитанное. UNAVAILABLE — место не определить, и ход в этом не
    виноват: нет базы API, API не ответил, срок вышел, страница над целью
    не видна; предупреждение в ход выводится раз за сессию. UNPARSED — в
    команде не разобран ни один id цели или часть, предупреждать не о чем.
    Сущности уже найденных целей приходят при любом исходе.
    """
    LOCATED = 'located'
    NO_TARGET = 'no-target'
    UNREAD = 'unread'
    UNAVAILABLE = 'unavailable'
    UNPARSED = 'unparsed'


class Entity(NamedTuple):
    """Сущность: блок с тегом типа в тексте."""
    block_id: str
    title: str
    tags: tuple[str, ...]


class Place(NamedTuple):
    """Исход проверки: entities — сущности места записи, edited — правленые цели."""
    verdict: Verdict
    reason: str
    entities: tuple[Entity, ...] = ()
    edited: tuple[Entity, ...] = ()


# Теги типа в тексте блока: даёт module.py по справочнику модулей сущностей.
TagsOf = Callable[[str], list[str]]


class Spot(NamedTuple):
    """Где лежит цель: сам блок и страницы от корня до той, где он лежит.

    Страница пути — словарь с `id`; текст (`markdown`) есть у страниц из
    дерева прочитанной страницы, у страниц из поиска его нет. whole — путь
    от корня документа (из поиска): пустой путь значит, что цель — корень.
    Путь из дерева прочитанной страницы начинается с её корня.
    """
    block: dict
    pages: list[dict]
    whole: bool


class Outcome(NamedTuple):
    """Суждение об одной цели: страница места, её тип и правленая сущность."""
    anchor: object
    located: bool
    page: str = ''
    entity: Entity | None = None
    edited: Entity | None = None
    unknown: str = ''


def short(block_id: str) -> str:
    return block_id[:SHORT_ID]


def is_page(block: dict) -> bool:
    return block.get(TYPE_KEY) == PAGE_TYPE


def edits_target(anchor) -> bool:
    """Правка, перенос или удаление самой цели, а не запись рядом или внутрь."""
    return not anchor.is_sibling and not anchor.is_container


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
    """Цель на прочитанной странице: она сама или страница на пути к ней прочитана."""
    ids = [spot.block[ID_KEY], *(page[ID_KEY] for page in spot.pages)]
    return any(same_id(block_id, read) for block_id in ids for read in reads)


def spot_in_reads(api: Api, block_id: str, reads: list[str]) -> tuple[Spot | None, list[str]]:
    """Запасной путь: цель в деревьях прочитанных страниц, свежие первыми.

    Дерево, где цель — корень, страницы над ней не знает: ищется дальше, а
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


def entity_of(block: dict, tags_of: TagsOf) -> Entity | None:
    markdown = block.get(MARKDOWN_KEY)
    tags = tags_of(markdown) if isinstance(markdown, str) else []
    if not tags:
        return None
    return Entity(block[ID_KEY], ' '.join(markdown.split())[:TITLE_PREVIEW], tuple(tags))


def locate_spot(api: Api, block: dict, reads: list[str]) -> tuple[Spot | None, list[str]]:
    """Путь страниц из поиска, а без него — из деревьев прочитанных страниц."""
    path = api.page_path(block)
    if path is not None:
        return Spot(block, path, True), []
    return spot_in_reads(api, block[ID_KEY], reads)


def judge_target(api: Api, anchor, reads: list[str], tags_of: TagsOf) -> Outcome:
    """Где лежит цель, какая страница принимает запись и какую сущность она правит.

    Родитель, который сам прочитан, пути не требует: один запрос. Иначе путь
    страниц. Текст страницы берётся свежим из API.
    """
    block = api.block(anchor.block_id)
    if anchor.is_container and any(same_id(block[ID_KEY], read) for read in reads):
        return Outcome(anchor, True, block[ID_KEY], entity_of(block, tags_of))
    spot, failures = locate_spot(api, block, reads)
    if spot is None:
        return Outcome(anchor, False, unknown='; '.join(failures))
    if not is_read(spot, reads):
        return Outcome(anchor, False)
    edited = entity_of(block, tags_of) if edits_target(anchor) else None
    if anchor.is_container:
        page = block
    elif spot.pages:
        page = spot.pages[-1]
    elif spot.whole:
        # Цель — корень документа: страницы над ней нет, места с типом тоже.
        return Outcome(anchor, True, edited=edited)
    else:
        return Outcome(anchor, False, unknown=f'страница над целью {short(block[ID_KEY])} не видна: '
                                              'цель прочитана сама, а поиск её не нашёл')
    if MARKDOWN_KEY not in page:
        page = api.block(page[ID_KEY])
    return Outcome(anchor, True, page[ID_KEY], entity_of(page, tags_of), edited)


def fetch_failure(block_id: str, error: Exception) -> str:
    # 404 в обоих регистрах: блока нет или он вне шаринга connect-ссылки.
    # Это не сеть, и оператору важно это различать.
    if getattr(error, 'code', None) == NOT_FOUND:
        return f'{short(block_id)}: не найдена в API в обоих регистрах'
    return f'{short(block_id)}: {error}'


def place_part(outcome: Outcome) -> str:
    target = short(outcome.anchor.block_id)
    if not outcome.page:
        return f'цель {target} — корень документа'
    if outcome.entity is None:
        return f'цель {target} на странице {short(outcome.page)} без типа'
    return (f'цель {target} в сущности {short(outcome.entity.block_id)} '
            f'({", ".join(outcome.entity.tags)})')


def describe(found: list[Outcome]) -> tuple[str, tuple[Entity, ...], tuple[Entity, ...]]:
    """Причина для следа, сущности места и правленые сущности без повторов."""
    places, edited, parts = {}, {}, []
    for outcome in found:
        part = place_part(outcome)
        if outcome.entity is not None:
            places.setdefault(outcome.entity.block_id.lower(), outcome.entity)
        if outcome.edited is not None:
            edited.setdefault(outcome.edited.block_id.lower(), outcome.edited)
            part += f', правит сущность ({", ".join(outcome.edited.tags)})'
        parts.append(part)
    return '; '.join(parts), tuple(places.values()), tuple(edited.values())


class Search(NamedTuple):
    outcomes: list[Outcome]
    failures: list[str]
    late: bool


def search(anchors: list, reads: list[str], api: Api, tags_of: TagsOf) -> Search:
    outcomes, failures = [], []
    for anchor in anchors:
        try:
            outcomes.append(judge_target(api, anchor, reads, tags_of))
        except Late:
            return Search(outcomes, failures, True)
        except FETCH_ERRORS as error:
            failures.append(fetch_failure(anchor.block_id, error))
    return Search(outcomes, failures, False)


def verdict_of(result: Search, broken: list[str]) -> Place:
    reason, entities, edited = describe([item for item in result.outcomes if item.located])
    unread = [item for item in result.outcomes if not item.located and not item.unknown]
    unknown = [item.unknown for item in result.outcomes if item.unknown]

    def place(verdict: Verdict, why: str) -> Place:
        return Place(verdict, why, entities, edited)

    if unread:
        names = ', '.join(short(item.anchor.block_id) for item in unread)
        return place(Verdict.UNREAD, f'цель {names} не найдена среди страниц, прочитанных в сессии')
    if result.late:
        return place(Verdict.UNAVAILABLE, f'не уложился в {DEADLINE_SEC} с')
    if result.failures or unknown:
        return place(Verdict.UNAVAILABLE,
                     'место цели не определено: ' + '; '.join([*result.failures, *unknown]))
    if broken:
        return place(Verdict.UNPARSED, f'часть целей не разобрана: {", ".join(broken)}; {reason}')
    return place(Verdict.LOCATED, reason)


def page_types(targets, reads: list[str], base: str | None, tags_of: TagsOf,
               clock=time.monotonic) -> Place:
    """Какие сущности задевает запись: место каждой цели и правленые цели.

    targets — цели команды записи от command-tree-craft (write_targets).
    Целей в команде бывает несколько, сущности собираются со всех. Цель,
    которая не лежит ни на одной прочитанной странице, — UNREAD: по механике
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
    return verdict_of(search(anchors, reads, Api(base, clock), tags_of), broken)
