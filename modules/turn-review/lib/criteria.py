"""Критерии ревью: разобранный файл критерия, правило applies и проблемы приёма.

Критерии привозят модули файлом data/review-criterion.md: шапка «id: ‹имя›»
и «applies: always | craft | tags #тег …», пустая строка, текст. Файл
разбирается в неизменяемый Criterion, чьи правила держит сам тип.
Теги в командах записей поставщик не размечает: их ищет здесь функция
поиска тегов, которую передаёт хук (find_tags модуля command-tree-craft).
Здесь же правило, по которому чистый вердикт не принимается: одно на stop и
превью.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import NewType

from .records import VolumeRecord, WriteRecord
from .volume import count_writes

CriterionId = NewType('CriterionId', str)
# Поиск тегов набора в команде: (команда, теги) → найденные теги в нижнем регистре.
TagFinder = Callable[[str, Iterable[str]], list[str]]

ID_FIELD = 'id'
APPLIES_FIELD = 'applies'
APPLIES_ALWAYS = 'always'
APPLIES_CRAFT = 'craft'
APPLIES_TAGS = 'tags'
HEAD_FIELDS = (ID_FIELD, APPLIES_FIELD)
TAG_PREFIX = '#'
PROBLEM_SEPARATOR = '. '
NO_TAG_FINDER_PROBLEM = ('теги в командах записей искать нечем: нет библиотеки разбора команд '
                         'command-tree-craft. Критерии по тегам взяты все, но чистый вердикт '
                         'не принимается. Скажи об этом человеку')
NO_CRITERIA_PROBLEM = ('ни один критерий не подошёл к объёму с записями write: проверять '
                       'записи нечем, чистый вердикт не принимается. Скажи об этом человеку')


def is_tag(word: str) -> bool:
    """Тег — слово с «#» и хотя бы одним знаком после: то же правило, что у тегов
    в data/entity.json для замка craft-type-lock. Пробелов в слове нет по разбору."""
    return word.startswith(TAG_PREFIX) and len(word) > len(TAG_PREFIX)


def _is_lower_tag(tag) -> bool:
    return isinstance(tag, str) and is_tag(tag) and tag == tag.lower()


@dataclass(frozen=True)
class Always:
    """Критерий входит в поручение всегда."""


@dataclass(frozen=True)
class Craft:
    """Критерий входит, когда в объёме есть записи write."""


@dataclass(frozen=True)
class Tags:
    """Критерий входит, когда запись write несёт один из тегов.

    Набор непустой, каждый тег по is_tag и в нижнем регистре: так сверка с
    тегами записи не зависит от того, как правило собрано.
    """
    tags: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.tags, frozenset) or not self.tags:
            raise ValueError(f'правило tags без тегов: {self.tags!r}')
        wrong = [tag for tag in self.tags if not _is_lower_tag(tag)]
        if wrong:
            raise ValueError(f'в правиле tags не теги «{TAG_PREFIX}…» в нижнем регистре: '
                             f'{", ".join(map(repr, wrong))}')


Rule = Always | Craft | Tags
RULE_TYPES = (Always, Craft, Tags)


@dataclass(frozen=True)
class Criterion:
    """Разобранный файл критерия: непустые id и текст, правило из трёх."""
    id: CriterionId
    applies: Rule
    body: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError(f'нет id: {self.id!r}')
        if not isinstance(self.body, str) or not self.body.strip():
            raise ValueError(f'нет текста у критерия «{self.id}»')
        if not isinstance(self.applies, RULE_TYPES):
            # ValueError: read_criteria называет его неразобранным файлом, а не падает.
            raise ValueError(f'правило {self.applies!r} не из Always, Craft, Tags')  # noqa: TRY004


def parse_applies(rule: str) -> Rule:
    """Правило `applies` строкой в правило-тип. Иное — ValueError с причиной."""
    words = rule.split()
    if words == [APPLIES_ALWAYS]:
        return Always()
    if words == [APPLIES_CRAFT]:
        return Craft()
    if words[:1] == [APPLIES_TAGS] and words[1:] and all(is_tag(word) for word in words[1:]):
        return Tags(frozenset(word.lower() for word in words[1:]))
    raise ValueError(f'applies «{rule}»: ждал {APPLIES_ALWAYS}, {APPLIES_CRAFT} или '
                     f'{APPLIES_TAGS} и теги с «{TAG_PREFIX}»')


def _parse_head(lines: list[str]) -> tuple[dict, int]:
    """Поля шапки и номер пустой строки после неё. Иное — ValueError с причиной."""
    head: dict[str, str] = {}
    for index, line in enumerate(lines):
        if not line.strip():
            return head, index
        key, separator, value = line.partition(':')
        key = key.strip()
        if not separator or key not in HEAD_FIELDS:
            raise ValueError(f'строка «{line}» в шапке: шапка — только {", ".join(HEAD_FIELDS)}, '
                             'после неё нужна пустая строка')
        if key in head:
            raise ValueError(f'поле «{key}» в шапке дважды')
        head[key] = value.strip()
    raise ValueError('после шапки нет пустой строки и текста критерия')


def make_criterion(text: str) -> Criterion:
    """Критерий из текста файла: шапка «id: …» и «applies: …», пустая строка, текст.

    Без пустой строки граница не видна: первая строка текста ушла бы в шапку
    или пропала. Чего-то нет или правило не из трёх — ValueError с причиной.
    """
    lines = text.splitlines()
    head, blank = _parse_head(lines)
    body = '\n'.join(lines[blank + 1:]).strip()
    if not head.get(ID_FIELD):
        raise ValueError('нет id')
    if not body:
        raise ValueError('нет текста')
    if APPLIES_FIELD not in head:
        raise ValueError('нет applies')
    return Criterion(CriterionId(head[ID_FIELD]), parse_applies(head[APPLIES_FIELD]), body)


def rule_tags(criteria: Iterable[Criterion]) -> frozenset[str]:
    """Все теги из правил tags: их и ищем в командах записей."""
    found: set[str] = set()
    for criterion in criteria:
        if isinstance(criterion.applies, Tags):
            found |= criterion.applies.tags
    return frozenset(found)


def write_tags(volume: list[VolumeRecord], tags: frozenset[str],
               find_tags: TagFinder | None) -> frozenset[str] | None:
    """Теги из `tags`, которые несут команды записей write объёма; искать нечем — None.

    Тег в тексте ссылки на блок не считается: это правило find_tags.
    """
    if find_tags is None:
        return None
    found: set[str] = set()
    for record in volume:
        if isinstance(record, WriteRecord):
            found |= {tag.lower() for tag in find_tags(record.command, tags)}
    return frozenset(found)


def criterion_applies(criterion: Criterion, volume: list[VolumeRecord],
                      tags: frozenset[str] | None) -> bool:
    """Подходит ли критерий к объёму. `tags` — теги записей write, None — не найти.

    Теги не найти — критерий по тегам берётся при любой записи write: лишняя
    проверка дешевле пропущенной, а чистый вердикт всё равно не примется.
    """
    match criterion.applies:
        case Always():
            return True
        case Craft():
            return count_writes(volume) > 0
        case Tags(tags=rule):
            return count_writes(volume) > 0 if tags is None else bool(rule & tags)


def broken_problem(broken: tuple[str, ...]) -> str | None:
    """Файлы ревью модулей, которые не разобрались: молча они не пропадают."""
    if not broken:
        return None
    return (f'файлы ревью не разобрались у модулей: {"; ".join(broken)}. Их в поручении нет, '
            'чистый вердикт не принимается: скажи об этом человеку')


def missing_criteria_problem(criteria: tuple[CriterionId, ...],
                             found: tuple[Criterion, ...]) -> str | None:
    """Id из поручения, которых среди найденных критериев нет.

    Модуль убрали или переименовали id после поручения: проверки по такому id
    уже не повторить, и молча он не пропадает.
    """
    known = {criterion.id for criterion in found}
    missing = [item for item in criteria if item not in known]
    if not missing:
        return None
    names = ', '.join(f'«{item}»' for item in missing)
    return (f'критерий {names} из поручения не нашёлся у модулей: проверять по нему нечем, '
            'чистый вердикт не принимается. Скажи об этом человеку')


def criteria_problem(volume: list[VolumeRecord], criteria: tuple[CriterionId, ...],
                     found: tuple[Criterion, ...], broken: tuple[str, ...],
                     can_find_tags: bool) -> str | None:
    """Почему чистый вердикт по объёму не принять; всё в порядке — None.

    Правило одно на stop и превью: не разобранные файлы, объём с записями
    write без единого критерия, критерий поручения без найденного файла и
    записи write, в чьих командах теги искать нечем.
    """
    writes = count_writes(volume)
    problems = (broken_problem(broken),
                NO_CRITERIA_PROBLEM if writes and not criteria else None,
                missing_criteria_problem(criteria, found),
                NO_TAG_FINDER_PROBLEM if writes and not can_find_tags else None)
    return PROBLEM_SEPARATOR.join(problem for problem in problems if problem) or None
