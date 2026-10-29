"""Остатки своих счетов, прочитанные сборщиком до и после истории."""

import re
from decimal import Decimal

AMOUNT = re.compile(r'-?\d+(\.\d+)?')
CURRENCY = re.compile(r'[A-Z][A-Z_]*')
REVIEW = {'code': 'balances_require_review'}


def valid_accounts(accounts) -> bool:
    if not isinstance(accounts, list) or not accounts:
        return False
    names = [row.get('account') if isinstance(row, dict) else None for row in accounts]
    return (all(isinstance(name, str) and name for name in names) and len(set(names)) == len(names)
            and all((row.get('account_name') is None or isinstance(row.get('account_name'), str))
                    and isinstance(row.get('amount'), str) and AMOUNT.fullmatch(row['amount'])
                    and isinstance(row.get('currency'), str) and CURRENCY.fullmatch(row['currency'])
                    for row in accounts))


def amounts(accounts: list) -> dict:
    return {row['account']: (Decimal(row['amount']), row['currency']) for row in accounts}


def read_balances(snapshot: dict) -> tuple[dict | None, list]:
    """Остаток на втором чтении; движение между чтениями и ошибки банка — в issues."""
    if 'balances' not in snapshot:
        return None, []
    value = snapshot['balances']
    phases = [value.get(name) if isinstance(value, dict) else None for name in ('before', 'after')]
    if not all(isinstance(phase, dict) for phase in phases):
        return {'accounts': None, 'observed_at': None, 'errors': []}, [REVIEW]
    before, after = phases
    errors = [phase['error'] for phase in phases if phase.get('error')]
    result = {'accounts': None, 'observed_at': after.get('observed_at'), 'errors': errors}
    if errors:
        return result, [{'code': 'balances_unread'}]
    if not valid_accounts(before.get('accounts')) or not valid_accounts(after.get('accounts')):
        return result, [REVIEW]
    result['accounts'] = [{key: row[key] for key in ('account', 'account_name', 'amount', 'currency')}
                          for row in after['accounts']]
    moved = amounts(before['accounts']) != amounts(after['accounts'])
    return result, [{'code': 'balance_changed_during_read'}] if moved else []
