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
from pathlib import Path
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
# Критерии последнего собранного поручения: по ним принимается вердикт.
CRITERIA_KEY = 'criteria'
# Замечания последнего вердикта с замечаниями: вопрос человеку показывает их.
LAST_REMARKS_KEY = 'last_remarks'
# Реплика, открывшая текущий ход, и модули, взявшие по ней номер: хуки одного
# события идут параллельно, и номер хода у всех модулей на реплике один.
PROMPT_MARK_KEY = 'prompt_mark'
PROMPT_TAKEN_KEY = 'prompt_taken'

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
CRITERION_KEY = 'criterion'
WHERE_KEY = 'where'
TEXT_KEY = 'text'
REMARK_FIELDS = (CRITERION_KEY, WHERE_KEY, TEXT_KEY)


def new_volume_id() -> str:
    return uuid.uuid4().hex


def new_state(turn: int = 0, volume_id: str | None = None, nudges: int = 0,
              released: bool = False, first_nudge_at: str | None = None,
              summary: str | None = None, summary_shown: bool = False,
              shown_verdict: str | None = None, criteria: list | None = None,
              last_remarks: list | None = None, prompt_mark: str | None = None,
              prompt_taken: list | None = None) -> dict:
    return {TURN_KEY: turn, VOLUME_ID_KEY: volume_id or new_volume_id(),
            NUDGES_KEY: nudges, RELEASED_KEY: released, FIRST_NUDGE_KEY: first_nudge_at,
            SUMMARY_KEY: summary, SUMMARY_SHOWN_KEY: summary_shown,
            SHOWN_VERDICT_KEY: shown_verdict, CRITERIA_KEY: list(criteria or []),
            LAST_REMARKS_KEY: list(last_remarks or []), PROMPT_MARK_KEY: prompt_mark,
            PROMPT_TAKEN_KEY: list(prompt_taken or [])}


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
            and isinstance(state.get(SUMMARY_SHOWN_KEY, False), bool)
            and isinstance(state.get(CRITERIA_KEY, []), list)
            and isinstance(state.get(LAST_REMARKS_KEY, []), list)
            and isinstance(state.get(PROMPT_MARK_KEY), (str, type(None)))
            and isinstance(state.get(PROMPT_TAKEN_KEY, []), list))


def _is_valid_state(state) -> bool:
    if not isinstance(state, dict):
        return False
    return (isinstance(state.get(TURN_KEY), int)
            and isinstance(state.get(VOLUME_ID_KEY), str) and bool(state[VOLUME_ID_KEY])
            and isinstance(state.get(NUDGES_KEY), int)
            and isinstance(state.get(RELEASED_KEY), bool)
            and _is_valid_debug_fields(state))


def _state_damage(storage) -> tuple[dict | None, str | None]:
    """Состояние с диска и что с ним не так. Файла нет — (None, None).

    Пустой файл — порча, а не отсутствие: состояние пишется атомарно, и
    пустым его оставить могла только чужая рука.
    """
    if not storage.path(STATE_FILE).exists():
        return None, None
    try:
        state = storage.read_json(STATE_FILE, default=None)
    except ValueError as error:
        # ValueError ловит и битый JSON, и байты не в UTF-8.
        return None, f'не JSON ({error})'
    if state is None:
        return None, 'пуст'
    if _is_valid_state(state):
        return state, None
    return None, f'испорчен: {state!r}'


def peek_state(storage) -> dict | None:
    """Состояние без починки: None, если файла нет или он испорчен.

    Показ ответа идёт до остановки, а починку с её уведомлением делает stop.
    Отсутствие от порчи здесь не отличается: в обоих случаях ответ спрятан.
    """
    state, _ = _state_damage(storage)
    return state


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
                         state.get(SHOWN_VERDICT_KEY), state.get(CRITERIA_KEY),
                         state.get(LAST_REMARKS_KEY), state.get(PROMPT_MARK_KEY),
                         state.get(PROMPT_TAKEN_KEY)), None
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
    """Записи есть, и все сделаны ходами раньше этого: этот ход в Craft не писал.

    Пустой объём — False: там нечего отпускать как разговор. Запись без
    номера хода не раньше. Номер хода после потери состояния продолжается
    от записей объёма, см. next_turn.
    """
    turns = [record.get(RECORD_TURN_KEY) for record in volume
             if record.get(KIND_KEY) == KIND_WRITE]
    return bool(turns) and all(isinstance(item, int) and item < turn for item in turns)


