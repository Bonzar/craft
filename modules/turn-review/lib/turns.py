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


# Поле id реплики в сыром событии Claude. Замер 02.11.2026 (Claude Code
# 2.1.316, `claude -p`): UserPromptSubmit несёт `prompt_id`, у двух хуков
# одной реплики он один, у двух одинаковых реплик подряд — разный. В едином
# формате события такого поля нет, поэтому оно берётся из `raw`.
PROMPT_ID_FIELD = 'prompt_id'
# Метка реплики по id не совпадёт с отпечатком текста: в отпечатке только hex.
PROMPT_ID_MARK = 'id:'


def prompt_mark(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:FINGERPRINT_CHARS]


def prompt_id_of(event) -> str | None:
    """Id реплики из сырого события харнеса; нет его или он не строка — None."""
    value = event.raw.get(PROMPT_ID_FIELD)
    return value if isinstance(value, str) and value else None


def claim_prompt_turn(storage, slug: str, event) -> tuple[int, str | None]:
    """Номер хода реплики `event` для модуля `slug` и что пришлось починить.

    Звать под state_lock на каждой реплике, даже пустой. Хуки одного события
    харнес запускает параллельно: первый пришедший модуль открывает новый ход,
    остальные берут его номер.

    Реплика узнаётся по id, который даёт харнес: тот же id — тот же ход, кто
    бы из модулей ни пришёл и сколько бы раз. Поэтому счёт не зависит от того,
    какие модули включены и не пропустил ли кто реплику.

    Id нет — запасной путь по тексту: реплика узнаётся по отпечатку, а повтор
    той же реплики — по тому, что модуль уже брал номер по ней. Тут модуль,
    включённый или пропустивший реплику между двумя одинаковыми, счёт собьёт.
    """
    state, problem = load_state(storage)
    prompt_id, taken = prompt_id_of(event), state.prompt_taken
    if prompt_id is not None:
        mark = PROMPT_ID_MARK + prompt_id
        is_same = state.prompt_mark == mark
    else:
        mark = prompt_mark(event.prompt_text or '')
        is_same = state.prompt_mark == mark and slug not in taken
    if is_same:
        if slug not in taken:
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
