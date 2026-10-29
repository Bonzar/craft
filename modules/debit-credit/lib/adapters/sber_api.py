"""История операций Сбера в общем формате. Ни одна прочитанная операция не теряется."""

import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .balances import read_balances
from .common import record_base, report

SOURCE = 'sber'
BONUS_ACCOUNT = 'СберСпасибо'
BONUS_CURRENCY = 'SBER_SPASIBO'
OWN_TRANSFER_FORM = 'UfsTransferSelf'
MOSCOW = ZoneInfo('Europe/Moscow')
CURRENCY_ALIASES = {'RUR': 'RUB'}
STATE_NAMES = {'AUTHORIZATION': ('pending', False)}
CATEGORIES = {'executed': ('completed', True), 'cancel': ('failed', None)}
STATEMENT_WINDOW_SECONDS = 600


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
        kind = 'transfer' if raw.get('form') == OWN_TRANSFER_FORM else None
    # У заявки без денег статус ни на что не влияет.
    issues = ['unknown_status'] if row_status == 'unknown' and reason != 'non_financial' else []
    if kind == 'transfer' and not other:
        issues.append('transfer_counterpart_unknown')
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


def same_operation(row: dict, record: dict, card: str) -> bool:
    amount = Decimal(row['amount'])
    sided = ((record['account'] == card and Decimal(record['amount']) == amount)
             or (record['other_account'] == card and Decimal(record['amount']) == -amount))
    gap = abs((datetime.fromisoformat(record['timestamp'])
               - datetime.fromisoformat(row['timestamp'])).total_seconds())
    return sided and gap <= STATEMENT_WINDOW_SECONDS


def statement_record(row: dict, card: str, name: str, suffix: str, locator: int) -> dict:
    stamp = datetime.fromisoformat(row['timestamp']).strftime('%Y%m%d%H%M')
    identifier = f"statement:{suffix}:{stamp}:{row['authorization_code']}:{row['amount']}"
    return {**record_base(SOURCE, locator), 'source_id': identifier, 'timestamp': row['timestamp'],
            'timestamp_precision': 'minute', 'amount': row['amount'], 'currency': 'RUB',
            'account': card, 'account_name': name, 'other_account': None,
            'counterparty_resource': None, 'kind': None, 'status': 'completed', 'cleared': True,
            'description': row['description'], 'bank_category': row['category'],
            'statement_only': True, 'issues': [], 'raw': row}


def merge_statement(result: dict, statement: dict) -> dict:
    """В историю добавляются только операции выписки, которых в ней нет."""
    suffix = statement['card_suffix']
    cards = {(r['account'], r['account_name']) for r in result['records']
             if (r.get('account_name') or '').endswith(suffix) and not r.get('statement_only')}
    if len(cards) != 1:
        result['issues'].append({'code': 'statement_card_not_in_history', 'card_suffix': suffix})
        return {'card_suffix': suffix, 'operations': len(statement['operations']), 'matched': 0, 'added': 0}
    card, name = cards.pop()
    used, matched, added = set(), 0, 0
    for row in statement['operations']:
        found = next((r for r in result['records'] if id(r) not in used and not r.get('statement_only')
                      and same_operation(row, r, card)), None)
        if found is not None:
            used.add(id(found))
            matched += 1
            continue
        result['records'].append(statement_record(row, card, name, suffix, len(result['records']) + 1))
        added += 1
    return {'card_suffix': suffix, 'operations': len(statement['operations']), 'matched': matched,
            'added': added}


def read_history(snapshot: dict, statements: list | None = None) -> dict:
    if (not isinstance(snapshot, dict) or snapshot.get('adapter') != 'sber-operations'
            or snapshot.get('schema_version') != 1 or not isinstance(snapshot.get('records'), list)):
        raise ValueError('Ожидается JSON сборщика истории Сбера')
    result = report('sber-api', snapshot['records'], parse_operation)
    result['statement_checks'] = [merge_statement(result, statement) for statement in statements or []]
    result['input_count'] += sum(check['added'] for check in result['statement_checks'])
    counts = Counter(row['source_id'] for row in result['records'])
    for row in result['records']:
        if counts[row['source_id']] > 1:
            row['issues'].append('duplicate_source_id')
        result['issues'].extend({'locator': row['locator'], 'code': code} for code in row['issues'])
    result['balances'], balance_issues = read_balances(snapshot)
    result['issues'].extend(balance_issues)
    is_complete = coverage_complete(snapshot)
    if not is_complete:
        result['issues'].append({'code': 'collection_coverage_unverified'})
    result.update(coverage='snapshot_complete' if is_complete else 'unverified',
                  ready=not result['rejected'] and not result['issues'],
                  period=snapshot.get('period'), source=SOURCE,
                  limitations=['categories_and_tags_not_assigned',
                               'no_writes'])
    return result