def next_turn(state_turn: int, volume: list[dict]) -> int:
    """Номер нового хода: больше и счётчика состояния, и любого хода в объёме.

    Состояние потеряно — счётчик снова 0, а записи объёма помнят свои ходы.
    Без этого ходы-разговоры после потери блокировались бы, пока счётчик не
    догонит старые записи.
    """
    turns = [record.get(RECORD_TURN_KEY) for record in volume]
    known = [item for item in turns if isinstance(item, int)]
    return max([state_turn, *known]) + 1


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


def _remark_problem(remark, criteria: list[str]) -> str | None:
    if not isinstance(remark, dict):
        return f'замечание {remark!r} не объект'
    for field_name in REMARK_FIELDS:
        if not isinstance(remark.get(field_name), str) or not remark[field_name].strip():
            return f'у замечания нет поля «{field_name}» строкой'
    if remark[CRITERION_KEY] not in criteria:
        return f'критерий «{remark[CRITERION_KEY]}» не из {", ".join(criteria)}'
    return None


def _checked_problem(checked: list, clean: bool, criteria: list[str]) -> str | None:
    unknown = [str(item) for item in checked if item not in criteria]
    if unknown:
        return f'в «{CHECKED_KEY}» критерии не из {", ".join(criteria)}: {", ".join(unknown)}'
    missing = [item for item in criteria if item not in checked]
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


def verdict_problem(verdict: dict, expected: str, criteria: list[str]) -> str | None:
    """Что не так с вердиктом нашего объёма по критериям поручения. Всё по форме — None."""
    clean, remarks, checked = (verdict.get(CLEAN_KEY), verdict.get(REMARKS_KEY),
                               verdict.get(CHECKED_KEY))
    if not isinstance(clean, bool):
        return f'поле «{CLEAN_KEY}» не true и не false'
    if not isinstance(remarks, list):
        return f'поле «{REMARKS_KEY}» не список'
    if not isinstance(checked, list):
        return f'поле «{CHECKED_KEY}» не список'
    for remark in remarks:
        problem = _remark_problem(remark, criteria)
        if problem:
            return problem
    if clean and remarks:
        return 'clean=true при замечаниях'
    if not clean and not remarks:
        return 'clean=false без замечаний'
    return _checked_problem(checked, clean, criteria) or _fingerprint_problem(verdict, expected)


def read_verdict(storage, volume_id: str, expected: str, criteria: list[str]) -> tuple[dict | None, str | None]:
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
    problem = verdict_problem(verdict, expected, criteria)
    if problem:
        drop_verdict(storage)
        return None, f'вердикт {path} не принят: {problem}'
    return verdict, None


def verdict_key(verdict: dict) -> str:
    """Чем вердикт отличается от другого: id объёма и отпечаток проверенных записей."""
    return f'{verdict[VOLUME_ID_KEY]}:{verdict[FINGERPRINT_KEY]}'


