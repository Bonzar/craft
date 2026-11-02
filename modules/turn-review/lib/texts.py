"""Тексты, которыми хук возвращает ход агенту: поручение, замечания, сбой поручения.

Только сборка строк: пути, счёт и критерии хук передаёт готовыми. Проблемы
приёма склеиваются здесь же, одним разделителем на stop и превью.
"""

from typing import NamedTuple

from .verdict import CRITERION_KEY, TEXT_KEY, WHERE_KEY

PROBLEM_SEPARATOR = '. '


class NudgeFacts(NamedTuple):
    """Что поручение называет агенту: пути, id объёма и его счёт."""
    brief: str
    volume_file: str
    verdict_file: str
    volume_id: str
    fingerprint_command: str
    writes: int
    fingerprint: str
    criteria: tuple[str, ...]


def problem_prefix(problem: str | None) -> str:
    return f'{problem[0].upper()}{problem[1:]}. ' if problem else ''


def with_problem(reason: str, problem: str | None) -> str:
    return f'{reason}{PROBLEM_SEPARATOR}{problem}' if problem else reason


def joined(*problems: str | None) -> str | None:
    return PROBLEM_SEPARATOR.join(problem for problem in problems if problem) or None


def remark_line(item: dict) -> str:
    """Замечание вердикта строкой; поле без значения — None, а не падение."""
    return f'- [{item.get(CRITERION_KEY)}] {item.get(WHERE_KEY)}: {item.get(TEXT_KEY)}'


def nudge_text(facts: NudgeFacts, problem: str | None) -> str:
    """Блок с просьбой запустить подагента по собранному поручению."""
    return (
        f'{problem_prefix(problem)}Ход не отпущен: ревью хода не сделано. Запусти подагента '
        f'инструментом Agent (subagent_type general-purpose) не в фоне, дождись его ответа '
        f'тем же ходом. Поручение подагенту лежит в файле {facts.brief}, передай ему пути: '
        f'объём {facts.volume_file}, вердикт {facts.verdict_file}, '
        f'volume_id {facts.volume_id}, команда отпечатка '
        f'«{facts.fingerprint_command}»; записей write в объёме сейчас '
        f'{facts.writes}, отпечаток {facts.fingerprint}, критерии '
        f'{", ".join(facts.criteria) or "нет"}. Ответ до чистого вердикта человеку не показан: '
        f'напиши его заново после вердикта.'
    )


def brief_failure_text(problem: str | None, error: Exception) -> str:
    """Блок, когда поручение не собрать: подагенту нечего дать, хук не падает."""
    return (f'{problem_prefix(problem)}Ход не отпущен: ревью хода не сделано, {error}. '
            'Скажи об этом человеку и закончи ход снова.')


def remarks_text(remarks: list[dict], problem: str | None = None) -> str:
    lines = [f'- [{item[CRITERION_KEY]}] {item[WHERE_KEY]}: {item[TEXT_KEY]}' for item in remarks]
    return (f'{problem_prefix(problem)}Ход не отпущен: ревью хода нашло замечания.\n'
            + '\n'.join(lines) + '\nПочини записи и снова закончи ход: хук попросит новое ревью.')
