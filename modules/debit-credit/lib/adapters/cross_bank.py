"""Перевод себе в другой банк: списание в одном снимке и поступление в другом — один перевод.

Стороны связываются правилом, как обмен баллов: перевод по СБП, имя второй стороны одно
и то же, банк, если карточка его называет, — банк другого снимка. Одна сумма и близость
по времени перевод не доказывают, поэтому пара пишется, только если она однозначна."""

from collections import defaultdict
from datetime import timedelta

from .common import amount_decimal
from .matching import time_interval

# Перевод по СБП доходит за секунды, но банк может придержать его до проверки.
CROSS_BANK_WINDOW = timedelta(days=3)
SOURCE_BANKS = {'yabank': 'яндекс', 'tbank': 'т-банк', 'sber': 'сбербанк'}
YANDEX_BANK_FIELDS = ('Банк получателя', 'Банк отправителя')
TBANK_SBP_IN = ('Тип перевода', 'Система быстрых платежей')
SBER_SBP_FORMS = {'P2PSBPInTransfer', 'UfsP2PSBPOutTransfer'}


def party(row: dict) -> tuple[str, str | None] | None:
    """Имя второй стороны перевода по СБП и её банк, если карточка его называет."""
    raw = row.get('raw') or {}
    if row['source'] == 'yabank':
        banks = [value for name, value in row.get('bank_fields') or [] if name in YANDEX_BANK_FIELDS]
        name = ((raw.get('feed') or {}).get('title') or {}).get('plain')
        return (name, banks[0]) if len(banks) == 1 and name else None
    if row['source'] == 'tbank':
        fields = (raw.get('payment') or {}).get('fieldsValues') or {}
        if fields.get('workflowType') == 'SBPTransfer':
            return (fields.get('maskedFIO'), fields.get('receiverBankName')) if fields.get('maskedFIO') else None
        info = {(item.get('fieldName'), item.get('fieldValue')) for item in raw.get('additionalInfo') or []}
        if TBANK_SBP_IN in info and raw.get('senderDetails'):
            return raw['senderDetails'], (raw.get('brand') or {}).get('name')
        return None
    if row['source'] == 'sber' and raw.get('form') in SBER_SBP_FORMS and raw.get('correspondent'):
        return raw['correspondent'], None
    return None


def same_person(first: str, second: str) -> bool:
    """Одно имя и одна буква фамилии: банки сокращают имя по-разному."""
    words = [name.replace('.', ' ').split() for name in (first, second)]
    if any(len(parts) < 2 for parts in words):
        return False
    return (words[0][0].casefold() == words[1][0].casefold()
            and words[0][-1][0].casefold() == words[1][-1][0].casefold())


def names_bank(named: str | None, source: str) -> bool:
    return named is None or SOURCE_BANKS.get(source, '\0') in named.casefold()


def candidate(row: dict) -> bool:
    return (row.get('kind') is None and not row.get('role') and row.get('status') == 'completed'
            and row.get('cleared') is True and not row.get('issues') and bool(amount_decimal(row['amount']))
            and not row.get('resolution', {}).get('same_operation_as') and party(row) is not None)


def linked(out: dict, came: dict) -> bool:
    (out_name, out_bank), (in_name, in_bank) = party(out), party(came)
    out_start, out_end = time_interval(out)
    in_start, in_end = time_interval(came)
    return (out['source'] != came['source'] and out['currency'] == came['currency']
            and amount_decimal(out['amount']) + amount_decimal(came['amount']) == 0
            and in_end > out_start and in_start - out_end <= CROSS_BANK_WINDOW
            and same_person(out_name, in_name) and (out_bank or in_bank) is not None
            and names_bank(out_bank, came['source']) and names_bank(in_bank, out['source']))


def cross_bank_links(snapshots: list) -> dict:
    """Ключ стороны → вторая сторона или 'ambiguous'. Неоднозначная пара не связывается."""
    rows = [row for snapshot in snapshots for row in snapshot['records'] if candidate(row)]
    outs = [row for row in rows if amount_decimal(row['amount']) < 0]
    ins = [row for row in rows if amount_decimal(row['amount']) > 0]
    found, back = defaultdict(list), defaultdict(list)
    for out in outs:
        for came in ins:
            if linked(out, came):
                found[key(out)].append(key(came))
                back[key(came)].append(key(out))
    links = {}
    for side, peers in [*found.items(), *back.items()]:
        other = back if side in found else found
        unique = len(peers) == 1 and other[peers[0]] == [side]
        links[side] = peers[0] if unique else 'ambiguous'
    return links


def key(row: dict) -> tuple:
    return row['source'], row['source_id']