def peek_verdict(storage, volume_id: str, criteria: list[str]) -> dict | None:
    """Вердикт нашего объёма до stop, для превью. Файл не трогается.

    Отпечаток с объёмом не сверяется: это превью, принимает вердикт stop.
    Критерии те же, по которым его примет stop, а не `checked` самого
    вердикта: иначе чистый вердикт с пустым `checked` открыл бы ответ.
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
    return None if verdict_problem(verdict, expected, criteria) else verdict


def current_turn(storage) -> int:
    """Номер текущего хода из состояния.

    Файла нет — 0: свежая сессия, ни одной реплики ещё не было. Файл испорчен
    — ValueError с путём: тихий 0 приписал бы запись не тому ходу.
    """
    state, damage = _state_damage(storage)
    if damage is not None:
        raise ValueError(f'файл состояния ревью {storage.path(STATE_FILE)} {damage}: '
                         'номер хода не узнать. Его заведёт заново остановка хода')
    return state[TURN_KEY] if state is not None else 0


def append_turn_record(storage, record: dict) -> None:
    """Запись объёма с номером текущего хода.

    Состояние испорчено — запись всё равно ложится, без номера, и ошибка
    пробрасывается. Запись без номера не раньше ни одного хода и идёт на
    ревью; потерянная запись ушла бы от ревью совсем.
    """
    try:
        turn = current_turn(storage)
    except ValueError:
        append_record(storage, record)
        raise
    append_record(storage, {KIND_KEY: record.get(KIND_KEY), RECORD_TURN_KEY: turn, **record})


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
    mark, taken = prompt_mark(text), state[PROMPT_TAKEN_KEY]
    if state[PROMPT_MARK_KEY] == mark and slug not in taken:
        save_state(storage, {**state, PROMPT_TAKEN_KEY: [*taken, slug]})
        return state[TURN_KEY], problem
    turn = next_turn(state[TURN_KEY], peek_volume(storage))
    save_state(storage, {**state, TURN_KEY: turn, PROMPT_MARK_KEY: mark, PROMPT_TAKEN_KEY: [slug]})
    return turn, problem


# Вопрос самого ревью о застрявшем ревью: его ответ разбирает хук, в объём
# он не идёт. Модель может пересказать вопрос, тогда он узнаётся по паре
# подписей вариантов.
OPTION_REVIEW = 'Провести ревью'
OPTION_RELEASE = 'Закончить без ревью'
QUESTION_MARK = 'Ревью хода не состоялось'
OUR_OPTIONS = frozenset({OPTION_REVIEW, OPTION_RELEASE})
QUESTIONS_FIELD = 'questions'
QUESTION_FIELD = 'question'
OPTIONS_FIELD = 'options'
LABEL_FIELD = 'label'


def question_items(tool_input) -> list[dict]:
    items = tool_input.get(QUESTIONS_FIELD) if isinstance(tool_input, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def option_labels(item: dict) -> set[str]:
    options = item.get(OPTIONS_FIELD)
    if not isinstance(options, list):
        return set()
    labels = [option.get(LABEL_FIELD) for option in options if isinstance(option, dict)]
    return {label.strip() for label in labels if isinstance(label, str)}


def review_question(tool_input) -> str | None:
    """Текст вопроса о ревью в вызове AskUserQuestion; нет его — None."""
    for item in question_items(tool_input):
        text = item.get(QUESTION_FIELD)
        if isinstance(text, str) and (QUESTION_MARK in text or OUR_OPTIONS <= option_labels(item)):
            return text
    return None


def is_review_question(tool_input) -> bool:
    return review_question(tool_input) is not None


# Сборка поручения подагенту. Каркас у ревью, записи описывают поставщики,
# критерии привозят модули: файл data/review-criterion.md с шапкой
# «id: ‹имя›» и «applies: always | craft | tags #тег …», потом текст.
CRITERION_FILE = 'review-criterion.md'
RECORDS_FILE = 'review-records.md'
DATA_DIR = 'data'
ID_FIELD = 'id'
APPLIES_FIELD = 'applies'
APPLIES_ALWAYS = 'always'
APPLIES_CRAFT = 'craft'
APPLIES_TAGS = 'tags'
BRIEF_FILE = 'turn-review-brief.md'
CRITERIA_MARK = '{{CRITERIA}}'
RECORDS_MARK = '{{RECORDS}}'
CRITERIA_LIST_MARK = '{{CRITERIA_LIST}}'


HEAD_FIELDS = (ID_FIELD, APPLIES_FIELD)
TAG_PREFIX = '#'


def parse_criterion(text: str) -> tuple[dict, str]:
    """Шапка критерия и его текст.

    Шапка — строки «id: …» и «applies: …», за ней обязательно пустая строка.
    Без неё граница не видна: первая строка текста ушла бы в шапку или
    пропала. Иное — ValueError с причиной.
    """
    head, lines = {}, text.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            return head, '\n'.join(lines[index + 1:]).strip()
        key, separator, value = line.partition(':')
        if not separator or key.strip() not in HEAD_FIELDS:
            raise ValueError(f'строка «{line}» в шапке: шапка — только {", ".join(HEAD_FIELDS)}, '
                             'после неё нужна пустая строка')
        head[key.strip()] = value.strip()
    raise ValueError('после шапки нет пустой строки и текста критерия')


def applies_problem(rule: str) -> str | None:
    """Что не так с правилом `applies`; всё верно — None."""
    words = rule.split()
    if rule in (APPLIES_ALWAYS, APPLIES_CRAFT):
        return None
    if words and words[0] == APPLIES_TAGS and words[1:] \
            and all(word.startswith(TAG_PREFIX) for word in words[1:]):
        return None
    return (f'applies «{rule}»: ждал {APPLIES_ALWAYS}, {APPLIES_CRAFT} или '
            f'{APPLIES_TAGS} и теги с «{TAG_PREFIX}»')


def criterion_applies(head: dict, volume: list[dict]) -> bool:
    """Подходит ли критерий к объёму. Правило уже проверено applies_problem."""
    rule = head[APPLIES_FIELD]
    writes = [record for record in volume if record.get(KIND_KEY) == KIND_WRITE]
    if rule == APPLIES_ALWAYS:
        return True
    if rule == APPLIES_CRAFT:
        return bool(writes)
    wanted = {word.lower() for word in rule.split()[1:]}
    for record in writes:
        types = record.get(TYPES_KEY)
        if isinstance(types, list) and wanted & {str(item).lower() for item in types}:
            return True
    return False


def criterion_problem(head: dict, body: str) -> str | None:
    """Чего не хватает разобранному файлу критерия; всё есть — None."""
    if not head.get(ID_FIELD):
        return 'нет id'
    if not body:
        return 'нет текста'
    if APPLIES_FIELD not in head:
        return 'нет applies'
    return applies_problem(head[APPLIES_FIELD])


def gather(modules_dir, filename: str) -> list[tuple[str, str]]:
    """Файлы данных с этим именем у всех модулей каталога: (slug папки, текст)."""
    found = []
    for child in sorted(Path(modules_dir).iterdir()):
        path = child / DATA_DIR / filename
        if child.is_dir() and path.is_file():
            found.append((child.name, path.read_text(encoding='utf-8')))
    return found


def read_criteria(modules_dir) -> tuple[list[tuple[dict, str]], list[str]]:
    """Разобранные критерии модулей и что не разобралось: «slug (причина)».

    Повтор id у второго модуля тоже не разобран: вердикт по id не отличил бы
    один критерий от другого. Первый модуль с этим id остаётся.
    """
    found, broken, owners = [], [], {}
    for slug, text in gather(modules_dir, CRITERION_FILE):
        try:
            head, body = parse_criterion(text)
        except ValueError as error:
            broken.append(f'{slug} ({error})')
            continue
        problem = criterion_problem(head, body)
        if problem is None and head[ID_FIELD] in owners:
            problem = f'id «{head[ID_FIELD]}» уже у модуля {owners[head[ID_FIELD]]}'
        if problem is not None:
            broken.append(f'{slug} ({problem})')
            continue
        owners[head[ID_FIELD]] = slug
        found.append((head, body))
    return found, broken


def assemble_brief(modules_dir, volume: list[dict], frame: str) -> tuple[str, list[str], list[str]]:
    """Поручение подагенту из каркаса, записей поставщиков и подходящих критериев.

    Возвращает текст, id критериев по порядку и список модулей с причиной,
    чей файл критерия не разобрался: их нельзя молча пропустить.
    """
    criteria, ids = [], []
    found, broken = read_criteria(modules_dir)
    for head, body in found:
        if criterion_applies(head, volume):
            ids.append(head[ID_FIELD])
            criteria.append(body)
    records = [text.strip() for _, text in gather(modules_dir, RECORDS_FILE)]
    text = (frame.replace(RECORDS_MARK, '\n\n'.join(records))
            .replace(CRITERIA_MARK, '\n\n'.join(criteria))
            .replace(CRITERIA_LIST_MARK, ', '.join(f'"{item}"' for item in ids)))
    return text, ids, broken


def criteria_for(modules_dir, volume: list[dict]) -> tuple[list[str], list[str]]:
    """Id критериев, которые подходят к объёму, и не разобранные файлы, без сборки текста."""
    _, ids, broken = assemble_brief(modules_dir, volume, '')
    return ids, broken


def write_brief(storage, text: str):
    path = storage.path(BRIEF_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path
