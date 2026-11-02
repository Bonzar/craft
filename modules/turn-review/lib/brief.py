"""Сборка поручения подагенту и критерии, по которым принимается вердикт.

Каркас у ревью, записи описывают поставщики файлом data/review-records.md,
критерии привозят модули файлом data/review-criterion.md. Поручение берёт
критерии, чьё правило совпало с объёмом, и требования модулей из
data/review.md тех модулей, чьи скиллы есть в записях skill объёма.
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
from .records import SkillRecord, VolumeRecord

CRITERION_FILE = 'review-criterion.md'
RECORDS_FILE = 'review-records.md'
REQUIREMENTS_FILE = 'review.md'
DATA_DIR = 'data'
BRIEF_FILE = 'turn-review-brief.md'
CRITERIA_MARK = '{{CRITERIA}}'
RECORDS_MARK = '{{RECORDS}}'
CRITERIA_LIST_MARK = '{{CRITERIA_LIST}}'
REQUIREMENTS_MARK = '{{MODULE_REQUIREMENTS}}'
REQUIREMENTS_HEAD = '### Требования модуля {slug}'
NO_REQUIREMENTS = 'нет'
NO_PROVIDER_PROBLEM = ('нет поставщика записей ревью: ни у одного модуля каталога {modules_dir} '
                       f'нет {DATA_DIR}/{RECORDS_FILE}. Объём пуст не потому, что записей не '
                       'было, а потому, что их некому положить. Скажи об этом человеку: нужен '
                       'модуль-поставщик, например review-craft-calls')


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


def gather(modules_dir, filename: str) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Файлы данных с этим именем у всех модулей каталога: (slug папки, текст).

    Второе — файлы, которые не прочитались, «slug (путь не читается: ошибка)»:
    молча пропустить их нельзя, а падение хука отпустило бы ход без ревью.
    """
    found, broken = [], []
    for child in sorted(Path(modules_dir).iterdir()):
        path = child / DATA_DIR / filename
        if not child.is_dir() or not path.is_file():
            continue
        text, problem = read_data_file(child.name, path)
        if problem is None:
            found.append((child.name, text))
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


def read_criteria(modules_dir) -> tuple[tuple[Criterion, ...], tuple[str, ...]]:
    """Разобранные критерии модулей и что не разобралось: «slug (причина)».

    Повтор id у второго модуля тоже не разобран: вердикт по id не отличил бы
    один критерий от другого. Первый модуль с этим id остаётся.
    """
    texts, unread = gather(modules_dir, CRITERION_FILE)
    found: list[Criterion] = []
    broken = list(unread)
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


def assemble_brief(modules_dir, volume: list[VolumeRecord], frame: str) -> BriefParts:
    """Поручение подагенту из каркаса, записей поставщиков, подходящих критериев
    и требований модулей, чьи скиллы есть в объёме.

    Не разобранные файлы критериев, записей и требований идут в broken: их
    нельзя молча пропустить.
    """
    found, broken = read_criteria(modules_dir)
    chosen = [criterion for criterion in found if criterion_applies(criterion, volume)]
    records, records_broken = gather(modules_dir, RECORDS_FILE)
    requirements, requirements_broken = read_requirements(modules_dir, volume)
    ids = tuple(criterion.id for criterion in chosen)
    text = (frame.replace(RECORDS_MARK, '\n\n'.join(text.strip() for _, text in records))
            .replace(CRITERIA_MARK, '\n\n'.join(criterion.body for criterion in chosen))
            .replace(REQUIREMENTS_MARK, requirements_text(requirements))
            .replace(CRITERIA_LIST_MARK, ', '.join(f'"{item}"' for item in ids)))
    return BriefParts(text, ids, broken + records_broken + requirements_broken)


def review_criteria(modules_dir, volume: list[VolumeRecord],
                    taken: tuple[CriterionId, ...] | None) -> CriteriaCheck:
    """Критерии, по которым принимается вердикт, и почему чистый не принять.

    Критерии — из последнего поручения (`taken`). Поручения не было (None) —
    те, что подошли бы к объёму. Проблема считается по каталогу модулей
    всегда: файл мог сломаться и после поручения.
    """
    found, broken = read_criteria(modules_dir)
    _, records_broken = gather(modules_dir, RECORDS_FILE)
    _, requirements_broken = read_requirements(modules_dir, volume)
    if taken is None:
        taken = tuple(criterion.id for criterion in found if criterion_applies(criterion, volume))
    return CriteriaCheck(taken, criteria_problem(volume, taken, found,
                                                 broken + records_broken + requirements_broken))


def providers_problem(modules_dir) -> str | None:
    """Ни у одного модуля нет описания записей: объём некому копить.

    turn-review поставщиков не требует, требуют они его. Без этой проверки
    ревью без поставщика молча считало бы, что записей не было.
    """
    records, broken = gather(modules_dir, RECORDS_FILE)
    if records or broken:
        return None
    return NO_PROVIDER_PROBLEM.format(modules_dir=modules_dir)


def write_brief(storage, text: str) -> Path:
    """Положить поручение в зону сессии. Не записать — BriefError с путём."""
    path = storage.path(BRIEF_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    except OSError as error:
        raise BriefError(f'поручение {path} не записать ({error})') from error
    return path
