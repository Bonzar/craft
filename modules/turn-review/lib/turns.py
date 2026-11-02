"""Ход и объём вместе: номер хода у записи и у реплики, очистка объёма.

Здесь сходятся состояние и объём: запись берёт номер хода из состояния,
реплика открывает ход по записям объёма, очистка меняет оба файла.
"""

import hashlib
from dataclasses import replace

from .records import KIND_ANCHOR, VolumeRecord, check_record, record_row
from .state import ReviewState, current_turn, load_state, save_state, state_lock
from .verdict import drop_verdict
from .volume import (
    FINGERPRINT_CHARS,
    append_row_unlocked,
    next_turn,
    parse_volume,
    peek_volume,
    rewrite_volume,
)


def append_turn_record(storage, record: VolumeRecord, *, turn: int | None = None) -> None:
    """Запись объёма с номером хода; номер ставит только эта функция.

    Запись — одна из размеченных по kind, собранная фабрикой lib. Иное —
    ValueError до лока, и в объём ничего не ложится. Поле `turn` записи
    заменяется: иначе поставщик мог бы выдать запись за запись прошлого хода,
    и ход-разговор отпустил бы её без ревью.

    `turn` не задан — номер берётся из состояния. Реплика задаёт его сама:
    номер ей выдал claim_prompt_turn. Состояние испорчено — запись всё равно
    ложится, без номера, и ошибка пробрасывается. Запись без номера не раньше
    ни одного хода и идёт на ревью; потерянная ушла бы от ревью совсем.

    Номер читается и запись ложится под локом ревью: параллельные поставщики
    не перемешают строки, а очистка на остановке не потеряет только что
    дописанную. Звать не под state_lock: flock на новом дескрипторе не
    реентерабелен и ждал бы сам себя.
    """
    check_record(record)
    with state_lock(storage):
        if turn is None:
            try:
                turn = current_turn(storage)
            except ValueError:
                append_row_unlocked(storage, record_row(replace(record, turn=None)))
                raise
        append_row_unlocked(storage, record_row(replace(record, turn=turn)))


def prompt_mark(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:FINGERPRINT_CHARS]


def claim_prompt_turn(storage, slug: str, text: str) -> tuple[int, str | None]:
    """Номер хода этой реплики для модуля `slug` и что пришлось починить.

    Звать под state_lock на каждой реплике, даже пустой. Хуки одного события
    харнес запускает параллельно: первый пришедший модуль открывает новый ход,
    остальные берут его номер. Реплика узнаётся по отпечатку текста, а
    повтор той же реплики — по тому, что модуль уже брал номер по ней: тогда
    это новый ход. Модуль, пропустивший реплику, этот счёт бы сбил.
    """
    state, problem = load_state(storage)
    mark, taken = prompt_mark(text), state.prompt_taken
    if state.prompt_mark == mark and slug not in taken:
        save_state(storage, replace(state, prompt_taken=(*taken, slug)))
        return state.turn, problem
    turn = next_turn(state.turn, peek_volume(storage))
    save_state(storage, replace(state, turn=turn, prompt_mark=mark, prompt_taken=(slug,)))
    return turn, problem


def clear_volume(storage, state: ReviewState) -> ReviewState:
    """Очистить объём и вердикт; новое состояние — с новым id и без поручений.

    Поля отладочного вывода тоже обнуляются: итог очищенного объёма хук
    кладёт поверх уже после очистки.

    Записи якоря переходят в новый объём: якорь выбирают раз за сессию, и без
    переноса каждое следующее ревью сессии нашло бы «у сессии нет якоря».
    """
    records, _ = parse_volume(storage)
    rewrite_volume(storage, [record for record in records
                             if record.kind == KIND_ANCHOR])
    drop_verdict(storage)
    cleared = ReviewState(turn=state.turn)
    save_state(storage, cleared)
    return cleared
