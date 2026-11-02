"""Объём ревью на диске: дописать строку, разобрать файл, починить, посчитать.

Объём — всё, что ход сделал с Craft с последнего чистого вердикта или
разрешения Влада, по строке на событие. Строки читаются словарями: файл мог
испортить кто угодно, нечитаемое называется, а не прячется.
"""

import hashlib
import json
import os
from typing import NewType

from .records import KIND_KEY, KIND_WRITE, RECORD_TURN_KEY

VOLUME_FILE = 'turn-review-volume.jsonl'
FINGERPRINT_CHARS = 16

# Отпечаток записей write, которые проверил подагент. Id объёма меняется
# только при очистке, и без отпечатка чистый вердикт отпустил бы и запись,
# сделанную уже после ревью. Отпечаток, а не число записей: он говорит, что
# именно проверено, а не сколько.
Fingerprint = NewType('Fingerprint', str)


def append_row_unlocked(storage, row: dict) -> None:
    """Дописать строку в объём; лок держит вызывающий."""
    storage.append_line(VOLUME_FILE, json.dumps(row, ensure_ascii=False))


def parse_volume_file(path) -> tuple[list[dict], list[str]]:
    """Разбор файла объёма по байтам: хук и скрипт отпечатка читают его одной функцией.

    Второе — описания нечитаемых строк: номер и причина.
    """
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


def parse_volume(storage) -> tuple[list[dict], list[str]]:
    return parse_volume_file(storage.path(VOLUME_FILE))


def rewrite_volume(storage, records: list[dict]) -> None:
    path = storage.path(VOLUME_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.tmp-{os.getpid()}')
    tmp.write_text(''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records),
                   encoding='utf-8')
    os.replace(tmp, path)


def peek_volume(storage) -> list[dict]:
    """Читаемые строки объёма без починки файла: починка и её след — дело stop."""
    return parse_volume(storage)[0]


def load_volume(storage) -> tuple[list[dict], str | None]:
    """Объём и что в нём пропало.

    Нечитаемые строки убираются, остальное идёт на ревью как было. Что было в
    убранных строках, уже не узнать: это называется человеку, а не прячется.
    """
    records, damage = parse_volume(storage)
    if not damage:
        return records, None
    rewrite_volume(storage, records)
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


def fingerprint(volume: list[dict]) -> Fingerprint:
    """Отпечаток записей `write` объёма: одна и та же строка у хука и у подагента.

    Считается по записям в каноническом виде, а не по байтам файла: починка
    объёма переписывает файл, а записи в нём те же.
    """
    lines = [json.dumps(record, ensure_ascii=False, sort_keys=True)
             for record in volume if record.get(KIND_KEY) == KIND_WRITE]
    digest = hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()
    return Fingerprint(digest[:FINGERPRINT_CHARS])
