"""Состояние ревью сессии: тип с проверкой полей, файл и лок.

Состояние — номер хода, номера ходов по id реплик, id объёма, счёт
поручений, критерии последнего поручения, а при отладочном выводе ещё время первого поручения и итог ревью
для показа. Поля проверяет сам тип при каждой сборке: на чтении с диска, в
dataclasses.replace и до записи. Поэтому save_state не запишет то, что
load_state потом отвергнет.

Все файлы ревью трогаются под одним локом: хуки параллельных вызовов идут
параллельно, и без лока запись в объём могла бы лечь в уже очищенный файл
или пропасть между чтением и очисткой.
"""

import fcntl
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import NewType

from .criteria import CriterionId

STATE_FILE = 'turn-review-state.json'
LOCK_FILE = 'turn-review.lock'

VolumeId = NewType('VolumeId', str)

# Критерии последнего собранного поручения: по ним принимается вердикт.
# NO_BRIEF — поручения ещё не было, пустой кортеж — поручение без критериев.
BriefCriteria = tuple[CriterionId, ...] | None
NO_BRIEF: BriefCriteria = None

# Без этих полей файл — порча, остальные получают умолчание.
REQUIRED_FIELDS = ('turn', 'volume_id', 'nudges', 'released')
# Поля-кортежи в JSON лежат списками.
TUPLE_FIELDS = ('criteria', 'last_remarks')
# Поля-словари в JSON лежат объектами, в состоянии они только для чтения.
MAPPING_FIELDS = ('prompt_turns',)
UNKNOWN_FIELD = 'unknown'


def new_volume_id() -> VolumeId:
    return VolumeId(uuid.uuid4().hex)


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_texts(value) -> bool:
    return isinstance(value, tuple) and all(isinstance(item, str) for item in value)


def _is_optional_text(value) -> bool:
    return value is None or isinstance(value, str)


def _is_prompt_turns(value) -> bool:
    return isinstance(value, Mapping) and all(
        isinstance(key, str) and key and _is_count(turn) for key, turn in value.items())


def _is_unknown(value) -> bool:
    """Незнакомые поля не перекрывают известные: иначе to_json затёр бы проверенное."""
    known = {item.name for item in fields(ReviewState)}
    return isinstance(value, Mapping) and not set(value) & known


CHECKS = (
    ('turn', _is_count, 'целое число'),
    ('volume_id', lambda value: isinstance(value, str) and bool(value), 'непустая строка'),
    ('nudges', _is_count, 'целое число'),
    ('released', lambda value: isinstance(value, bool), 'true или false'),
    ('criteria', lambda value: value is NO_BRIEF or _is_texts(value), 'кортеж строк или null'),
    # Элемент не того типа уронил бы хук там, где поле читают, например в
    # тексте вопроса человеку.
    ('last_remarks', lambda value: isinstance(value, tuple)
     and all(isinstance(item, dict) for item in value), 'кортеж объектов'),
    ('prompt_turns', _is_prompt_turns, 'объект «id реплики → номер хода»'),
    ('first_nudge_at', _is_optional_text, 'строка или null'),
    ('summary', _is_optional_text, 'строка или null'),
    ('summary_shown', lambda value: isinstance(value, bool), 'true или false'),
    ('shown_verdict', _is_optional_text, 'строка или null'),
    (UNKNOWN_FIELD, lambda value: _is_unknown(value), 'объект без известных полей'),
)


