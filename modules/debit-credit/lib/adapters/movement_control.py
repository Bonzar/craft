"""Движение по счетам без подмены неизвестных начальных и конечных остатков."""

from collections import Counter
from copy import deepcopy
from datetime import datetime
from decimal import Decimal

from .common import amount_decimal
from .matching import mapped_record, validate_mapping, validate_records

FINANCIAL_ISSUES = {'feed_detail_amount_differs', 'feed_detail_date_differs',
                    'feed_detail_status_differs', 'unknown_status', 'ambiguous_bank_account_identity'}


def validate_snapshot(snapshot: dict) -> None:
    if (not isinstance(snapshot, dict) or not isinstance(snapshot.get('records'), list)
            or not isinstance(snapshot.get('rejected'), list)
            or type(snapshot.get('input_count')) is not int
            or snapshot['input_count'] != len(snapshot['records']) + len(snapshot['rejected'])):
        raise ValueError('Не сошёлся счёт входа снимка')
    validate_records(snapshot['records'])


def empty_flow() -> dict:
    return {'count': 0, 'credit': Decimal(0), 'debit': Decimal(0), 'net': Decimal(0)}


def empty_account(account: str, currency: str) -> dict:
    return {'account': account, 'currency': currency,
            'source': {'completed': empty_flow(), 'pending': empty_flow()},
            'ledger': {'completed': empty_flow(), 'pending': empty_flow()},
            'opening_balance': None, 'closing_balance': None, 'source_coverage_verified': False}


def disposition(row: dict, mapping: dict, scope: set, start: datetime, end: datetime,
                is_source: bool) -> tuple:
    if not start <= datetime.fromisoformat(row['timestamp']) < end:
        return 'outside_period', None, []
    if is_source and row.get('resolution', {}).get('same_operation_as'):
        return 'confirmed_alias', None, []
    if row.get('status') in {'failed', 'canceled'}:
        return 'nonfinancial_status', None, []
    if not is_source:
        if row.get('upcoming') or row.get('raw', {}).get('deleted') is not None:
            return 'planned_or_deleted', None, []
        if (row['account'], row['currency']) not in scope:
            return 'outside_source_scope', row, []
        if row.get('eligible_for_matching') is False:
            return 'unresolved', None, ['ineligible_ledger_record']
    expected, issues = mapped_record(row, mapping) if is_source else (row, [])
    issues = [*issues, *(code for code in row.get('issues', []) if code in FINANCIAL_ISSUES)]
    if expected is None or issues:
        return 'unresolved', expected, issues
    if (expected.get('kind') in {'transfer', 'transfer_leg'}
            and expected['account'] == expected.get('other_account')):
        return 'internal_same_account', expected, []
    cleared, status = expected.get('cleared'), expected.get('status')
    if cleared is True and status == 'completed':
        return 'counted_completed', expected, []
    if cleared is False and status == 'pending':
        return 'counted_pending', expected, []
    return 'unresolved', expected, ['unknown_or_conflicting_completion']


def summarize_side(snapshot: dict, name: str, mapping: dict, accounts: dict,
                   start: datetime, end: datetime) -> list:
    rows = []
    scope = set(accounts)
    for i, row in enumerate(snapshot['records']):
        state, expected, issues = disposition(row, mapping, scope, start, end, name == 'source')
        result = {'index': i, 'state': state, 'issues': issues}
        if expected is not None:
            result.update(account=expected['account'], currency=expected['currency'])
        rows.append(result)
        if not state.startswith('counted_'):
            continue
        key = expected['account'], expected['currency']
        if key not in accounts:
            accounts[key] = empty_account(*key)
        flow = accounts[key][name]['completed' if state == 'counted_completed' else 'pending']
        amount = amount_decimal(expected['amount'])
        # Изменяются только новые локальные накопители отчёта.
        flow['count'] += 1
        flow['credit' if amount >= 0 else 'debit'] += amount
        flow['net'] += amount
    if len(rows) != len(snapshot['records']):
        raise RuntimeError('Не все операции получили результат контроля')
    return rows


def finalize_account(account: dict) -> dict:
    result = deepcopy(account)
    source, ledger = account['source'], account['ledger']
    result['completed_difference'] = str(source['completed']['net'] - ledger['completed']['net'])
    result['including_pending_difference'] = str(
        source['completed']['net'] + source['pending']['net']
        - ledger['completed']['net'] - ledger['pending']['net'])
    for side in ('source', 'ledger'):
        for stage in ('completed', 'pending'):
            result[side][stage] = {key: str(value) if isinstance(value, Decimal) else value
                                   for key, value in account[side][stage].items()}
    return result


def compare_movements(source: dict, ledger: dict, mapping: dict,
                      start: datetime, end: datetime) -> dict:
    if any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
           for value in (start, end)) or start >= end:
        raise ValueError('Нужен возрастающий период с часовыми поясами')
    validate_snapshot(source)
    validate_snapshot(ledger)
    validate_mapping(mapping)
    scope = {(target['account'], target['currency']) for accounts in mapping.values()
             for target in accounts.values()}
    accounts = {key: empty_account(*key) for key in sorted(scope)}
    source_rows = summarize_side(source, 'source', mapping, accounts, start, end)
    ledger_rows = summarize_side(ledger, 'ledger', mapping, accounts, start, end)
    records = [finalize_account(value) for value in accounts.values()]
    uncovered = sorted({(row['account'], row['currency']) for row in ledger_rows
                        if row['state'] == 'outside_source_scope'})
    issues = [{'side': side, **row} for side, rows in (('source', source_rows), ('ledger', ledger_rows))
              for row in rows if row['state'] == 'unresolved']
    return {'schema_version': 1, 'adapter': 'movement-control', 'records': records,
            'input_count': len(records), 'rejected': [], 'issues': issues, 'ready': False,
            'coverage': 'movement_comparison_only', 'period': {'start': start.isoformat(), 'end': end.isoformat()},
            'source_input_count': source['input_count'], 'ledger_input_count': ledger['input_count'],
            'source_dispositions': source_rows, 'ledger_dispositions': ledger_rows,
            'source_state_counts': dict(Counter(row['state'] for row in source_rows)),
            'ledger_state_counts': dict(Counter(row['state'] for row in ledger_rows)),
            'uncovered_ledger_accounts': [{'account': name, 'currency': unit} for name, unit in uncovered],
            'input_rejections': {'source': deepcopy(source['rejected']), 'ledger': deepcopy(ledger['rejected'])},
            'input_issues': {'source': deepcopy(source.get('issues', [])),
                             'ledger': deepcopy(ledger.get('issues', []))},
            'limitations': ['opening_and_closing_balances_unverified',
                            'per_account_source_coverage_unverified', 'net_equality_does_not_prove_completeness',
                            'source_dates_may_follow_accounting_rules', 'no_writes']}
