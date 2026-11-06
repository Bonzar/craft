"""Сборка поручения подагенту и критерии, по которым принимается вердикт.

Каркас и описания видов записей — у ревью: формат строк задаёт только его
контракт. Критерии привозят модули файлом data/review-criterion.md, и ищутся
они только у модулей, чей `for` называет turn-review: их список даёт хук по
шапкам соседей (registry.adapters ядра). Поручение берёт описания видов,
которые есть в объёме, критерии, чьё
правило совпало с объёмом, и требования модулей из data/review.md тех
модулей, чьи скиллы есть в записях skill объёма: те ищутся в каталоге
модулей по slug скилла, `for` им не нужен. Критерии по тегам выбираются по
типу каждой записи из строки замка craft-type-lock (entity_types), а без
строки — все.
"""

from pathlib import Path
from typing import NamedTuple

from .criteria import (
    Criterion,
    CriterionId,
    criteria_problem,
    criterion_applies,
    make_criterion,
)
from .entity_types import selection_tags, types_text
from .records import KINDS, SkillRecord, VolumeRecord

CRITERION_FILE = 'review-criterion.md'
REQUIREMENTS_FILE = 'review.md'
DATA_DIR = 'data'
BRIEF_FILE = 'turn-review-brief.md'
CRITERIA_MARK = '{{CRITERIA}}'
RECORDS_MARK = '{{RECORDS}}'
CRITERIA_LIST_MARK = '{{CRITERIA_LIST}}'
REQUIREMENTS_MARK = '{{MODULE_REQUIREMENTS}}'
TYPES_MARK = '{{ENTITY_TYPES}}'
REQUIREMENTS_HEAD = '### Требования модуля {slug}'
NO_REQUIREMENTS = 'нет'
REVIEW_SLUG = 'turn-review'
# Описание вида записи в data/review-records.md ревью: раздел «## ‹вид›».
KIND_HEAD = '## '
NO_PROVIDER_PROBLEM = ('нет поставщика записей ревью: ни у одного модуля с for = '
                       f'["{REVIEW_SLUG}"] в каталоге {{modules_dir}} нет хуков. Объём пуст не '
                       'потому, что записей не было, а потому, что их некому положить. Скажи '
                       'об этом человеку: нужен модуль-поставщик, например review-craft-calls')
EMPTY_PROVIDER = (f'{{slug}} (объявил for = ["{REVIEW_SLUG}"], а нет ни '
                  f'{DATA_DIR}/{CRITERION_FILE}, ни хуков, которые писали бы записи)')


class Provider(NamedTuple):
    """Модуль, чей `for` называет turn-review: slug, папка и есть ли у него хуки.

    Поставщик критерия узнаётся по файлу критерия, поставщик записей — по
    хукам: строки объёма пишут только они.
    """
    slug: str
    path: Path
    has_hooks: bool


class Sources(NamedTuple):
    """Откуда ревью берёт файлы модулей.

    `providers` — модули с for = ["turn-review"], у них критерии и описания
    записей. `modules_dir` — каталог модулей: в нём требования по slug
    скилла.
    """
    modules_dir: Path
    providers: tuple[Provider, ...]


class BriefError(Exception):
    """Поручение не собрать: каркас не читается или файл поручения не записать."""


class BriefParts(NamedTuple):
    """Собранное поручение: текст, id критериев по порядку и не разобранные файлы."""
    text: str
    criteria: tuple[CriterionId, ...]
    broken: tuple[str, ...]


class CriteriaCheck(NamedTuple):
    """Критерии, по которым принимается вердикт, и почему чистый вердикт не принять."""
    criteria: tuple[CriterionId, ...]
    problem: str | None