@dataclass(frozen=True, kw_only=True)
class ReviewState:
    """Состояние ревью. Проверка полей одна, в __post_init__: не по типу — ValueError."""

    turn: int = 0
    volume_id: VolumeId = field(default_factory=new_volume_id)
    nudges: int = 0
    released: bool = False
    criteria: BriefCriteria = NO_BRIEF
    # Замечания последнего вердикта с замечаниями: вопрос человеку показывает их.
    last_remarks: tuple[dict, ...] = ()
    # Номер хода по id реплики харнеса: поставщики кладут в записи id, а не
    # номер, и ход записи ревью узнаёт здесь.
    prompt_turns: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    # Поля отладочного вывода. Пишутся только при включённом выводе.
    first_nudge_at: str | None = None
    summary: str | None = None
    summary_shown: bool = False
    # Какой вердикт уже показан превью до stop: id объёма и отпечаток записей write.
    shown_verdict: str | None = None
    # Поля с диска, которых этот код не знает: пишутся обратно как были.
    unknown: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for name, is_valid, expected in CHECKS:
            value = getattr(self, name)
            if not is_valid(value):
                raise ValueError(f'поле «{name}» не {expected}: {value!r}')

    @classmethod
    def from_json(cls, data) -> 'ReviewState':
        """Состояние из JSON файла. Не объект, нет обязательного поля или поле не по типу — ValueError."""
        if not isinstance(data, dict):
            # ValueError: _state_damage называет любую порчу файла одной ошибкой.
            raise ValueError('не объект')  # noqa: TRY004
        missing = [name for name in REQUIRED_FIELDS if name not in data]
        if missing:
            raise ValueError(f'нет полей {", ".join(missing)}')
        known = {item.name for item in fields(cls)} - {UNKNOWN_FIELD}
        values = {name: _from_json_value(name, value) for name, value in data.items()
                  if name in known}
        unknown = {name: value for name, value in data.items() if name not in known}
        return cls(**values, unknown=MappingProxyType(unknown))

    def to_json(self) -> dict:
        data = {item.name: _to_json_value(getattr(self, item.name)) for item in fields(self)
                if item.name != UNKNOWN_FIELD}
        return {**data, **self.unknown}


def _from_json_value(name: str, value):
    """Список JSON — в кортеж, объект — в словарь только для чтения; иное отвергнет проверка."""
    if name in TUPLE_FIELDS and isinstance(value, list):
        return tuple(value)
    if name in MAPPING_FIELDS and isinstance(value, dict):
        return MappingProxyType(dict(value))
    return value


def _to_json_value(value):
    if isinstance(value, tuple):
        return list(value)
    return dict(value) if isinstance(value, MappingProxyType) else value


@contextmanager
def state_lock(storage):
    path = storage.path(LOCK_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _state_damage(storage) -> tuple[ReviewState | None, str | None]:
    """Состояние с диска и что с ним не так. Файла нет — (None, None).

    Пустой файл — порча, а не отсутствие: состояние пишется атомарно, и
    пустым его оставить могла только чужая рука.
    """
    if not storage.path(STATE_FILE).exists():
        return None, None
    try:
        data = storage.read_json(STATE_FILE, default=None)
    except ValueError as error:
        # ValueError ловит и битый JSON, и байты не в UTF-8.
        return None, f'не JSON ({error})'
    if data is None:
        return None, 'пуст'
    try:
        return ReviewState.from_json(data), None
    except ValueError as error:
        return None, f'испорчен ({error}): {data!r}'


def peek_state(storage) -> ReviewState | None:
    """Состояние без починки: None, если файла нет или он испорчен.

    Показ ответа идёт до остановки, а починку с её уведомлением делает stop.
    Отсутствие от порчи здесь не отличается: в обоих случаях ответ спрятан.
    """
    state, _ = _state_damage(storage)
    return state


def load_state(storage) -> tuple[ReviewState, str | None]:
    """Состояние сессии и что пришлось починить.

    Первого нет — заводится и сразу пишется: поручение называет id объёма
    подагенту, и вердикт должен застать тот же id, а не свежесгенерированный.
    Испорченное заводится заново с новым id: старый вердикт чужим и станет, а
    ревью пойдёт по поручению. Без починки каждая остановка упиралась бы в тот
    же файл, и выйти можно было бы только его удалением.
    """
    state, damage = _state_damage(storage)
    if state is not None:
        return state, None
    fresh = ReviewState()
    save_state(storage, fresh)
    if damage is None:
        return fresh, None
    return fresh, f'файл состояния ревью {storage.path(STATE_FILE)} {damage}: заведён заново'


def save_state(storage, state: ReviewState) -> None:
    """Записать состояние. Не ReviewState — ValueError: на диск идёт только проверенное."""
    if not isinstance(state, ReviewState):
        # ValueError, как у порчи на чтении: правило записи и чтения одно.
        raise ValueError(f'состояние ревью не ReviewState: {state!r}')  # noqa: TRY004
    storage.write_json(STATE_FILE, state.to_json())
