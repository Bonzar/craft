"""Какие сущности задевает запись в Craft: владельцы места и правленая сущность.

Часть замка craft-type-lock. Место записи судится по ближайшему владельцу с
типом (hooks/owner.py): новая строка — владелец места, куда она встанет, с
её отступом; правка блока без типа — владелец блока. Правка блока с типом
задевает только его самого, кроме смены состояния — тогда задет и его
владелец (hooks/payload.py); перенос и удаление задевают владельца там,
откуда блок уходит, перенос — ещё и там, куда он встаёт.

Цель должна лежать на странице, которую агент читал в сессии: сама цель или
одна из страниц на пути к ней среди прочитанных. Цели из команды разбирает
библиотека command-tree-craft, поиск тегов в тексте даёт точка входа замка
hooks/module.py; здесь только суждение о месте.
"""

import enum
import time
from typing import NamedTuple

from api import BASE_ENV, DEADLINE_SEC, FETCH_ERRORS, ID_KEY, Api, Late, same_id
from owner import (
    Entity,
    Missing,
    Spot,
    Start,
    TagsOf,
    Unseen,
    Walk,
    entity_of,
    fetch_failure,
    is_read,
    level_of,
    locate_spot,
    short,
)
from payload import Write, changes_state

__all__ = ['BASE_ENV', 'Entity', 'Place', 'Verdict', 'page_types']

# Отступ новых блоков не разобран: владельцы ищутся на всех уровнях до этого.
GUESS_LEVELS = range(8)


class Verdict(enum.Enum):
    """Исходы проверки места.

    LOCATED — все цели найдены, владельцы известны (их может не быть: выше
    места типа нет). GUESSED — цели найдены, но отступ, с которым встанет
    запись, не разобран: владельцы — все кандидаты, тип не определён точно.
    NO_TARGET — у команды нет цели, судить нечего. UNREAD — цель не лежит ни
    на одной прочитанной странице: запись в непрочитанное. UNAVAILABLE —
    место не определить, и ход в этом не виноват: нет базы API, API не
    ответил, срок вышел, страница над целью не видна; предупреждение в ход
    выводится раз за сессию. UNPARSED — в команде не разобран ни один id цели
    или часть. UNTARGETED — у команды, у которой цель есть всегда, цели нет
    ни во флаге, ни в JSON правки: Craft найдёт её путём, которого замок не
    видит, и запись закрыта. Сущности уже найденных целей приходят при любом
    исходе.
    """
    LOCATED = 'located'
    GUESSED = 'guessed'
    NO_TARGET = 'no-target'
    UNREAD = 'unread'
    UNAVAILABLE = 'unavailable'
    UNPARSED = 'unparsed'
    UNTARGETED = 'untargeted'


# Пачка судится по командам; исход пачки — самый строгий исход её команд:
# запреты впереди, неопределённость впереди определённого.
STRICTNESS = (Verdict.UNREAD, Verdict.UNTARGETED, Verdict.UNAVAILABLE, Verdict.UNPARSED,
              Verdict.GUESSED, Verdict.LOCATED, Verdict.NO_TARGET)
DATED_NOTE = 'цель — заметка дня по дате: место не судится'


class Place(NamedTuple):
    """Исход проверки.

    entities — владельцы места; edited — правленые цели с типом; closing —
    владельцы целей, чьё состояние запись меняет (module.py решает, какие из
    них требуют своего скилла).
    """
    verdict: Verdict
    reason: str
    entities: tuple[Entity, ...] = ()
    edited: tuple[Entity, ...] = ()
    closing: tuple[tuple[Entity, Entity], ...] = ()


class Outcome(NamedTuple):
    """Суждение об одной цели."""
    anchor: object
    located: bool
    owners: tuple[Entity, ...] = ()
    edited: Entity | None = None
    closing: tuple[tuple[Entity, Entity], ...] = ()
    unknown: str = ''
    guessed: bool = False


def owners_at(walk: Walk, start: Start, levels) -> tuple[tuple[Entity, ...], bool]:
    """Владельцы места на каждом из уровней; уровни не разобраны или их нет — на всех."""
    guessed = not levels
    found = {}
    for level in GUESS_LEVELS if guessed else sorted(levels):
        owner = walk.owner(start, level)
        if owner is not None:
            found.setdefault(owner.block_id.lower(), owner)
    return tuple(found.values()), guessed


