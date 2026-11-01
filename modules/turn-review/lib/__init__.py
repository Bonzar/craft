"""Хранилище ревью хода: объём, состояние и вердикт в зоне сессии.

Объём — всё, что ход сделал с Craft с последнего чистого вердикта или
разрешения Влада: по строке на событие. Состояние — номер хода, id объёма и
счёт поручений, а при отладочном выводе ещё время первого поручения и итог
ревью для показа. Вердикт пишет подагент, хук только читает его и убирает.

Все три файла трогаются под одним локом: хуки параллельных вызовов идут
параллельно, и без лока запись в объём могла бы лечь в уже очищенный файл
или пропасть между чтением и очисткой.
"""

import fcntl
import hashlib
import json
import os
import uuid
from contextlib import contextmanager

VOLUME_FILE = 'turn-review-volume.jsonl'
STATE_FILE = 'turn-review-state.json'
VERDICT_FILE = 'turn-review-verdict.json'
LOCK_FILE = 'turn-review.lock'

TURN_KEY = 'turn'
VOLUME_ID_KEY = 'volume_id'
NUDGES_KEY = 'nudges'
RELEASED_KEY = 'released'
# Поля отладочного вывода. Пишутся только при включённом выводе, поэтому у
# каждого есть пустое значение по умолчанию.
FIRST_NUDGE_KEY = 'first_nudge_at'
SUMMARY_KEY = 'summary'
SUMMARY_SHOWN_KEY = 'summary_shown'
# Какой вердикт уже показан превью до stop: id объёма и отпечаток записей write.
SHOWN_VERDICT_KEY = 'shown_verdict'

# Поля строки объёма. Их читает и бриф подагента, поэтому имена одни на всех.
KIND_KEY = 'kind'
RECORD_TURN_KEY = 'turn'
TOOL_KEY = 'tool'
COMMAND_KEY = 'command'
RESULT_KEY = 'result'
TYPES_KEY = 'types'
IDS_KEY = 'ids'
SKILL_KEY = 'skill'
ANSWER_KEY = 'answer'

KIND_WRITE = 'write'
KIND_READ = 'read'
KIND_ANCHOR = 'anchor'
KIND_SKILL = 'skill'
# Слово Влада: реплика и ответ на вопрос кнопками. По ним подагент снимает
# замечание, которое Влад уже решил сам.
KIND_PROMPT = 'prompt'
KIND_ANSWER = 'answer'
PROMPT_TEXT_KEY = 'text'
QUESTION_KEY = 'question'

CLEAN_KEY = 'clean'
REMARKS_KEY = 'remarks'
CHECKED_KEY = 'checked'
# Отпечаток записей write, которые проверил подагент. Id объёма меняется
# только при очистке, и без отпечатка чистый вердикт отпустил бы и запись,
# сделанную уже после ревью. Отпечаток, а не число записей: он говорит, что
# именно проверено, а не сколько.
FINGERPRINT_KEY = 'fingerprint'
FINGERPRINT_CHARS = 16
CRITERIA = ('craft', 'consistency', 'anchor')
CRITERION_KEY = 'criterion'
WHERE_KEY = 'where'
TEXT_KEY = 'text'
REMARK_FIELDS = (CRITERION_KEY, WHERE_KEY, TEXT_KEY)


def new_volume_id() -> str:
    return uuid.uuid4().hex


def new_state(turn: int = 0, volume_id: str | None = None, nudges: int = 0,
              released: bool = False, first_nudge_at: str | None = None,
              summary: str | None = None, summary_shown: bool = False,
              shown_verdict: str | None = None) -> dict:
    return {TURN_KEY: turn, VOLUME_ID_KEY: volume_id or new_volume_id(),
            NUDGES_KEY: nudges, RELEASED_KEY: released, FIRST_NUDGE_KEY: first_nudge_at,
            SUMMARY_KEY: summary, SUMMARY_SHOWN_KEY: summary_shown,
            SHOWN_VERDICT_KEY: shown_verdict}


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


def _is_valid_debug_fields(state: dict) -> bool:
    """Поля отладочного вывода: нет поля — законно, есть — нужного типа."""
    return (isinstance(state.get(FIRST_NUDGE_KEY), (str, type(None)))
            and isinstance(state.get(SUMMARY_KEY), (str, type(None)))
            and isinstance(state.get(SHOWN_VERDICT_KEY), (str, type(None)))
            and isinstance(state.get(SUMMARY_SHOWN_KEY, False), bool))


def _is_valid_state(state) -> bool:
    if not isinstance(state, dict):
        return False
    return (isinstance(state.get(TURN_KEY), int)
            and isinstance(state.get(VOLUME_ID_KEY), str) and bool(state[VOLUME_ID_KEY])
            and isinstance(state.get(NUDGES_KEY), int)
            and isinstance(state.get(RELEASED_KEY), bool)
            and _is_valid_debug_fields(state))


