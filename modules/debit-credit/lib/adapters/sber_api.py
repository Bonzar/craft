"""История операций Сбера в общем формате. Ни одна прочитанная операция не теряется."""

import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .common import record_base, report

SOURCE = 'sber'
BONUS_ACCOUNT = 'СберСпасибо'
BONUS_CURRENCY = 'SBER_SPASIBO'
OWN_TRANSFER_FORM = 'UfsTransferSelf'
MOSCOW = ZoneInfo('Europe/Moscow')
CURRENCY_ALIASES = {'RUR': 'RUB'}
STATE_NAMES = {'AUTHORIZATION': ('pending', False)}
CATEGORIES = {'executed': ('completed', True), 'cancel': ('failed', None)}


def text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Ожидается непустая строка истории')
    return value


def read_time(value) -> datetime:
    match = re.fullmatch(r'(\d{2})\.(\d{2})\.(\d{4})T(\d{2}):(\d{2}):(\d{2})', text(value))
    if not match:
        raise ValueError('Не распознана дата операции')
    day, month, year, hour, minute, second = map(int, match.groups())
    return datetime(year, month, day, hour, minute, second, tzinfo=MOSCOW)


def read_money(value: dict) -> tuple[Decimal, str]:
    if not isinstance(value, dict) or type(value.get('amount')) not in (int, float):
        raise ValueError('Не распознана сумма операции')
    currency = CURRENCY_ALIASES.get(value.get('currencyCode'), value.get('currencyCode'))
    if not isinstance(currency, str) or not re.fullmatch('[A-Z]{3}', currency):
        raise ValueError('Некорректный код валюты')
    return Decimal(str(value['amount'])), currency


def read_bonus(bonuses) -> tuple[Decimal, str]:
    if not isinstance(bonuses, list) or len(bonuses) != 1 or bonuses[0].get('currency') != BONUS_CURRENCY:
        raise ValueError('Не распознаны баллы операции')
    item = bonuses[0]
    if type(item.get('income', 0)) not in (int, float) or type(item.get('outcome', 0)) not in (int, float):
        raise ValueError('Не распознано число баллов')
    return Decimal(str(item.get('income', 0))) - Decimal(str(item.get('outcome', 0))), BONUS_CURRENCY


def resource(raw: dict, key: str) -> dict:
    value = raw.get(key) or {}
    return value if isinstance(value, dict) and isinstance(value.get('id'), str) and value['id'] else {}


def status(raw: dict) -> tuple[str, bool | None]:
    state = raw.get('state') or {}
    return STATE_NAMES.get(state.get('name')) or CATEGORIES.get(state.get('category'), ('unknown', None))


def sides(raw: dict, amount: Decimal) -> tuple[dict, dict]:
    """Свой счёт — сторона, с которой ушли деньги, или сторона, куда они пришли."""
    source, target = resource(raw, 'fromResource'), resource(raw, 'toResource')
    return (source, target) if amount < 0 else (target, source)


def parse_operation(raw: dict, index: int) -> dict:
    identifier = text(raw.get('uohId'))
    timestamp = read_time(raw.get('date'))
    row_status, cleared = status(raw)
    reason = 'failed_operation' if row_status == 'failed' else None
    if raw.get('bonuses'):
        amount, currency = read_bonus(raw['bonuses'])
        own, other, kind = {'id': BONUS_ACCOUNT, 'displayedValue': BONUS_ACCOUNT}, {}, None
    elif raw.get('operationAmount') is None and raw.get('isFinancial') is False:
        amount, currency, own, other, kind = Decimal(0), None, {}, {}, None
        reason = reason or 'non_financial'
    else:
        amount, currency = read_money(raw.get('operationAmount'))
        own, other = sides(raw, amount)
        if not own:
            raise ValueError('Не определён свой счёт операции')
        kind = 'transfer' if raw.get('form') == OWN_TRANSFER_FORM and other else None
    issues = ['unknown_status'] if row_status == 'unknown' else []
    row = {**record_base(SOURCE, index + 1), 'source_id': identifier,
           'timestamp': timestamp.isoformat(), 'timestamp_precision': 'second',
           'amount': str(amount), 'currency': currency, 'account': own.get('id'),
           'account_name': own.get('displayedValue'),
           'other_account': other.get('id') if kind == 'transfer' else None,
           'counterparty_resource': other.get('id') if kind != 'transfer' else None,
           'kind': kind, 'status': row_status, 'cleared': cleared,
           'description': raw.get('correspondent') or raw.get('description') or '',
           'bank_category': raw.get('description'), 'issues': issues, 'raw': raw}
    if reason:
        row['not_recorded_reason'] = reason
    return row


def coverage_complete(snapshot: dict) -> bool:
    observations = snapshot.get('observations')
    return (snapshot.get('complete') is True and not snapshot.get('excluded')
            and not snapshot.get('conflicts') and not snapshot.get('repeatedObservations')
            and isinstance(observations, list) and observations == snapshot['records'])


def read_history(snapshot: dict) -> dict:
    if (not isinstance(snapshot, dict) or snapshot.get('adapter') != 'sber-operations'
            or snapshot.get('schema_version') != 1 or not isinstance(snapshot.get('records'), list)):
        raise ValueError('Ожидается JSON сборщика истории Сбера')
    result = report('sber-api', snapshot['records'], parse_operation)
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
                  period=snapshot.get('period'), source=SOURCE,
                  limitations=['statement_only_charges_not_read', 'categories_and_tags_not_assigned',
                               'no_writes'])
    return result
