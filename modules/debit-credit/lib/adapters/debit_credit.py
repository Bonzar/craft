"""Чтение русского CSV Debit & Credit. Каждая строка остаётся отдельной."""

import csv
import io
import re
from zoneinfo import ZoneInfo

from .common import amount_decimal, parse_date, record_base, report
from .markers import extract_markers

HEADERS = ('Дата', 'Описание', 'Категория', 'Контрагент', 'Заметки', 'Тег',
           'Счет', 'Счет-получатель перевода', 'Сумма')
CURRENCY_PATTERN = re.compile(r'[A-Z][A-Z0-9_]{2,20}')


def read_csv(text: str, timezone: str, account_currencies: dict | None = None) -> dict:
    ZoneInfo(timezone)
    currencies = validate_currencies({} if account_currencies is None else account_currencies)
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')), strict=True)
    if reader.fieldnames != list(HEADERS):
        raise ValueError('CSV должен содержать девять известных колонок в исходном порядке')
    try:
        rows = list(reader)
    except csv.Error as error:
        raise ValueError('Нарушена структура CSV, чтение не завершено') from error
    result = report('debit-credit-csv', rows,
                    lambda row, index: parse_row(row, index, timezone, currencies))
    result['limitations'] = ['no_source_ids', 'no_cleared_column', 'no_balance_column',
                             'currency_from_explicit_account_mapping_only',
                             'split_categories_preserved_as_text']
    return result


def validate_currencies(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError('Валюты счетов должны быть объектом account: currency')
    for account, currency in value.items():
        if not isinstance(account, str) or not account.strip():
            raise ValueError('Пустой счёт в настройке валют')
        if not isinstance(currency, str) or not CURRENCY_PATTERN.fullmatch(currency):
            raise ValueError('Неизвестный формат валюты в настройке счёта')
    return dict(value)


def parse_row(row: dict, index: int, timezone: str, currencies: dict) -> dict:
    if set(row) != set(HEADERS) or any(value is None for value in row.values()):
        raise ValueError('Число полей строки не совпадает с заголовком')
    account, other = row['Счет'].strip(), row['Счет-получатель перевода'].strip()
    if not account:
        raise ValueError('Не указан счёт операции')
    amount = amount_decimal(row['Сумма'])
    timestamp, _ = parse_date(row['Дата'], timezone)
    clock = re.search(r'[T ]\d{2}:\d{2}:\d{2}(?:[.,](\d+))?', row['Дата'])
    precision = 'second' if clock else 'minute'
    if clock and clock[1]:
        precision = 'millisecond' if len(clock[1]) <= 3 else 'microsecond'
    return {**record_base('debit-credit', index + 2),
            'timestamp': timestamp, 'timestamp_precision': precision, 'amount': str(amount),
            'currency': currencies.get(account), 'account': account,
            'other_account': other or None,
            'kind': 'transfer_leg' if other else ('expense' if amount < 0 else 'income'),
            'description': row['Описание'], 'notes': row['Заметки'],
            'category': row['Категория'] or None, 'tag': row['Тег'] or None,
            'payee': row['Контрагент'],
            'markers': [marker.as_dict() for marker in extract_markers(row['Заметки'])],
            'raw': dict(row)}