def _state_damage(storage) -> tuple[dict | None, str | None]:
    """Состояние с диска и что с ним не так. Файла нет — (None, None)."""
    try:
        state = storage.read_json(STATE_FILE, default=None)
    except ValueError as error:
        # ValueError ловит и битый JSON, и байты не в UTF-8.
        return None, f'не JSON ({error})'
    if state is None or _is_valid_state(state):
        return state, None
    return None, f'испорчен: {state!r}'


def load_state(storage) -> tuple[dict, str | None]:
    """Состояние сессии и что пришлось починить.

    Первого нет — заводится и сразу пишется: поручение называет id объёма
    подагенту, и вердикт должен застать тот же id, а не свежесгенерированный.
    Испорченное заводится заново с новым id: старый вердикт чужим и станет, а
    ревью пойдёт по поручению. Без починки каждая остановка упиралась бы в тот
    же файл, и выйти можно было бы только его удалением.
    """
    state, damage = _state_damage(storage)
    if state is not None:
        return new_state(state[TURN_KEY], state[VOLUME_ID_KEY], state[NUDGES_KEY],
                         state[RELEASED_KEY], state.get(FIRST_NUDGE_KEY),
                         state.get(SUMMARY_KEY), state.get(SUMMARY_SHOWN_KEY, False),
                         state.get(SHOWN_VERDICT_KEY)), None
    fresh = new_state()
    save_state(storage, fresh)
    if damage is None:
        return fresh, None
    return fresh, f'файл состояния ревью {storage.path(STATE_FILE)} {damage}: заведён заново'


def save_state(storage, state: dict) -> None:
    storage.write_json(STATE_FILE, state)


def append_record(storage, record: dict) -> None:
    storage.append_line(VOLUME_FILE, json.dumps(record, ensure_ascii=False))


def _parse_volume(storage) -> tuple[list[dict], list[str]]:
    """Строки объёма, которые читаются, и номера тех, что не читаются."""
    return parse_volume_file(storage.path(VOLUME_FILE))