def gather(providers: tuple[Provider, ...], filename: str
           ) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Файлы данных с этим именем у модулей-поставщиков: (slug, текст).

    Второе — файлы, которые не прочитались, «slug (путь не читается: ошибка)»:
    молча пропустить их нельзя, а падение хука отпустило бы ход без ревью.
    Модули без for = ["turn-review"] сюда не попадают, их файлы не читаются.
    """
    found, broken = [], []
    for provider in providers:
        path = Path(provider.path) / DATA_DIR / filename
        if not path.is_file():
            continue
        text, problem = read_data_file(provider.slug, path)
        if problem is None:
            found.append((provider.slug, text))
        else:
            broken.append(problem)
    return tuple(found), tuple(broken)


def read_data_file(slug: str, path: Path) -> tuple[str, None] | tuple[None, str]:
    """Текст файла данных модуля или причина «slug (путь не читается: ошибка)»."""
    try:
        return path.read_text(encoding='utf-8'), None
    except (OSError, UnicodeDecodeError) as error:
        return None, f'{slug} ({path} не читается: {error})'


def volume_skills(volume: list[VolumeRecord]) -> tuple[str, ...]:
    """Slug скиллов из записей skill объёма: по порядку, без повторов.

    Slug с разделителем пути или «.», «..» модулем быть не может: такой не
    берётся, иначе путь к файлу требований увёл бы из каталога модулей.
    """
    found: list[str] = []
    for record in volume:
        if not isinstance(record, SkillRecord):
            continue
        slug = record.skill.strip()
        if slug and slug not in {'.', '..'} and Path(slug).name == slug and slug not in found:
            found.append(slug)
    return tuple(found)


def read_requirements(modules_dir, volume: list[VolumeRecord]
                      ) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Требования модулей, чьи скиллы есть в объёме: (slug, текст) и что не прочиталось.

    Модуля или файла нет — требований у скилла нет. Файл есть, но не
    читается — он в broken с путём, как файл критерия: молча он не пропадает.
    """
    found, broken = [], []
    for slug in volume_skills(volume):
        path = Path(modules_dir) / slug / DATA_DIR / REQUIREMENTS_FILE
        if not path.is_file():
            continue
        text, problem = read_data_file(slug, path)
        if problem is None:
            found.append((slug, text))
        else:
            broken.append(problem)
    return tuple(found), tuple(broken)


def requirements_text(requirements: tuple[tuple[str, str], ...]) -> str:
    """Раздел требований для каркаса: по подразделу на модуль, без них — «нет»."""
    if not requirements:
        return NO_REQUIREMENTS
    return '\n\n'.join(f'{REQUIREMENTS_HEAD.format(slug=slug)}\n\n{text.strip()}'
                        for slug, text in requirements)


def empty_providers(providers: tuple[Provider, ...]) -> tuple[str, ...]:
    """Модули с for = ["turn-review"] без критерия и без хуков.

    Модуль объявил, что служит ревью, а дать ему нечего: например, модуль
    сущности потерял критерий. Молча он не проходит.
    """
    return tuple(EMPTY_PROVIDER.format(slug=provider.slug) for provider in providers
                 if not provider.has_hooks
                 and not (Path(provider.path) / DATA_DIR / CRITERION_FILE).is_file())


def kind_sections(descriptions: str) -> tuple[str, dict[str, str]]:
    """Описания видов записей ревью: общая часть до разделов и текст на вид.

    Раздел вида — «## ‹вид›» и текст до следующего раздела. Вид не из
    KINDS или раздел дважды — ValueError: файл свой, и порча в нём — дефект.
    """
    common: list[str] = []
    sections: dict[str, list[str]] = {}
    current = common
    for line in descriptions.splitlines():
        if line.startswith(KIND_HEAD):
            kind = line[len(KIND_HEAD):].strip()
            if kind not in KINDS or kind in sections:
                raise ValueError(f'раздел «{line}» описаний записей: вида нет или он дважды')
            current = sections[kind] = []
            continue
        current.append(line)
    return ('\n'.join(common).strip(),
            {kind: '\n'.join(lines).strip() for kind, lines in sections.items()})


def records_text(descriptions: str, volume: list[VolumeRecord]) -> str:
    """Описания видов, которые есть в объёме, по порядку файла, с общей частью.

    Вид в объёме без описания — BriefError: подагент получил бы запись, о
    которой ему ничего не сказали.
    """
    try:
        common, sections = kind_sections(descriptions)
    except ValueError as error:
        raise BriefError(str(error)) from error
    present = {record.kind for record in volume}
    missing = sorted(present - set(sections))
    if missing:
        raise BriefError(f'нет описания записей вида {", ".join(missing)}')
    chosen = [text for kind, text in sections.items() if kind in present]
    return '\n\n'.join([common, *chosen]) if chosen else common


