"""Лента своих счетов Т-Банка в общем формате. Ни одна прочитанная операция не теряется."""

import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

from .common import record_base, report

SOURCE = 'tbank'
KUBYSHKA_ACCOUNT = 'Кубышка'
KUBYSHKA_DESCRIPTIONS = {'Перевод средств из Кубышки': 'Credit', 'Пополнение Кубышки': 'Debit'}
INNER_PROVIDER = 'transfer-inner'
PAIR_WINDOW_SECONDS = 600
STATUSES = {'OK': ('completed', True), 'FAILED': ('failed', None)}
SIGNS = {'Debit': -1, 'Credit': 1}


def text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Ожидается непустая строка ленты')
    return value


def read_money(value: dict) -> tuple[Decimal, str]:
    if not isinstance(value, dict) or type(value.get('value')) not in (int, float):
        raise ValueError('Не распознана сумма ленты')
    currency = text(value.get('currency', {}).get('name'))
    if not re.fullmatch('[A-Z]{3}', currency):
        raise ValueError('Некорректный код валюты ленты')
    amount = Decimal(str(value['value']))
    if amount < 0:
        raise ValueError('Знак суммы ленты задаётся типом операции')
    return amount, currency


def read_time(value: dict) -> datetime:
    milliseconds = value.get('milliseconds') if isinstance(value, dict) else None
    if type(milliseconds) is not int:
        raise ValueError('Не распознано время операции')
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)


def counterpart(raw: dict, kind: str) -> tuple[str | None, str | None]:
    """Второй счёт перевода: Кубышка по описанию или свой счёт из реквизитов перевода."""
    if KUBYSHKA_DESCRIPTIONS.get(raw.get('description')) == kind:
        return 'transfer', KUBYSHKA_ACCOUNT
    payment = raw.get('payment') or {}
    if payment.get('providerId') != INNER_PROVIDER:
        return None, None
    if kind == 'Debit':
        other = (payment.get('fieldsValues') or {}).get('bankContract')
    else:
        other = raw.get('senderAgreement') or payment.get('bankAccountId')
    return 'transfer', other if isinstance(other, str) and other else None


def parse_operation(raw: dict, index: int, accounts: set) -> dict:
    identifier, account = text(raw.get('id')), text(raw.get('account'))
    if account not in accounts:
        raise ValueError('Операция не своего счёта: такой счёт не запрашивался')
    kind_name = raw.get('type')
    if kind_name not in SIGNS:
        raise ValueError('Не распознано направление операции')
    amount, currency = read_money(raw.get('accountAmount'))
    status, cleared = STATUSES.get(raw.get('status'), ('unknown', None))
    kind, other = counterpart(raw, kind_name)
    issues = []
    if status == 'unknown':
        issues.append('unknown_status')
    if kind == 'transfer' and other is None:
        issues.append('transfer_counterpart_unknown')
    row = {**record_base(SOURCE, index + 1), 'source_id': identifier,
           'timestamp': read_time(raw.get('operationTime')).isoformat(), 'timestamp_precision': 'second',
           'amount': str(amount * SIGNS[kind_name]), 'currency': currency, 'account': account,
           'other_account': other, 'kind': kind, 'status': status, 'cleared': cleared,
           'description': raw.get('description') or '',
           'bank_category': (raw.get('category') or {}).get('name'), 'mcc': raw.get('mcc'),
           'issues': issues, 'raw': raw}
    if status == 'failed':
        row['not_recorded_reason'] = 'failed_operation'
    elif (raw.get('operationId') or {}).get('source') == 'LoyaltyBonus' and amount == 0:
        row['not_recorded_reason'] = 'zero_cashback'
    return row


def is_inner(row: dict) -> bool:
    return (row['raw'].get('payment') or {}).get('providerId') == INNER_PROVIDER


def seconds_apart(first: dict, second: dict) -> float:
    return abs((datetime.fromisoformat(first['timestamp'])
                - datetime.fromisoformat(second['timestamp'])).total_seconds())


def pair_transfers(records: list, accounts: set) -> None:
    """Зачисление на свой счёт связывается со списанием той же операции перевода."""
    debits = [r for r in records if is_inner(r) and Decimal(r['amount']) < 0]
    credits = [r for r in records if is_inner(r) and Decimal(r['amount']) > 0]
    paired = set()
    for debit in debits:
        candidates = [c for c in credits if id(c) not in paired and c['account'] == debit['other_account']
                      and c['other_account'] == debit['account']
                      and Decimal(c['amount']) == -Decimal(debit['amount'])
                      and c['status'] == debit['status'] and seconds_apart(c, debit) <= PAIR_WINDOW_SECONDS]
        if len(candidates) == 1:
            credit = candidates[0]
            paired.update((id(credit), id(debit)))
            # Меняется только новая запись результата, исходная операция остаётся в raw.
            credit['resolution'] = {'same_operation_as': {'source': SOURCE, 'id': debit['source_id']},
                                    'reason': 'tbank_own_transfer_pair'}
        elif len(candidates) > 1:
            debit['issues'].append('ambiguous_transfer_pair')
    for row in debits + credits:
        if id(row) not in paired and row['other_account'] in accounts and row['status'] == 'completed':
            row['issues'].append('transfer_pair_not_found')


def coverage_complete(snapshot: dict) -> bool:
    observations = snapshot.get('observations')
    return (snapshot.get('complete') is True and not snapshot.get('excluded')
            and not snapshot.get('conflicts') and not snapshot.get('repeatedObservations')
            and isinstance(observations, list) and observations == snapshot['records'])


def read_history(snapshot: dict) -> dict:
    if (not isinstance(snapshot, dict) or snapshot.get('adapter') != 'tbank-timeline'
            or snapshot.get('schema_version') != 1 or not isinstance(snapshot.get('records'), list)
            or not isinstance(snapshot.get('accounts'), list)):
        raise ValueError('Ожидается JSON сборщика ленты Т-Банка')
    accounts = set(snapshot['accounts'])
    result = report('tbank-api', snapshot['records'],
                    lambda raw, index: parse_operation(raw, index, accounts))
    pair_transfers(result['records'], accounts)
    counts = Counter(row['source_id'] for row in result['records'])
    for row in result['records']:
        if counts[row['source_id']] > 1:
            row['issues'].append('duplicate_source_id')
        result['issues'].extend({'locator': row['locator'], 'code': code} for code in row['issues'])
    is_complete = coverage_complete(snapshot)
    if not is_complete:
        result['issues'].append({'code': 'collection_coverage_unverified'})
    result.update(coverage='snapshot_complete' if is_complete else 'unverified',
                  ready=not result['rejected'] and not result['issues'],
                  period=snapshot.get('period'), accounts=sorted(accounts), source=SOURCE,
                  limitations=['categories_and_tags_not_assigned', 'no_writes'])
    return result
