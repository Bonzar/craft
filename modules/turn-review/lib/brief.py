"""Сборка поручения подагенту и критерии, по которым принимается вердикт.

Каркас у ревью, записи описывают поставщики файлом data/review-records.md,
критерии привозят модули файлом data/review-criterion.md. Поручение берёт
критерии, чьё правило совпало с объёмом.
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

CRITERION_FILE = 'review-criterion.md'
RECORDS_FILE = 'review-records.md'
DATA_DIR = 'data'
BRIEF_FILE = 'turn-review-brief.md'
CRITERIA_MARK = '{{CRITERIA}}'
RECORDS_MARK = '{{RECORDS}}'
CRITERIA_LIST_MARK = '{{CRITERIA_LIST}}'
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
        try:
            found.append((child.name, path.read_text(encoding='utf-8')))
        except (OSError, UnicodeDecodeError) as error:
            broken.append(f'{child.name} ({path} не читается: {error})')
    return tuple(found), tuple(broken)


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


def assemble_brief(modules_dir, volume: list[dict], frame: str) -> BriefParts:
    """Поручение подагенту из каркаса, записей поставщиков и подходящих критериев.

    Не разобранные файлы критериев и записей идут в broken: их нельзя
    молча пропустить.
    """
    found, broken = read_criteria(modules_dir)
    chosen = [criterion for criterion in found if criterion_applies(criterion, volume)]
    records, records_broken = gather(modules_dir, RECORDS_FILE)
    ids = tuple(criterion.id for criterion in chosen)
    text = (frame.replace(RECORDS_MARK, '\n\n'.join(text.strip() for _, text in records))
            .replace(CRITERIA_MARK, '\n\n'.join(criterion.body for criterion in chosen))
            .replace(CRITERIA_LIST_MARK, ', '.join(f'"{item}"' for item in ids)))
    return BriefParts(text, ids, broken + records_broken)


def review_criteria(modules_dir, volume: list[dict],
                    taken: tuple[CriterionId, ...] | None) -> CriteriaCheck:
    """Критерии, по которым принимается вердикт, и почему чистый не принять.

    Критерии — из последнего поручения (`taken`). Поручения не было (None) —
    те, что подошли бы к объёму. Проблема считается по каталогу модулей
    всегда: файл мог сломаться и после поручения.
    """
    found, broken = read_criteria(modules_dir)
    _, records_broken = gather(modules_dir, RECORDS_FILE)
    if taken is None:
        taken = tuple(criterion.id for criterion in found if criterion_applies(criterion, volume))
    return CriteriaCheck(taken, criteria_problem(volume, taken, found, broken + records_broken))


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
