"""Ход и объём вместе: номер хода по id реплики и очистка объёма.

Номер хода знает только ревью. Поставщики кладут в записи id реплики
харнеса, а хук ревью на реплике связывает этот id с номером хода в
состоянии. Очистка меняет оба файла: объём и состояние.
"""

from dataclasses import replace
from types import MappingProxyType

from .records import KIND_ANCHOR, VolumeRecord
from .state import ReviewState, load_state, save_state
from .verdict import drop_verdict
from .volume import rewrite_volume

# Поле id реплики в сыром событии Claude. Замер 02.11.2026 (Claude Code
# 2.1.316, `claude -p`): UserPromptSubmit, PreToolUse, PostToolUse и Stop одной
# реплики несут один и тот же `prompt_id`, у двух одинаковых реплик подряд он
# разный. В едином формате события такого поля нет, поэтому оно берётся из `raw`.
PROMPT_ID_FIELD = 'prompt_id'


def prompt_id_of(event) -> str | None:
    """Id реплики из сырого события харнеса; нет его или он не строка — None."""
    value = event.raw.get(PROMPT_ID_FIELD)
    return value if isinstance(value, str) and value else None


def claim_prompt_turn(storage, event) -> tuple[int, str | None]:
    """Номер хода реплики `event` и что пришлось починить. Звать под state_lock.

    Реплика с id, которого состояние ещё не знает, открывает новый ход, и id
    ложится в состояние рядом с номером: по нему ревью узнаёт ход записей
    поставщиков. Тот же id второй раз — тот же ход: хук, запущенный на реплике
    дважды, ход не делит. Без id, у старого харнеса, каждая реплика — новый
    ход, а записи поставщиков без id идут на ревью в любом ходе.
    """
    state, problem = load_state(storage)
    prompt_id = prompt_id_of(event)
    known = state.prompt_turns.get(prompt_id) if prompt_id is not None else None
    if known is not None:
        return known, problem
    turn = state.turn + 1
    prompt_turns = state.prompt_turns
    if prompt_id is not None:
        prompt_turns = MappingProxyType({**prompt_turns, prompt_id: turn})
    save_state(storage, replace(state, turn=turn, prompt_turns=prompt_turns))
    return turn, problem


def keep_after_clear(records: list[VolumeRecord]) -> list[VolumeRecord]:
    """Записи якоря переходят в новый объём: якорь выбирают раз за сессию."""
    return [record for record in records if record.kind == KIND_ANCHOR]


def clear_volume(storage, state: ReviewState) -> ReviewState:
    """Очистить объём и вердикт; новое состояние — с новым id и без поручений.

    Поля отладочного вывода тоже обнуляются: итог очищенного объёма хук
    кладёт поверх уже после очистки.

    Записи якоря переходят в новый объём: без переноса каждое следующее ревью
    сессии нашло бы «у сессии нет якоря». Из номеров по id реплик остаются
    только номера текущего хода: записи прежних ходов ушли с очисткой, а
    запись, дописанная в этом же ходе после очистки, свой ход знать должна.
    """
    rewrite_volume(storage, keep_after_clear)
    drop_verdict(storage)
    current = {prompt_id: turn for prompt_id, turn in state.prompt_turns.items()
               if turn == state.turn}
    cleared = ReviewState(turn=state.turn, prompt_turns=MappingProxyType(current))
    save_state(storage, cleared)
    return cleared
