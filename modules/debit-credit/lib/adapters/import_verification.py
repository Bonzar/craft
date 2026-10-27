"""Проверка добавлений по двум снимкам приложения. Записи и счета не меняются."""

from collections import Counter, defaultdict
from decimal import Decimal

from .common import amount_decimal
from .debit_credit_backup import normalize_money
from .markers import Marker
from .matching import time_gap

FIELDS = ('amount', 'currency', 'account', 'other_account', 'kind', 'cleared',
          'description', 'category', 'tag', 'payee', 'notes')
EXISTING_FIELDS = (*FIELDS, 'timestamp', 'linked_id', 'category_path', 'splits',
                   'account_id', 'reconciled', 'upcoming')


def index_records(snapshot: dict) -> dict:
    records = snapshot['records']
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError('Ожидается снимок с записями приложения')
    result = {}
    for row in records:
        identifier = row.get('source_id')
        if not isinstance(identifier, str) or not identifier or identifier in result:
            raise ValueError('Снимок приложения должен содержать уникальные постоянные ID')
        result[identifier] = row
    return result


def marker_keys(record: dict) -> set:
    keys = set()
    for value in record.get('markers', []):
        marker = Marker(**value)
        keys.add((marker.source, marker.id, marker.role))
    return keys


def compare_fields(expected: dict, actual: dict) -> list:
    different = []
    for key in FIELDS:
        if key not in expected:
            raise ValueError('Не заполнено ожидаемое поле записи')
        matches = (amount_decimal(expected[key]) == amount_decimal(actual[key])
                   if key == 'amount' else expected[key] == actual.get(key))
        if not matches:
            different.append(key)
    if time_gap(expected, actual) != 0:
        different.append('timestamp')
    return different


def verify_leg(expected: dict, after_ids: dict, marker_index: dict, before_ids: dict) -> dict:
    keys = marker_keys(expected)
    if not keys:
        raise ValueError('Для проверки импорта нужны ожидаемые служебные метки')
    matches = set.intersection(*(marker_index.get(key, set()) for key in keys))
    row = {'markers': expected['markers'], 'match_count': len(matches),
           'ledger_ids': sorted(matches), 'different_fields': [], 'issues': []}
    if len(matches) != 1:
        row['issues'].append('missing_or_duplicate_import_marker')
        return row
    identifier = next(iter(matches))
    actual = after_ids[identifier]
    row['different_fields'] = compare_fields(expected, actual)
    if row['different_fields']:
        row['issues'].append('imported_fields_differ')
    if identifier in before_ids:
        row['issues'].append('expected_record_not_new')
    if expected['kind'] == 'transfer_leg':
        peer = expected.get('transfer_peer_marker')
        if not isinstance(peer, dict):
            raise ValueError('Для перевода нужна метка ожидаемой второй стороны')
        peer_keys = marker_keys({'markers': [peer]})
        other = after_ids.get(actual.get('linked_id'))
        if (not other or other.get('linked_id') != identifier or other.get('kind') != 'transfer_leg'
                or other['account'] != expected['other_account']
                or other.get('other_account') != expected['account']):
            row['issues'].append('transfer_link_missing')
        elif not peer_keys <= marker_keys(other):
            row['issues'].append('transfer_peer_marker_differs')
    return row


def balance_delta_issues(before: dict, after: dict, expected: list) -> list:
    first = {row['id']: row for row in before['accounts']}
    last = {row['id']: row for row in after['accounts']}
    issues, deltas = [], defaultdict(lambda: {'balance': Decimal(0), 'uncleared_balance': Decimal(0)})
    for row in expected:
        key = row['account'], row['currency']
        if type(row['cleared']) is not bool:
            raise ValueError('Для проверки остатка нужна ожидаемая завершённость')
        amount = amount_decimal(row['amount'])
        if row['cleared']:
            deltas[key]['balance'] += amount
        deltas[key]['uncleared_balance'] += amount
    if first.keys() != last.keys():
        issues.append({'code': 'account_set_changed'})
    known_names = {(row['name'], row['currency']) for row in first.values()}
    if set(deltas) - known_names:
        issues.append({'code': 'expected_account_not_in_baseline'})
    for identifier in first.keys() & last.keys():
        a, b = first[identifier], last[identifier]
        if any(a.get(key) != b.get(key) for key in ('name', 'currency', 'hidden', 'deleted_locally')):
            issues.append({'code': 'account_metadata_changed', 'account_id': identifier})
        for field in ('balance', 'uncleared_balance'):
            old, old_issues = normalize_money(a[field], a['currency'])
            new, new_issues = normalize_money(b[field], b['currency'])
            delta = new - old
            wanted = deltas[(a['name'], a['currency'])][field]
            if old_issues or new_issues or delta != wanted:
                issues.append({'code': 'account_balance_delta_differs', 'account_id': identifier,
                               'field': field, 'expected_delta': str(wanted), 'actual_delta': str(delta)})
    return issues


def verify_additions(before: dict, after: dict, expected: list) -> dict:
    """Проверяет добавление, не исправления существующих записей и не баланс банка."""
    if not isinstance(expected, list) or not expected:
        raise ValueError('Нужен непустой список ожидаемых записей')
    before_ids, after_ids = index_records(before), index_records(after)
    marker_index = defaultdict(set)
    for identifier, row in after_ids.items():
        for key in marker_keys(row):
            marker_index[key].add(identifier)
    rows = [verify_leg(row, after_ids, marker_index, before_ids) for row in expected]
    issues = [{'code': code, 'expected_index': i} for i, row in enumerate(rows) for code in row['issues']]
    uses = Counter(identifier for row in rows for identifier in row['ledger_ids'])
    if any(count > 1 for count in uses.values()):
        issues.append({'code': 'expected_rows_share_app_record'})
    if before.get('rejected') or after.get('rejected') or before.get('issues') or after.get('issues'):
        issues.append({'code': 'snapshot_reader_errors'})
    changed = sorted(identifier for identifier in before_ids.keys() & after_ids.keys()
                     if any(before_ids[identifier].get(key) != after_ids[identifier].get(key)
                            for key in EXISTING_FIELDS))
    removed = sorted(before_ids.keys() - after_ids.keys())
    matched = {identifier for row in rows for identifier in row['ledger_ids']}
    unexpected = sorted(after_ids.keys() - before_ids.keys() - matched)
    for condition, code in ((changed, 'existing_records_changed'), (removed, 'existing_records_removed'),
                            (unexpected, 'unexpected_new_records')):
        if condition:
            issues.append({'code': code})
    issues.extend(balance_delta_issues(before, after, expected))
    return {'schema_version': 1, 'adapter': 'import-verification', 'input_count': len(expected),
            'records': rows, 'rejected': [], 'issues': issues, 'ready': not issues,
            'coverage': 'expected_additions_only', 'changed_existing_ids': changed,
            'removed_existing_ids': removed, 'unexpected_new_ids': unexpected,
            'limitations': ['does_not_reconcile_bank_balances', 'does_not_verify_updates_or_deletions',
                            'requires_fresh_full_before_after_snapshots', 'no_writes']}