def parse_volume_file(path) -> tuple[list[dict], list[str]]:
    """Разбор файла объёма по байтам: хук и скрипт отпечатка читают его одной функцией."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return [], []
    records, damage = [], []
    for number, line in enumerate(data.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line.decode('utf-8'))
        except ValueError as error:
            damage.append(f'строка {number}: {error}')
            continue
        if not isinstance(record, dict):
            damage.append(f'строка {number}: не объект')
            continue
        records.append(record)
    return records, damage


def _rewrite_volume(storage, records: list[dict]) -> None:
    path = storage.path(VOLUME_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.tmp-{os.getpid()}')
    tmp.write_text(''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records),
                   encoding='utf-8')
    os.replace(tmp, path)


def peek_volume(storage) -> list[dict]:
    """Читаемые строки объёма без починки файла: починка и её след — дело stop."""
    return _parse_volume(storage)[0]


def load_volume(storage) -> tuple[list[dict], str | None]:
    """Объём и что в нём пропало.

    Нечитаемые строки убираются, остальное идёт на ревью как было. Что было в
    убранных строках, уже не узнать: это называется человеку, а не прячется.
    """
    records, damage = _parse_volume(storage)
    if not damage:
        return records, None
    _rewrite_volume(storage, records)
    return records, (f'объём ревью {storage.path(VOLUME_FILE)} был испорчен '
                     f'({"; ".join(damage)}): эти строки убраны, их ревью не было')


def count_kind(volume: list[dict], kind: str) -> int:
    return sum(1 for record in volume if record.get(KIND_KEY) == kind)


def count_writes(volume: list[dict]) -> int:
    return count_kind(volume, KIND_WRITE)


def writes_before_turn(volume: list[dict], turn: int) -> bool:
    """Все записи объёма сделаны ходами раньше этого: этот ход в Craft не писал.

    Номер хода потерян вместе с состоянием — он снова 0, и ни одна запись
    не раньше него: тишины нет. Запись без номера хода тоже не раньше.
    """
    turns = [record.get(RECORD_TURN_KEY) for record in volume
             if record.get(KIND_KEY) == KIND_WRITE]
    return all(isinstance(item, int) and item < turn for item in turns)


def fingerprint(volume: list[dict]) -> str:
    """Отпечаток записей `write` объёма: одна и та же строка у хука и у подагента.

    Считается по записям в каноническом виде, а не по байтам файла: починка
    объёма переписывает файл, а записи в нём те же.
    """
    lines = [json.dumps(record, ensure_ascii=False, sort_keys=True)
             for record in volume if record.get(KIND_KEY) == KIND_WRITE]
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:FINGERPRINT_CHARS]


def drop_verdict(storage) -> None:
    storage.path(VERDICT_FILE).unlink(missing_ok=True)


def clear_volume(storage, state: dict) -> dict:
    """Очистить объём и вердикт; новое состояние — с новым id и без поручений.

    Поля отладочного вывода тоже обнуляются: итог очищенного объёма хук
    кладёт поверх уже после очистки.

    Записи якоря переходят в новый объём: якорь выбирают раз за сессию, и без
    переноса каждое следующее ревью сессии нашло бы «у сессии нет якоря».
    """
    records, _ = _parse_volume(storage)
    _rewrite_volume(storage, [record for record in records
                              if record.get(KIND_KEY) == KIND_ANCHOR])
    drop_verdict(storage)
    cleared = new_state(state[TURN_KEY])
    save_state(storage, cleared)
    return cleared


def _remark_problem(remark) -> str | None:
    if not isinstance(remark, dict):
        return f'замечание {remark!r} не объект'
    for field_name in REMARK_FIELDS:
        if not isinstance(remark.get(field_name), str) or not remark[field_name].strip():
            return f'у замечания нет поля «{field_name}» строкой'
    if remark[CRITERION_KEY] not in CRITERIA:
        return f'критерий «{remark[CRITERION_KEY]}» не из {", ".join(CRITERIA)}'
    return None


def _checked_problem(checked: list, clean: bool) -> str | None:
    unknown = [str(item) for item in checked if item not in CRITERIA]
    if unknown:
        return f'в «{CHECKED_KEY}» критерии не из {", ".join(CRITERIA)}: {", ".join(unknown)}'
    missing = [item for item in CRITERIA if item not in checked]
    if clean and missing:
        return f'clean=true, а критерии не пройдены: {", ".join(missing)}'
    return None


def _fingerprint_problem(verdict: dict, expected: str) -> str | None:
    checked = verdict.get(FINGERPRINT_KEY)
    if not isinstance(checked, str) or not checked.strip():
        return f'поле «{FINGERPRINT_KEY}» не строка'
    if checked.strip() != expected:
        return (f'отпечаток вердикта {checked.strip()} не сходится с объёмом {expected}: '
                'объём менялся после ревью')
    return None


def verdict_problem(verdict: dict, expected: str) -> str | None:
    """Что не так с вердиктом нашего объёма. Всё по форме — None."""
    clean, remarks, checked = (verdict.get(CLEAN_KEY), verdict.get(REMARKS_KEY),
                               verdict.get(CHECKED_KEY))
    if not isinstance(clean, bool):
        return f'поле «{CLEAN_KEY}» не true и не false'
    if not isinstance(remarks, list):
        return f'поле «{REMARKS_KEY}» не список'
    if not isinstance(checked, list):
        return f'поле «{CHECKED_KEY}» не список'
    for remark in remarks:
        problem = _remark_problem(remark)
        if problem:
            return problem
    if clean and remarks:
        return 'clean=true при замечаниях'
    if not clean and not remarks:
        return 'clean=false без замечаний'
    return _checked_problem(checked, clean) or _fingerprint_problem(verdict, expected)


def read_verdict(storage, volume_id: str, expected: str) -> tuple[dict | None, str | None]:
    """Вердикт нашего объёма и что с ним не так.

    Чужой id — вердикт по старому объёму: его не видно, как будто его нет.
    Вердикт не по форме или по меньшему объёму убирается, а его изъян идёт в
    поручение: иначе испорченный файл держал бы ход до конца сессии.
    """
    path = storage.path(VERDICT_FILE)
    try:
        verdict = storage.read_json(VERDICT_FILE, default=None)
    except ValueError as error:
        drop_verdict(storage)
        return None, f'вердикт {path} не JSON ({error})'
    if not isinstance(verdict, dict) or verdict.get(VOLUME_ID_KEY) != volume_id:
        return None, None
    problem = verdict_problem(verdict, expected)
    if problem:
        drop_verdict(storage)
        return None, f'вердикт {path} не принят: {problem}'
    return verdict, None


def verdict_key(verdict: dict) -> str:
    """Чем вердикт отличается от другого: id объёма и отпечаток проверенных записей."""
    return f'{verdict[VOLUME_ID_KEY]}:{verdict[FINGERPRINT_KEY]}'


def peek_verdict(storage, volume_id: str) -> dict | None:
    """Вердикт нашего объёма до stop, для превью. Файл не трогается.

    Отпечаток с объёмом не сверяется: это превью, принимает вердикт stop.
    Не JSON или не по форме — None: изъян назовёт stop в поручении.
    """
    try:
        verdict = storage.read_json(VERDICT_FILE, default=None)
    except ValueError:
        return None
    if not isinstance(verdict, dict) or verdict.get(VOLUME_ID_KEY) != volume_id:
        return None
    expected = verdict.get(FINGERPRINT_KEY)
    expected = expected.strip() if isinstance(expected, str) else ''
    return None if verdict_problem(verdict, expected) else verdict
