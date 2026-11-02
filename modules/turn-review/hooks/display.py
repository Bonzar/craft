"""Показ ответа человеку до вердикта: прятать ли ответ и когда класть итог ревью.

Модель этот текст не видит: меняется только показ. Текст итога собирает lib,
здесь решения хука — по хранилищу, флагу отладки и входу события.
"""

from dataclasses import replace
from datetime import datetime, timezone

import jarvis
from debug_output import debug_on, read_debug
from review_access import required_criteria

# дока, MessageDisplay: `final` — последний кусок сообщения.
FINAL_FIELD = 'final'
REPORT_SEPARATOR = '\n\n---\n'
# Ответ за ход с записями человек читает только после чистого вердикта.
HIDDEN_TEXT = 'Ревью хода идёт, отчёт после него.'
DEBUG_OFF_REASON = 'отладочный вывод выключен'
NOT_FINAL_REASON = 'не последний кусок сообщения'


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def is_final(event) -> bool:
    return bool(event.raw.get(FINAL_FIELD))


def displayed(base: str, report: str) -> jarvis.Response:
    """Показанный текст ответа и под ним отчёт."""
    return jarvis.UpdatedDisplay(text=f'{base}{REPORT_SEPARATOR}{report}')


def display_gate(event, review) -> str | None:
    """Причина не показывать итог под ответом; показывать — None."""
    enabled, problem = read_debug()
    if not enabled:
        return review.with_problem(DEBUG_OFF_REASON, problem)
    if not is_final(event):
        return NOT_FINAL_REASON
    return None


def answer_hidden(storage, state, runtime, review) -> bool:
    """Прятать ли ответ: ход писал в Craft, а чистого вердикта по объёму нет.

    Вердикт с замечаниями или по другому объёму ответ не открывает: ход
    закончится блоком, и ответ придёт заново после чистого вердикта.
    Разрешение человека закончить без ревью открывает ответ. Состояние или
    строка объёма не читаются — ответ спрятан, даже без записей: починку и
    её уведомление делает stop.
    """
    volume, damage = review.parse_volume_file(storage.path(review.VOLUME_FILE))
    if state is None or damage:
        return True
    if not review.count_writes(volume) or review.writes_before_turn(volume, state.turn):
        return False
    if state.released:
        return False
    criteria, problem = required_criteria(state, runtime, review, volume)
    verdict = review.peek_verdict(storage, state.volume_id, criteria, problem)
    return not (verdict is not None and verdict[review.CLEAN_KEY]
                and verdict.get(review.FINGERPRINT_KEY) == review.fingerprint(volume))


def note_summary(storage, saved, before, review, volume: list, head: str,
                 verdict: dict | None = None) -> None:
    """Положить итог ревью в состояние для показа; вывод выключен — ничего.

    `before` — состояние до решения: в нём счёт поручений, время первого и
    показанный превью вердикт. `saved` — состояние после решения, итог ложится
    поверх него. Вердикт уже показан превью — итог сразу помечен показанным.
    """
    if not debug_on():
        return
    shown = verdict is not None and before.shown_verdict == review.verdict_key(verdict)
    summary = review.summary_text(head, before, volume, verdict, now_utc())
    review.save_state(storage, replace(saved, summary=summary, summary_shown=shown))


def first_nudge_at(state) -> str | None:
    """Время первого поручения объёма, UTC; пишется только при отладочном выводе."""
    if state.first_nudge_at is not None or not debug_on():
        return state.first_nudge_at
    return now_utc().isoformat()
