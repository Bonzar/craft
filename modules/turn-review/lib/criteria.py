"""Критерии ревью: разобранный файл критерия, правило applies и проблемы приёма.

Критерии привозят модули файлом data/review-criterion.md: шапка «id: ‹имя›»
и «applies: always | craft | tags #тег …», пустая строка, текст. Файл
разбирается в неизменяемый Criterion, чьи правила держит сам тип.
Здесь же правило, по которому чистый вердикт не принимается: одно на stop и
превью.
"""

from dataclasses import dataclass
from typing import NewType

from .records import VolumeRecord, WriteRecord
from .volume import count_writes

CriterionId = NewType('CriterionId', str)

ID_FIELD = 'id'
APPLIES_FIELD = 'applies'
APPLIES_ALWAYS = 'always'
APPLIES_CRAFT = 'craft'
APPLIES_TAGS = 'tags'
HEAD_FIELDS = (ID_FIELD, APPLIES_FIELD)
TAG_PREFIX = '#'
PROBLEM_SEPARATOR = '. '
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


def write_tags(volume: list[VolumeRecord]) -> set[str]:
    """Теги типа всех записей write объёма, в нижнем регистре."""
    found = set()
    for record in volume:
        if isinstance(record, WriteRecord) and record.types is not None:
            found |= {item.lower() for item in record.types}
    return found


def criterion_applies(criterion: Criterion, volume: list[VolumeRecord]) -> bool:
    """Подходит ли критерий к объёму. Правило из трёх держит сам Criterion."""
    match criterion.applies:
        case Always():
            return True
        case Craft():
            return count_writes(volume) > 0
        case Tags(tags=tags):
            return bool(tags & write_tags(volume))


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


def untagged_problem(volume: list[VolumeRecord], found: tuple[Criterion, ...]) -> str | None:
    """Теги записей write, на которые нет ни одного критерия с правилом tags.

    Модуль сущности без критерия иначе прошёл бы молча: общие критерии
    always и craft подходят к любой записи и тег не проверяют.
    """
    covered: set[str] = set()
    for criterion in found:
        if isinstance(criterion.applies, Tags):
            covered |= criterion.applies.tags
    missing = sorted(write_tags(volume) - covered)
    if not missing:
        return None
    return (f'нет критерия на тег {", ".join(missing)}: записи с ним проверить нечем, '
            'чистый вердикт не принимается. Скажи об этом человеку')


def unknown_tags_problem(volume: list[VolumeRecord]) -> str | None:
    """Записи write, чьи теги неизвестны: поставщик назвал ошибку справочника.

    Критерии по тегам такую запись не выбирают, и untagged_problem её не
    видит: без этой проверки чистый вердикт прошёл бы без критериев её типа.
    `types` не список до объёма не доходит: такую строку отвергает чтение.
    """
    reasons = [record.types_error for record in volume
               if isinstance(record, WriteRecord) and record.types_error is not None]
    if not reasons:
        return None
    return (f'теги записи неизвестны, проверить нечем ({"; ".join(dict.fromkeys(reasons))}): '
            'чистый вердикт не принимается. Скажи об этом человеку')


def criteria_problem(volume: list[VolumeRecord], criteria: tuple[CriterionId, ...],
                     found: tuple[Criterion, ...], broken: tuple[str, ...]) -> str | None:
    """Почему чистый вердикт по объёму не принять; всё в порядке — None.

    Правило одно на stop и превью: не разобранные файлы, объём с записями
    write без единого критерия, критерий поручения без найденного файла,
    запись с неизвестными тегами и тег записи без своего критерия.
    """
    problems = (broken_problem(broken),
                NO_CRITERIA_PROBLEM if count_writes(volume) and not criteria else None,
                missing_criteria_problem(criteria, found),
                unknown_tags_problem(volume),
                untagged_problem(volume, found))
    return PROBLEM_SEPARATOR.join(problem for problem in problems if problem) or None
