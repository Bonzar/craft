"""Состояние замка craft-type-lock: счёт ходов, вызванные скиллы, чтения.

Файл в зоне сессии хранилища. Хуки параллельных вызовов идут параллельно,
поэтому чтение и запись идут под локом на соседний файл.
"""

import fcntl
import json
from contextlib import contextmanager

import jarvis

STATE_FILE = 'craft-type-lock.json'
# Хуки параллельных вызовов идут параллельно: два скилла в одном сообщении
# без лока перезаписали бы друг друга, и один пропал бы из счёта.
LOCK_FILE = 'craft-type-lock.lock'
TURN_KEY = 'turn'
SKILLS_KEY = 'skills'
# Страницы, которые агент читал в сессии, свежие первыми. Среди них замок
# ищет цель записи: connect-API родителя блока не отдаёт, а по правилу
# чтения до записи страница цели среди прочитанных должна быть.
READS_KEY = 'reads'
MAX_READS = 20
# Id, прочитанные в текущем ходе: по ним замок узнаёт, прочитан ли шаблон
# сущности. Реплика и сжатие их сбрасывают: шаблон из прошлого хода к
# записи уже не свеж. Потолок держит файл маленьким на длинном ходе.
TURN_READS_KEY = 'turn_reads'
MAX_TURN_READS = 200
WARNED_KEY = 'place_warned'


class BrokenFile(ValueError):
    """Файл замка испорчен: path — какой именно, его и удалять."""

    def __init__(self, path, message: str) -> None:
        super().__init__(message)
        self.path = path


def new_state(turn: int = 0, skills: dict | None = None, reads: list | None = None,
              warned: bool = False, turn_reads: list | None = None) -> dict:
    return {TURN_KEY: turn, SKILLS_KEY: dict(skills or {}), READS_KEY: list(reads or []),
            WARNED_KEY: warned, TURN_READS_KEY: list(turn_reads or [])}


def read_state(storage: jarvis.Storage, craft) -> dict:
    """Состояние замка; craft — разбор команд Craft, им проверяются id чтений."""
    path = storage.path(STATE_FILE)
    try:
        state = storage.read_json(STATE_FILE, default=None)
    except json.JSONDecodeError as error:
        raise BrokenFile(path, f'файл состояния замка {path} не JSON: {error}. '
                               'Удали его, счёт начнётся заново') from error
    if state is None:
        return new_state()
    turn = state.get(TURN_KEY) if isinstance(state, dict) else None
    skills = state.get(SKILLS_KEY) if isinstance(state, dict) else None
    reads = state.get(READS_KEY, []) if isinstance(state, dict) else None
    turn_reads = state.get(TURN_READS_KEY, []) if isinstance(state, dict) else None
    if (type(turn) is not int or not isinstance(skills, dict) or not is_id_list(reads, craft)
            or not is_id_list(turn_reads, craft)):
        raise BrokenFile(path, f'файл состояния замка {path} испорчен: {state!r}. '
                               'Удали его, счёт начнётся заново')
    return new_state(turn, skills, reads, bool(state.get(WARNED_KEY)), turn_reads)


def is_id_list(value, craft) -> bool:
    return isinstance(value, list) and all(craft.is_block_id(item) for item in value)


@contextmanager
def state_lock(storage: jarvis.Storage):
    path = storage.path(LOCK_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def remember_reads(reads: list[str], fresh: list[str], craft) -> list[str]:
    """Свежие чтения вперёд, без повторов, не длиннее MAX_READS."""
    return craft.unique_ids([*fresh, *reads])[:MAX_READS]


def remember_turn_reads(turn_reads: list[str], fresh: list[str], craft) -> list[str]:
    """Все id чтения этого хода, свежие вперёд, не длиннее MAX_TURN_READS."""
    return craft.unique_ids([*fresh, *turn_reads])[:MAX_TURN_READS]
