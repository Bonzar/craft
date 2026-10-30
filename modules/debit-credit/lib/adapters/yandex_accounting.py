"""Правила учёта Яндекс Банка. Ожидаемые записи ещё не являются планом записи."""

from copy import deepcopy
from datetime import datetime, timedelta

from .common import amount_decimal
from .matching import validate_records
from .review import validate_comparison

CARD_ACCOUNT = 'Карта Пэй'
ROUNDING_ACCOUNT = 'Округление'
# Так округления записаны в приложении: категория внутри «Яндекс Пэй».
ROUNDING_CATEGORY = ['Яндекс Пэй', 'Округление']
POINTS_ACCOUNT = 'ЯПлюс Баллы'
CASHBACK_DELAY_SECONDS = 120
ROUNDING_DELAY_SECONDS = 60
ACTIVE_STATUSES = {'completed', 'pending'}


def is_alias(row: dict) -> bool:
    return bool(row.get('resolution', {}).get('same_operation_as'))


def is_purchase_candidate(row: dict) -> bool:
    return (row['source'] == 'yabank' and row['account'] == CARD_ACCOUNT
            and row['currency'] == 'RUB' and amount_decimal(row['amount']) < 0
            and row.get('kind') != 'transfer_leg' and not row.get('other_account')
            and row['status'] in ACTIVE_STATUSES and not is_alias(row))


def rounding_kind(row: dict) -> str | None:
    fields = row.get('bank_fields', [])
    if not any(name == 'Округление' for name, _ in fields):
        return None
    amount = amount_decimal(row['amount'])
    if (row['account'], row.get('other_account')) == (CARD_ACCOUNT, ROUNDING_ACCOUNT) and amount < 0:
        return 'expense'
    if (row['account'], row.get('other_account')) == (ROUNDING_ACCOUNT, CARD_ACCOUNT) and amount > 0:
        return 'income'
    return None


def parent_candidates(row: dict, purchases: list, window: int) -> list:
    stamp = datetime.fromisoformat(row['timestamp'])
    result = []
    for purchase in purchases:
        before = datetime.fromisoformat(purchase['timestamp'])
        if not 0 <= (stamp - before).total_seconds() <= window:
            continue
        result.append({'source': purchase['source'], 'source_id': purchase['source_id'],
                       'timestamp': purchase['timestamp'], 'amount': purchase['amount'],
                       'description': purchase.get('description'),
                       'proposed_timestamp': (before + timedelta(seconds=ROUNDING_DELAY_SECONDS)).isoformat()})
    return result


def expected_rounding(row: dict, purchases: list, window: int) -> dict:
    kind = rounding_kind(row)
    if kind is None or row['status'] not in ACTIVE_STATUSES or is_alias(row):
        return deepcopy(row)
    result = deepcopy(row)
    result.update(kind=kind, other_account=None)
    result['accounting'] = {'rule': 'rounding', 'before': {
        key: row.get(key) for key in ('kind', 'other_account', 'timestamp')},
        'parent_candidates': parent_candidates(row, purchases, window),
        'parent_confirmed': False, 'unresolved_fields': ['timestamp', 'description', 'tag']}
    result['issues'] = [*row.get('issues', []), 'rounding_parent_requires_review']
    if kind == 'income':
        result.update(category='Округление', category_path=list(ROUNDING_CATEGORY))
    else:
        result['accounting']['unresolved_fields'].append('category')
    return result


def expected_cashback(row: dict) -> dict | None:
    hint = row.get('cashback_hint')
    if hint is None:
        return None
    amount = amount_decimal(hint)
    if amount < 0:
        raise ValueError('Кешбэк не может быть отрицательным')
    if (not amount or row['status'] not in ACTIVE_STATUSES or is_alias(row)
            or row.get('kind') in ('transfer', 'transfer_leg')
            or amount_decimal(row['amount']) >= 0 or row.get('role')
            or row.get('issues')):
        return None
    if not row['source_id']:
        raise ValueError('У покупки с кешбэком нужен ID')
    result = deepcopy(row)
    # Старые DOM-снимки не содержат отдельного статуса кешбэка.
    completed = row.get('cashback_cleared', True if row['status'] == 'completed' else None)
    if completed is not None and type(completed) is not bool:
        raise ValueError('Статус завершённости кешбэка должен быть логическим')
    result.update(role='cashback', locator=str(row['locator']) + ':cashback', native_id=None,
                  account=POINTS_ACCOUNT, other_account=None, kind='income',
                  amount=str(amount), currency='YPLUS_POINTS', category='Баллы',
                  category_path=['Баллы'], tag=None, markers=[], cashback_hint=None,
                  description='Кешбэк — ' + (row.get('description') or 'Покупка'), notes='',
                  timestamp=(datetime.fromisoformat(row['timestamp'])
                             + timedelta(seconds=CASHBACK_DELAY_SECONDS)).isoformat(),
                  status='completed' if completed else 'pending' if completed is False else 'unknown',
                  cleared=completed,
                  issues=[] if completed is not None else ['cashback_completion_requires_review'])
    result['accounting'] = {'rule': 'cashback', 'derived': True,
                            'purchase_id': row['source_id'], 'purchase_timestamp': row['timestamp'],
                            'unresolved_fields': ['tag']}
    result.update(bank_account_ids=[], bank_fields=[], bank_category=None, bank_comment=None, mcc=None)
    result.pop('resolution', None)
    result.pop('account_evidence', None)
    return result


