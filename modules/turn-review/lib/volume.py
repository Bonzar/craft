"""Объём ревью на диске: дописать строку, разобрать файл, починить, посчитать.

Объём — всё, что ход сделал с Craft с последнего чистого вердикта или
разрешения Влада, по строке на событие. Строки читаются в типы записей
(parse_record): файл мог испортить кто угодно, и строка, которая не JSON или
не собирается в запись, называется с причиной, а не прячется.
"""

import hashlib
import json
import os
from typing import NewType

from .records import KIND_WRITE, VolumeRecord, WriteRecord, parse_record, record_row

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


def parse_line(line: bytes) -> VolumeRecord:
    """Запись из строки файла; не JSON или не запись — ValueError с причиной."""
    return parse_record(json.loads(line.decode('utf-8')))


def parse_volume_file(path) -> tuple[list[VolumeRecord], list[str]]:
    """Разбор файла объёма по байтам: хук и скрипт отпечатка читают его одной функцией.

    Второе — описания нечитаемых строк: номер и причина. Строка JSON, которая
    не собирается в запись, — такая же нечитаемая, как обрыв.
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
            records.append(parse_line(line))
        except ValueError as error:
            damage.append(f'строка {number}: {error}')
    return records, damage


def parse_volume(storage) -> tuple[list[VolumeRecord], list[str]]:
    return parse_volume_file(storage.path(VOLUME_FILE))


def rewrite_volume(storage, records: list[VolumeRecord]) -> None:
    path = storage.path(VOLUME_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.tmp-{os.getpid()}')
    tmp.write_text(''.join(json.dumps(record_row(record), ensure_ascii=False) + '\n'
                           for record in records), encoding='utf-8')
    os.replace(tmp, path)


def peek_volume(storage) -> list[VolumeRecord]:
    """Читаемые записи объёма без починки файла: починка и её след — дело stop."""
    return parse_volume(storage)[0]


def load_volume(storage) -> tuple[list[VolumeRecord], str | None]:
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


def count_kind(volume: list[VolumeRecord], kind: str) -> int:
    return sum(1 for record in volume if record.kind == kind)


def count_writes(volume: list[VolumeRecord]) -> int:
    return count_kind(volume, KIND_WRITE)


def writes_before_turn(volume: list[VolumeRecord], turn: int) -> bool:
    """Записи есть, и все сделаны ходами раньше этого: этот ход в Craft не писал.

    Пустой объём — False: там нечего отпускать как разговор. Запись без
    номера хода не раньше. Номер хода после потери состояния продолжается
    от записей объёма, см. next_turn.
    """
    turns = [record.turn for record in volume if isinstance(record, WriteRecord)]
    return bool(turns) and all(item is not None and item < turn for item in turns)


def next_turn(state_turn: int, volume: list[VolumeRecord]) -> int:
    """Номер нового хода: больше и счётчика состояния, и любого хода в объёме.

    Состояние потеряно — счётчик снова 0, а записи объёма помнят свои ходы.
    Без этого ходы-разговоры после потери блокировались бы, пока счётчик не
    догонит старые записи.
    """
    known = [record.turn for record in volume if record.turn is not None]
    return max([state_turn, *known]) + 1


def fingerprint(volume: list[VolumeRecord]) -> Fingerprint:
    """Отпечаток записей `write` объёма: одна и та же строка у хука и у подагента.

    Считается по записям в каноническом виде, а не по байтам файла: починка
    объёма переписывает файл, а записи в нём те же.
    """
    lines = [json.dumps(record_row(record), ensure_ascii=False, sort_keys=True)
             for record in volume if isinstance(record, WriteRecord)]
    digest = hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()
    return Fingerprint(digest[:FINGERPRINT_CHARS])
