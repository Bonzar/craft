"""Связи по меткам и кандидаты старых записей. Не принимает решений о записи."""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from .common import amount_decimal
from .markers import Marker

PRECISIONS = {'minute': timedelta(minutes=1), 'second': timedelta(seconds=1),
              'microsecond': timedelta(microseconds=1)}
MAPPING_FIELDS = ('account', 'source_currency', 'currency')


def identity(record: dict) -> tuple | None:
    if record['source_id'] is None:
        return None
    return record['source'], record['source_id'], record.get('role')


def time_interval(record: dict) -> tuple:
    stamp = datetime.fromisoformat(record['timestamp'])
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('Дата сопоставления должна содержать часовой пояс')
    precision = record['timestamp_precision']
    if precision not in PRECISIONS:
        raise ValueError('Неизвестная точность даты')
    if precision == 'minute':
        stamp = stamp.replace(second=0, microsecond=0)
    elif precision == 'second':
        stamp = stamp.replace(microsecond=0)
    start = stamp.astimezone(timezone.utc)
    return start, start + PRECISIONS[precision]


def validate_records(records: list) -> None:
    if not isinstance(records, list):
        raise ValueError('Ожидается список операций')
    for row in records:
        if not isinstance(row, dict):
            raise ValueError('Ожидается объект операции')
        if not isinstance(row.get('amount'), str) or not isinstance(row.get('timestamp'), str):
            raise ValueError('Нужны строковые сумма и дата операции')
        Marker(row['source'], row['source_id'] or 'without-id', row.get('role'))
        if row['source_id'] is not None and (not isinstance(row['source_id'], str) or not row['source_id']):
            raise ValueError('ID должен быть строкой или null')
        if row.get('cleared') is not None and type(row['cleared']) is not bool:
            raise ValueError('Некорректный признак завершённости')
        amount_decimal(row['amount'])
        time_interval(row)
        if not isinstance(row.get('markers', []), list):
            raise ValueError('Ожидается список меток')
        for item in row.get('markers', []):
            Marker(**item)


def validate_mapping(mapping: dict) -> None:
    if not isinstance(mapping, dict):
        raise ValueError('Ожидается карта счетов по источникам')
    for source, accounts in mapping.items():
        Marker(source, 'mapping')
        if not isinstance(accounts, dict):
            raise ValueError('Ожидается карта счетов источника')
        for name, target in accounts.items():
            if not isinstance(name, str) or not name or not isinstance(target, dict):
                raise ValueError('Некорректное сопоставление счёта')
            if any(not isinstance(target.get(key), str) or not target[key]
                   for key in MAPPING_FIELDS):
                raise ValueError('Нужны счёт приложения и обе единицы суммы')


def mapped_record(row: dict, mapping: dict) -> tuple[dict | None, list]:
    accounts = mapping.get(row['source'], {})
    target = accounts.get(row.get('account'))
    if target is None:
        return None, ['unmapped_account']
    if row.get('currency') != target['source_currency']:
        return None, ['mapping_currency_mismatch']
    result = {**row, 'account': target['account'], 'currency': target['currency']}
    other = row.get('other_account')
    if other:
        if other not in accounts:
            return None, ['unmapped_other_account']
        result['other_account'] = accounts[other]['account']
    return result, []


def time_gap(first: dict, second: dict) -> float:
    a_start, a_end = time_interval(first)
    b_start, b_end = time_interval(second)
    if a_start < b_end and b_start < a_end:
        return 0
    return max((a_start - b_end).total_seconds(), (b_start - a_end).total_seconds(), 0.000001)


def differences(expected: dict, actual: dict) -> dict:
    result = {}
    for key in ('amount', 'currency', 'account', 'other_account', 'cleared', 'kind'):
        value = expected.get(key)
        if value is None:
            continue
        matches = (amount_decimal(value) == amount_decimal(actual[key]) if key == 'amount'
                   else value == actual.get(key))
        if not matches:
            result[key] = {'source': value, 'ledger': actual.get(key)}
    if time_gap(expected, actual) > 0:
        result['timestamp'] = {'source': expected['timestamp'], 'ledger': actual['timestamp']}
    return result


def index_markers(ledger: list) -> dict:
    index = defaultdict(list)
    for position, row in enumerate(ledger):
        keys = {(m['source'], m['id'], m.get('role')) for m in row.get('markers', [])}
        for key in keys:
            index[key].append(position)
    return index


def candidate_indices(expected: dict | None, ledger: list, window_seconds: int) -> list:
    if expected is None:
        return []
    found = []
    for position, row in enumerate(ledger):
        if row.get('markers') or row.get('eligible_for_matching') is False:
            continue
        if (expected['account'] == row.get('account')
                and expected['currency'] == row.get('currency')
                and amount_decimal(expected['amount']) == amount_decimal(row['amount'])
                and time_gap(expected, row) <= window_seconds):
            found.append(position)
    return found


def compare_one(row: dict, ledger: list, mapping: dict, marker_index: dict,
                repeated: Counter, window_seconds: int) -> dict:
    key = identity(row)
    expected, map_issues = mapped_record(row, mapping)
    marked = marker_index.get(key, []) if key else []
    indices = marked or candidate_indices(expected, ledger, window_seconds)
    match = ('marker_conflict' if len(marked) > 1 else 'marker') if marked else (
        'candidate' if indices else 'no_candidate')
    issues = [*row.get('issues', []), *map_issues]
    if key and repeated[key] > 1:
        issues.append('repeated_source_identity')
    if row.get('status') in ('unknown', 'canceled') or row.get('eligible_for_matching') is False:
        issues.append('source_requires_review')
    if any(ledger[i].get('eligible_for_matching') is False for i in indices):
        issues.append('ledger_requires_review')
    return {'record': deepcopy(row), 'match': match, 'ledger_indices': list(indices),
            'differences': {i: differences(expected, ledger[i]) for i in indices} if expected else {},
            'issues': issues}


def compare_records(sources: list, ledger: list, account_map: dict, *, window_seconds: int) -> dict:
    """Окно поиска задаёт вызывающий. no_candidate не означает доказанный пропуск."""
    validate_records(sources)
    validate_records(ledger)
    validate_mapping(account_map)
    if type(window_seconds) is not int or window_seconds < 0:
        raise ValueError('Окно поиска — целое неотрицательное число секунд')
    marker_index = index_markers(ledger)
    repeated = Counter(identity(row) for row in sources if identity(row))
    rows = [compare_one(row, ledger, account_map, marker_index, repeated, window_seconds)
            for row in sources]
    references = Counter(i for row in rows for i in row['ledger_indices'])
    for row in rows:
        if any(references[i] > 1 for i in row['ledger_indices']):
            row['issues'].append('shared_candidate')
    if len(rows) != len(sources):
        raise RuntimeError('Нарушен счёт сопоставленных операций')
    return {'schema_version': 1, 'adapter': 'comparison', 'input_count': len(sources),
            'records': rows, 'rejected': [], 'ledger_records': deepcopy(ledger),
            'unreferenced_ledger': [i for i in range(len(ledger)) if i not in references],
            'issues': [{'locator': i, 'code': code} for i, row in enumerate(rows)
                       for code in row['issues']], 'coverage': 'unverified', 'ready': False,
            'window_seconds': window_seconds,
            'limitations': ['candidates_require_human_review', 'no_candidate_is_not_missing',
                            'metadata_not_compared', 'marked_ledger_excluded_from_fuzzy_search',
                            'balances_not_reconciled', 'no_writes']}