def spot_of(walk: Walk, block: dict) -> tuple[Spot | None, str]:
    """Путь страниц до цели. Сама цель прочитана — путь не нужен: ищется, когда понадобится."""
    if any(same_id(block[ID_KEY], read) for read in walk.reads):
        return Spot(block, [], False), ''
    spot, failures = locate_spot(walk.api, block, walk.reads)
    return spot, '; '.join(failures)


def judge_self(walk: Walk, anchor, block: dict, start: Start, write: Write) -> Outcome:
    """Правка, перенос или удаление самой цели."""
    edited = entity_of(block, walk.tags_of)
    closes = edited is not None and not write.removes and changes_state(write, block.get('markdown'))
    if edited is not None and not write.removes and not closes:
        return Outcome(anchor, True, edited=edited)
    owners, _ = owners_at(walk, start, {level_of(block)})
    if closes:
        return Outcome(anchor, True, edited=edited, closing=tuple((edited, owner) for owner in owners))
    return Outcome(anchor, True, owners, edited)


def probe_target(walk: Walk, block_id: str) -> None:
    """Цели нет в дереве прочитанного — один запрос детей страницы, где она вероятнее.

    Кандидаты: страница над целью, известная по вложенности markdown-чтения,
    затем прочитанные страницы с неизвестными детьми, свежие первыми. Цель не
    нашлась и там — дальше обычный путь через API.
    """
    tree = walk.api.read_tree
    if tree is None or walk.api.known(block_id) is not None:
        return
    parent = tree.parent(block_id)
    candidates = [parent] if parent else []
    candidates += [read for read in walk.reads if tree.is_unknown(read)]
    for page_id in candidates:
        if tree.is_unknown(page_id):
            walk.api.probe(page_id)
            return


def judge_target(walk: Walk, anchor, write: Write, landing) -> Outcome:
    """Где лежит цель и каких владельцев задевает запись по ней.

    landing — отступы, с которыми запись встанет рядом с целью или в её
    дети: у `blocks add` из нагрузки, у переноса — отступ переносимого блока.
    """
    probe_target(walk, anchor.block_id)
    block = walk.api.block(anchor.block_id)
    spot, failures = spot_of(walk, block)
    if spot is None:
        return Outcome(anchor, False, unknown=failures)
    if not is_read(spot, walk.reads):
        return Outcome(anchor, False)
    pages = [page[ID_KEY] for page in spot.pages]
    if anchor.is_container:
        start = Start([*pages, block[ID_KEY]], spot.whole, None, write.at_end, block)
    else:
        start = Start(pages, spot.whole, block[ID_KEY], anchor.is_sibling and not write.before, block)
    if not anchor.is_container and not anchor.is_sibling:
        return judge_self(walk, anchor, block, start, write)
    owners, guessed = owners_at(walk, start, landing)
    return Outcome(anchor, True, owners, guessed=guessed)


def place_part(outcome: Outcome) -> str:
    target = short(outcome.anchor.block_id)
    owners = [*outcome.owners, *(owner for _, owner in outcome.closing)]
    if owners:
        part = f'цель {target} у владельца ' + ', '.join(
            f'{short(owner.block_id)} ({", ".join(owner.tags)})' for owner in owners)
    else:
        part = f'цель {target} без владельца с типом'
    if outcome.guessed:
        part += ' (отступ записи не разобран: все кандидаты)'
    if outcome.edited is not None:
        part += f', правит сущность ({", ".join(outcome.edited.tags)})'
    if outcome.closing:
        part += ', меняет её состояние'
    return part


def describe(found: list[Outcome]) -> tuple[str, tuple, tuple, tuple]:
    """Причина для следа, владельцы, правленые сущности и владельцы закрытия без повторов."""
    owners, edited, closing = {}, {}, {}
    for outcome in found:
        for owner in outcome.owners:
            owners.setdefault(owner.block_id.lower(), owner)
        if outcome.edited is not None:
            edited.setdefault(outcome.edited.block_id.lower(), outcome.edited)
        for pair in outcome.closing:
            closing.setdefault((pair[0].block_id.lower(), pair[1].block_id.lower()), pair)
    reason = '; '.join(place_part(outcome) for outcome in found)
    return reason, tuple(owners.values()), tuple(edited.values()), tuple(closing.values())


class Search(NamedTuple):
    outcomes: list[Outcome]
    failures: list[str]
    late: bool


