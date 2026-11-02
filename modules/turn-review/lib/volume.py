"""Объём ревью на диске: дописать строку, разобрать файл, починить, посчитать.

Объём — всё, что ход сделал с Craft с последнего чистого вердикта или
разрешения Влада, по строке на событие. Строки читаются в типы записей
(parse_record): файл мог испортить кто угодно, и строка, которая не JSON или
не собирается в запись, называется с причиной, а не прячется.
"""

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from typing import NewType

from .records import KIND_WRITE, VolumeRecord, WriteRecord, parse_record, record_row

# Имя файла объёма в зоне сессии хранилища — часть контракта поставщиков.
VOLUME_FILE = 'turn-review-volume.jsonl'
FINGERPRINT_CHARS = 16

# Отпечаток записей write, которые проверил подагент. Id объёма меняется
# только при очистке, и без отпечатка чистый вердикт отпустил бы и запись,
# сделанную уже после ревью. Отпечаток, а не число записей: он говорит, что
# именно проверено, а не сколько.
Fingerprint = NewType('Fingerprint', str)


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


def rewrite_volume(storage, choose: Callable[[list[VolumeRecord]], list[VolumeRecord]]
                   ) -> tuple[list[VolumeRecord], list[str]]:
    """Переписать объём записями, которые выбрал `choose` из читаемых; вернуть прочитанное.

    Чтение и замена файла идут под локом журнала ядра (Storage.journal_lock):
    поставщики дописывают объём из своих процессов тем же append_line, и
    строка, дописанная между чтением и заменой, иначе пропала бы. Возвращает
    читаемые записи и нечитаемые строки на момент замены.
    """
    with storage.journal_lock(VOLUME_FILE) as path:
        records, damage = parse_volume_file(path)
        tmp = path.with_name(path.name + f'.tmp-{os.getpid()}')
        tmp.write_text(''.join(json.dumps(record_row(record), ensure_ascii=False) + '\n'
                               for record in choose(records)), encoding='utf-8')
        os.replace(tmp, path)
    return records, damage


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
    # Под локом перечитывается: пока шла проверка, поставщик мог дописать строку.
    records, damage = rewrite_volume(storage, lambda readable: readable)
    if not damage:
        return records, None
    return records, (f'объём ревью {storage.path(VOLUME_FILE)} был испорчен '
                     f'({"; ".join(damage)}): эти строки убраны, их ревью не было')


def count_kind(volume: list[VolumeRecord], kind: str) -> int:
    return sum(1 for record in volume if record.kind == kind)


def count_writes(volume: list[VolumeRecord]) -> int:
    return count_kind(volume, KIND_WRITE)


def record_turn(record: VolumeRecord, prompt_turns: Mapping[str, int]) -> int | None:
    """Номер хода записи по id её реплики; id нет или он ревью незнаком — None."""
    if record.prompt_id is None:
        return None
    return prompt_turns.get(record.prompt_id)


def writes_before_turn(volume: list[VolumeRecord], turn: int,
                       prompt_turns: Mapping[str, int]) -> bool:
    """Записи есть, и все сделаны ходами раньше этого: этот ход в Craft не писал.

    Пустой объём — False: там нечего отпускать как разговор. Запись без id
    реплики или с id, которого ревью не знает, не раньше: её ход неизвестен,
    и она идёт на ревью, а не отпускается молча.
    """
    turns = [record_turn(record, prompt_turns) for record in volume
             if isinstance(record, WriteRecord)]
    return bool(turns) and all(item is not None and item < turn for item in turns)


def fingerprint(volume: list[VolumeRecord]) -> Fingerprint:
    """Отпечаток записей `write` объёма: одна и та же строка у хука и у подагента.

    Считается по записям в каноническом виде, а не по байтам файла: починка
    объёма переписывает файл, а записи в нём те же.
    """
    lines = [json.dumps(record_row(record), ensure_ascii=False, sort_keys=True)
             for record in volume if isinstance(record, WriteRecord)]
    digest = hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()
    return Fingerprint(digest[:FINGERPRINT_CHARS])