def validate_snapshot(snapshot: dict, window: int) -> None:
    if (not isinstance(snapshot, dict) or snapshot.get('adapter') == 'yandex-accounting'
            or not isinstance(snapshot.get('records'), list)
            or not isinstance(snapshot.get('rejected'), list)
            or type(snapshot.get('input_count')) is not int
            or snapshot['input_count'] != len(snapshot['records']) + len(snapshot['rejected'])):
        raise ValueError('Нужен исходный результат адаптера без прежнего разворота правил')
    if type(window) is not int or window < 0:
        raise ValueError('Окно поиска покупки — целое неотрицательное число секунд')
    validate_records(snapshot['records'])
    if any(row['source'] != 'yabank' or 'accounting' in row for row in snapshot['records']):
        raise ValueError('Правила применяются к исходным операциям Яндекс Банка')


def expand_accounting(snapshot: dict, *, parent_window_seconds: int) -> dict:
    validate_snapshot(snapshot, parent_window_seconds)
    source = snapshot['records']
    purchases = [row for row in source if is_purchase_candidate(row)]
    records = [expected_rounding(row, purchases, parent_window_seconds) for row in source]
    origins = [{'input_index': i, 'record_indices': [i]} for i in range(len(source))]
    for i, row in enumerate(source):
        derived = expected_cashback(row)
        if derived is not None:
            origins[i]['record_indices'].append(len(records))
            records.append(derived)
    validate_records(records)
    if sorted(i for origin in origins for i in origin['record_indices']) != list(range(len(records))):
        raise RuntimeError('Нарушен счёт исходных и производных записей')
    issues = deepcopy(snapshot.get('issues', []))
    for row in records:
        for code in row.get('issues', []):
            issue = {'locator': row['locator'], 'code': code}
            if issue not in issues:
                issues.append(issue)
    derived_count = len(records) - len(source)
    return {**deepcopy(snapshot), 'adapter': 'yandex-accounting', 'records': records,
            'source_input_count': snapshot['input_count'], 'source_record_count': len(source),
            'input_count': snapshot['input_count'] + derived_count,
            'derived_count': derived_count, 'origins': origins, 'issues': issues,
            'parent_window_seconds': parent_window_seconds, 'ready': False,
            'limitations': [*snapshot.get('limitations', []), 'cashback_credit_aliases_not_inferred',
                            'rounding_parent_candidates_not_confirmed', 'no_writes']}


def cashback_confirmation(row: dict, ledger: list) -> tuple:
    source = row['record']
    if source.get('role') != 'cashback' or not source.get('accounting', {}).get('derived'):
        return 'not_derived_cashback', None
    if source.get('cleared') is False:
        return 'keep_preliminary', None
    if source.get('cleared') is not True or row['issues'] or row['match'] != 'marker' or len(row['ledger_indices']) != 1:
        return 'requires_review', None
    position = row['ledger_indices'][0]
    actual = ledger[position]
    changes = row['differences'].get(position, row['differences'].get(str(position), {}))
    if (source.get('source') != 'yabank' or source.get('account') != POINTS_ACCOUNT
            or source.get('currency') != 'YPLUS_POINTS'
            or source.get('status') != 'completed' or source.get('kind') != 'income'
            or actual.get('kind') != 'income' or actual.get('splits')
            or actual.get('eligible_for_matching') is False
            or not any(m['source'] == source['source'] and m['id'] == source['source_id']
                       and m.get('role') == 'cashback' for m in actual.get('markers', []))
            or (actual.get('account'), actual.get('currency')) != (POINTS_ACCOUNT, 'RUB')
            or not changes.keys() <= {'amount', 'cleared'}):
        return 'requires_review', None
    if actual.get('cleared') is True:
        return ('already_confirmed' if not changes else 'requires_review'), None
    if actual.get('cleared') is not False or amount_decimal(source['amount']) <= 0:
        return 'requires_review', None
    return 'confirm_existing', {'locator': actual['locator'],
                                'set': {'amount': source['amount'], 'cleared': True}}


def plan_cashback_confirmations(comparison: dict) -> dict:
    """Выбирает только переход предварительного дохода в подтверждённый по точной метке."""
    validate_comparison(comparison)
    decisions, updates = [], []
    for index, row in enumerate(comparison['records']):
        action, update = cashback_confirmation(row, comparison['ledger_records'])
        decisions.append({'input_index': index, 'action': action})
        if update is not None:
            updates.append(update)
    if len({row['locator'] for row in updates}) != len(updates):
        raise ValueError('Несколько подтверждений ссылаются на одну запись приложения')
    return {'input_count': comparison['input_count'], 'decisions': decisions, 'updates': updates,
            'rejected': deepcopy(comparison['rejected']), 'ready': False,
            'limitations': ['proposal_only', 'does_not_add_or_write_operations']}