def judge_safely(walk: Walk, anchor, write: Write, landing, result: Search) -> bool:
    """Суждение об одной цели в result; False — срок вышел."""
    try:
        result.outcomes.append(judge_target(walk, anchor, write, landing))
    except Late:
        return False
    except Unseen as error:
        result.outcomes.append(Outcome(anchor, False, unknown=str(error)))
    except Missing:
        # Страница цели уже не та, что агент читал: запись в непрочитанное.
        result.outcomes.append(Outcome(anchor, False))
    except FETCH_ERRORS as error:
        result.failures.append(fetch_failure(anchor.block_id, error))
    return True


def search(anchors: list, walk: Walk, write: Write) -> Search:
    """Сначала сами цели: у переноса их отступ — отступ места, куда он встаёт."""
    result = Search([], [], False)
    own = [anchor for anchor in anchors if not anchor.is_sibling and not anchor.is_container]
    for anchor in own:
        if not judge_safely(walk, anchor, write, None, result):
            return result._replace(late=True)
    landing = write.levels
    if write.moves:
        moved = [walk.api.blocks.get(anchor.block_id.lower()) for anchor in own]
        landing = frozenset(level_of(block) for block in moved if block) or None
    for anchor in anchors:
        if anchor in own:
            continue
        if not judge_safely(walk, anchor, write, landing, result):
            return result._replace(late=True)
    return result


def verdict_of(result: Search, broken: list[str]) -> Place:
    located = [item for item in result.outcomes if item.located]
    reason, entities, edited, closing = describe(located)
    unread = [item for item in result.outcomes if not item.located and not item.unknown]
    unknown = [item.unknown for item in result.outcomes if item.unknown]

    def place(verdict: Verdict, why: str) -> Place:
        return Place(verdict, why, entities, edited, closing)

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
    if any(item.guessed for item in located):
        return place(Verdict.GUESSED, reason)
    return place(Verdict.LOCATED, reason)


def unique(items, key) -> tuple:
    """Без повторов по ключу, порядок первого вхождения."""
    found = {}
    for item in items:
        found.setdefault(key(item), item)
    return tuple(found.values())


def merge(places: list[Place]) -> Place:
    """Исход пачки: самый строгий исход, сущности всех команд без повторов.

    Причина — причины команд с этим исходом впереди, остальные за ними.
    """
    if not places:
        return Place(Verdict.NO_TARGET, '')
    verdict = min((place.verdict for place in places), key=STRICTNESS.index)
    ordered = sorted(places, key=lambda place: place.verdict is not verdict)
    reason = '; '.join(place.reason for place in ordered if place.reason)
    return Place(verdict, reason,
                 unique([e for place in places for e in place.entities], lambda e: e.block_id.lower()),
                 unique([e for place in places for e in place.edited], lambda e: e.block_id.lower()),
                 unique([pair for place in places for pair in place.closing],
                        lambda pair: (pair[0].block_id.lower(), pair[1].block_id.lower())))


def part_type(targets, write: Write, base: str | None, walk: Walk | None) -> Place:
    """Исход одной команды пачки."""
    anchors, broken = targets.anchors, targets.broken
    if not anchors:
        if broken:
            return Place(Verdict.UNPARSED, 'цель в команде не разобрана: ' + ', '.join(broken))
        if targets.dated:
            return Place(Verdict.NO_TARGET, DATED_NOTE)
        if targets.targeted:
            return Place(Verdict.UNTARGETED, 'цели нет ни во флаге, ни в JSON правки')
        return Place(Verdict.NO_TARGET, '')
    if not base:
        return Place(Verdict.UNAVAILABLE, f'{BASE_ENV} не задан, место записи не проверить')
    if walk is None:
        return Place(Verdict.UNREAD, 'в сессии нет чтений Craft')
    return verdict_of(search(anchors, walk, write), broken)


def page_types(parts, reads: list[str], base: str | None, tags_of: TagsOf,
               clock=time.monotonic, tree=None) -> Place:
    """Какие сущности задевает запись: владельцы мест всех целей и правленые цели.

    parts — команды пачки парами (цели от command-tree-craft `write_targets`,
    разбор нагрузки `payload.write_of`); каждая судится отдельно, исходы
    сливаются (merge). Цель, которая не лежит ни на одной прочитанной
    странице, — UNREAD: по механике Craft страница цели читается до записи.
    Запросы всех команд идут одной проверкой: общий срок и общая память. tree —
    дерево прочитанного: что в нём есть, в API не запрашивается.
    """
    walk = Walk(Api(base, clock, tree), reads, tags_of) if base and reads else None
    return merge([part_type(targets, write, base, walk) for targets, write in parts])
