"""Полная сверка операций и счетов после восстановления с другими внутренними ID."""

import json
from collections import Counter
from datetime import datetime, timezone

from .common import amount_decimal

ROW_FIELDS = ('currency', 'account', 'other_account', 'kind', 'cleared', 'description',
              'notes', 'category', 'tag', 'payee', 'category_path', 'reconciled', 'upcoming')
ACCOUNT_FIELDS = ('name', 'currency', 'hidden', 'deleted_locally')
BALANCE_FIELDS = ('balance', 'uncleared_balance', 'last_reconciled_balance')


def freeze(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def money(value: str) -> str:
    return format(amount_decimal(value).normalize(), 'f')


def project_record(row: dict) -> dict:
    result = {key: row[key] for key in ROW_FIELDS}
    stamp = datetime.fromisoformat(row['timestamp'])
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('Для сверки нужна дата с часовым поясом')
    result.update(amount=money(row['amount']), timestamp=stamp.astimezone(timezone.utc).isoformat(),
                  deleted=row.get('raw', {}).get('deleted'),
                  splits=sorted([{'amount': money(s['amount']), 'category_path': s['category_path']}
                                 for s in row['splits']], key=freeze))
    return result


def validate_snapshot(data: dict) -> dict:
    if (data.get('adapter') != 'debit-credit-backup' or data.get('coverage') != 'backup_snapshot'
            or data.get('date_filter')):
        raise ValueError('Нужны полные снимки резервных копий')
    if data.get('input_count') != len(data['records']) + len(data['rejected']):
        raise ValueError('Не сошёлся счёт строк снимка')
    index = {}
    for row in data['records']:
        key = row.get('locator')
        if type(key) is not int or key <= 0 or key in index:
            raise ValueError('Нужны уникальные локальные указатели внутри снимка')
        index[key] = row
    return index


def transaction_content(data: dict, side: str) -> tuple[Counter, list]:
    index = validate_snapshot(data)
    projected = {key: project_record(row) for key, row in index.items()}
    contents, issues = [], []
    for key, row in index.items():
        peer = index.get(row.get('linked_locator'))
        if row['kind'] == 'transfer_leg':
            if (not peer or peer.get('linked_locator') != key or peer['kind'] != 'transfer_leg'
                    or peer['account'] != row['other_account']
                    or peer['other_account'] != row['account']):
                issues.append({'code': 'invalid_transfer_link', 'snapshot': side, 'locator': key})
        elif row.get('linked_locator') is not None or row['other_account'] is not None:
            issues.append({'code': 'unexpected_transfer_link', 'snapshot': side, 'locator': key})
        contents.append(freeze({'record': projected[key],
                                'peer': projected[peer['locator']] if peer else None}))
    return Counter(contents), issues


def account_content(data: dict, side: str) -> tuple[Counter, list]:
    rows, identities = [], []
    for account in data['accounts']:
        rows.append(freeze({**{k: account.get(k) for k in ACCOUNT_FIELDS},
                            **{k: money(account[k]) for k in BALANCE_FIELDS}}))
        identities.append((account['name'], account['currency']))
    issues = ([{'code': 'ambiguous_account_identity', 'snapshot': side}]
              if len(set(identities)) != len(identities) else [])
    return Counter(rows), issues


def verify_restoration(expected: dict, actual: dict) -> dict:
    """Сравнивает содержимое с кратностью, а не назначает новые ID старым записям."""
    first, first_issues = transaction_content(expected, 'expected')
    last, last_issues = transaction_content(actual, 'actual')
    accounts_before, account_issues = account_content(expected, 'expected')
    accounts_after, other_account_issues = account_content(actual, 'actual')
    issues = [*first_issues, *last_issues, *account_issues, *other_account_issues]
    if expected['rejected'] or actual['rejected'] or expected['issues'] or actual['issues']:
        issues.append({'code': 'snapshot_reader_errors'})
    missing, unexpected = first - last, last - first
    if missing or unexpected:
        issues.append({'code': 'transaction_content_differs'})
    if accounts_before != accounts_after:
        issues.append({'code': 'account_content_differs'})
    rows = [{'content': json.loads(key), 'expected_count': first[key], 'actual_count': last[key]}
            for key in sorted(first.keys() | last.keys())]
    assert sum(r['expected_count'] for r in rows) == len(expected['records'])
    assert sum(r['actual_count'] for r in rows) == len(actual['records'])
    return {'schema_version': 1, 'adapter': 'restore-verification',
            'input_count': expected['input_count'], 'actual_input_count': actual['input_count'],
            'records': rows, 'rejected': [], 'issues': issues, 'ready': not issues,
            'missing_record_count': sum(missing.values()), 'unexpected_record_count': sum(unexpected.values()),
            'account_content_equal': accounts_before == accounts_after,
            'coverage': 'full_transaction_and_account_content',
            'limitations': ['does_not_reconcile_bank_balances', 'does_not_verify_cloud_sync',
                            'does_not_verify_budgets_plans_or_attachment_files',
                            'does_not_assign_cross_snapshot_record_identity', 'no_writes']}
