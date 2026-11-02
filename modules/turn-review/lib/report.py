"""Итог ревью для показа человеку под ответом: вердикт, объём, поручения и время.

Только текст: когда его показывать и куда класть, решает хук. Время «сейчас»
хук передаёт сам, поэтому итог считается без часов внутри.
"""

from datetime import datetime

from .question import MAX_NUDGES
from .records import KIND_READ, VolumeRecord
from .state import ReviewState
from .verdict import CHECKED_KEY, CLEAN_KEY, CRITERION_KEY, REMARKS_KEY, TEXT_KEY
from .volume import count_kind, count_writes

REPORT_TITLE = 'Ревью хода:'
REPORT_CLEAN = 'чисто.'
REPORT_RELEASED = 'без ревью, по решению человека.'
REPORT_REMARKS_SHOWN = 3
REPORT_REMARK_CHARS = 60


def shortened(text: str) -> str:
    if len(text) <= REPORT_REMARK_CHARS:
        return text
    return text[:REPORT_REMARK_CHARS].rstrip() + '…'


def remarks_head(remarks: list[dict]) -> str:
    shown = [f'[{item[CRITERION_KEY]}] {shortened(item[TEXT_KEY])}'
             for item in remarks[:REPORT_REMARKS_SHOWN]]
    rest = len(remarks) - len(shown)
    tail = f'; и ещё {rest}' if rest else ''
    return f'замечания ({len(remarks)}): {"; ".join(shown)}{tail}.'


def verdict_head(verdict: dict) -> str:
    if verdict[CLEAN_KEY]:
        return REPORT_CLEAN
    return remarks_head(verdict[REMARKS_KEY])


def elapsed_text(state: ReviewState, now: datetime) -> str:
    # Счёт выше MAX_NUDGES — метка заданного вопроса, а не поручение.
    nudges = min(state.nudges, MAX_NUDGES)
    started = state.first_nudge_at
    if not nudges:
        return 'Поручений не было.'
    if started is None:
        return f'Поручений {nudges}, время первого не записано.'
    try:
        seconds = int((now - datetime.fromisoformat(started)).total_seconds())
    except (ValueError, TypeError) as error:
        return f'Поручений {nudges}, время первого не читается ({started!r}: {error}).'
    return f'Поручений {nudges}, с первого прошло {seconds} с.'


def summary_text(head: str, state: ReviewState, volume: list[VolumeRecord],
                 verdict: dict | None, now: datetime) -> str:
    """Итог ревью для показа: 3 строки — вердикт, объём, поручения и время."""
    counts = f'Записей write {count_writes(volume)}, read {count_kind(volume, KIND_READ)}.'
    if verdict is not None:
        counts = f'Критерии: {", ".join(verdict[CHECKED_KEY])}. {counts}'
    return f'{REPORT_TITLE} {head}\n{counts}\n{elapsed_text(state, now)}'