def read_criteria(providers: tuple[Provider, ...]
                  ) -> tuple[tuple[Criterion, ...], tuple[str, ...]]:
    """Разобранные критерии поставщиков и что не разобралось: «slug (причина)».

    Повтор id у второго модуля тоже не разобран: вердикт по id не отличил бы
    один критерий от другого. Первый модуль с этим id остаётся. Поставщик
    без обоих файлов тоже здесь: см. empty_providers.
    """
    texts, unread = gather(providers, CRITERION_FILE)
    found: list[Criterion] = []
    broken = [*empty_providers(providers), *unread]
    owners: dict[str, str] = {}
    for slug, text in texts:
        try:
            criterion = make_criterion(text)
        except ValueError as error:
            broken.append(f'{slug} ({error})')
            continue
        if criterion.id in owners:
            broken.append(f'{slug} (id «{criterion.id}» уже у модуля {owners[criterion.id]})')
            continue
        owners[criterion.id] = slug
        found.append(criterion)
    return tuple(found), tuple(broken)


def applicable(found: tuple[Criterion, ...], volume: list[VolumeRecord]
               ) -> tuple[Criterion, ...]:
    """Критерии, чьё правило совпало с объёмом.

    Теги — типы записей write из строк замка; у записи без известного типа
    берутся все критерии по тегам.
    """
    tags = selection_tags(volume)
    return tuple(criterion for criterion in found if criterion_applies(criterion, volume, tags))


def assemble_brief(sources: Sources, volume: list[VolumeRecord], frame: str,
                   descriptions: str) -> BriefParts:
    """Поручение подагенту из каркаса, описаний видов записей объёма, подходящих
    критериев и требований модулей, чьи скиллы есть в объёме.

    Не разобранные файлы критериев и требований идут в broken: их нельзя
    молча пропустить. Описания видов — свой файл ревью: их порча — BriefError.
    """
    found, broken = read_criteria(sources.providers)
    chosen = applicable(found, volume)
    requirements, requirements_broken = read_requirements(sources.modules_dir, volume)
    ids = tuple(criterion.id for criterion in chosen)
    text = (frame.replace(RECORDS_MARK, records_text(descriptions, volume))
            .replace(CRITERIA_MARK, '\n\n'.join(criterion.body for criterion in chosen))
            .replace(REQUIREMENTS_MARK, requirements_text(requirements))
            .replace(TYPES_MARK, types_text(volume))
            .replace(CRITERIA_LIST_MARK, ', '.join(f'"{item}"' for item in ids)))
    return BriefParts(text, ids, broken + requirements_broken)


def review_criteria(sources: Sources, volume: list[VolumeRecord],
                    taken: tuple[CriterionId, ...] | None) -> CriteriaCheck:
    """Критерии, по которым принимается вердикт, и почему чистый не принять.

    Критерии — из последнего поручения (`taken`). Поручения не было (None) —
    те, что подошли бы к объёму. Проблема считается по поставщикам всегда:
    файл мог сломаться и после поручения.
    """
    found, broken = read_criteria(sources.providers)
    _, requirements_broken = read_requirements(sources.modules_dir, volume)
    if taken is None:
        taken = tuple(criterion.id for criterion in applicable(found, volume))
    problem = criteria_problem(volume, taken, found, broken + requirements_broken)
    return CriteriaCheck(taken, problem)


def providers_problem(sources: Sources) -> str | None:
    """Ни у одного модуля с for = ["turn-review"] нет хуков: объём некому копить.

    Поставщики ревью не требуют и пишут объём сами, по контракту. Без этой
    проверки ревью без поставщика молча считало бы, что записей не было.
    """
    if any(provider.has_hooks for provider in sources.providers):
        return None
    return NO_PROVIDER_PROBLEM.format(modules_dir=sources.modules_dir)


def write_brief(storage, text: str) -> Path:
    """Положить поручение в зону сессии. Не записать — BriefError с путём."""
    path = storage.path(BRIEF_FILE).resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    except OSError as error:
        raise BriefError(f'поручение {path} не записать ({error})') from error
    return path
