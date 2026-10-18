"""JSON читающего сборщика. ID списка и собственный ID карточки не смешиваются."""

import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from .common import amount_decimal, normalize_space, record_base, report

STATUSES = {'CLEAR': ('completed', True), 'HOLD': ('pending', False),
            'CANCEL': ('canceled', None), 'FAIL': ('failed', None)}
ACCOUNT_PHRASES = {'со счёта в сейвах': 'Счёт в Сейвах',
                   'на счёт в сейвах': 'Счёт в Сейвах',
                   'с карты пэй': 'Карта Пэй', 'на карту пэй': 'Карта Пэй',
                   'с вклада в сейвах': 'Вклад в Сейвах', 'на вклад в сейвах': 'Вклад в Сейвах'}


def text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Ожидается непустая строка API')
    return value


def read_amount(value: dict, direction: str) -> tuple:
    if not isinstance(value, dict) or direction not in ('CREDIT', 'DEBIT'):
        raise ValueError('Не определены сумма и направление API')
    money, points = value.get('money'), value.get('plus')
    if (money is None) == (points is None):
        raise ValueError('Нужна одна единица суммы: деньги или баллы')
    if money is not None:
        currency = text(money['currency'])
        if not re.fullmatch('[A-Z]{3}', currency):
            raise ValueError('Некорректный код валюты API')
        amount = amount_decimal(text(money['amount']))
    else:
        currency, amount = 'YPLUS_POINTS', amount_decimal(text(points))
    if amount < 0:
        raise ValueError('Знак суммы API должен задаваться направлением')
    return amount * (1 if direction == 'CREDIT' else -1), currency


def read_fields(detail: dict) -> list:
    groups = detail.get('additionalFieldsList')
    if not groups and detail.get('additionalFields'):
        groups = [detail['additionalFields']]
    if groups is None:
        return []
    if not isinstance(groups, list):
        raise ValueError('Не распознаны группы реквизитов API')
    fields = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get('additionalFields'), list):
            raise ValueError('Не распознаны реквизиты API')
        for item in group['additionalFields']:
            fields.append((text(item['name']), text(item['value'])))
    return fields


def read_direction(detail: dict, fields: list) -> tuple:
    direction = detail.get('direction')
    amount = detail['amount']
    if direction is None and amount.get('money') is None and amount.get('plus') is not None:
        purposes = [value for name, value in fields if name == 'Назначение']
        if len(purposes) == 1 and re.fullmatch(r'Списание баллов(?: от .+)?',
                                             normalize_space(purposes[0]), re.I):
            return 'DEBIT', {'field': 'Назначение', 'value': purposes[0]}
    return direction, None


def account_phrase(value: str) -> str:
    value = normalize_space(value)
    if value.lower() in ACCOUNT_PHRASES:
        return ACCOUNT_PHRASES[value.lower()]
    return re.sub(r'^(?:На|Со|С) ', '', value, count=1, flags=re.I)


def read_accounts(title: dict, fields: list, amount: Decimal, currency: str) -> tuple:
    compound = title.get('compound')
    if compound is not None:
        first, second = text(compound['firstPart']), text(compound['secondPart'])
        return (first, second, 'transfer_leg') if amount < 0 else (second, first, 'transfer_leg')
    if currency == 'YPLUS_POINTS':
        return 'ЯПлюс Баллы', None, None
    values = {value if name == 'Способ оплаты' else account_phrase(value)
              for name, value in fields if name in ('Способ оплаты', 'Пополнение')}
    if not values and amount < 0:
        values = {account_phrase(value) for name, value in fields if name == 'Перевод'
                  and normalize_space(value).lower().startswith(('с ', 'со '))}
    return (values.pop(), None, None) if len(values) == 1 else (None, None, None)


def cashback_hint(detail: dict) -> tuple:
    cashback = detail.get('cashback')
    if cashback is None:
        return None, []
    try:
        services = cashback['servicesCashback']
        if cashback.get('summary') is not None or not isinstance(services, list) or not services:
            return None, ['cashback_structure_requires_review']
        amounts, issues = [], []
        for service in services:
            info = service['cashbackInfo']
            if info['partialValue'] is not None or info['totalValue']['money'] is not None:
                return None, ['cashback_structure_requires_review']
            amount, unit = read_amount(info['totalValue'], info['direction'])
            if unit != 'YPLUS_POINTS' or amount < 0:
                return None, ['cashback_structure_requires_review']
            amounts.append(amount)
            if info['cashbackStatus'] != 'FULLY':
                issues.append('cashback_status_requires_review')
        return str(sum(amounts, Decimal(0))), issues
    except (KeyError, TypeError, ValueError):
        return None, ['cashback_structure_requires_review']


def bank_account_ids(detail: dict) -> list:
    money = detail['amount'].get('money') or {}
    values = {money['agreementId']} if money.get('agreementId') else set()
    for action in detail.get('actions') or []:
        value = action.get('action')
        if not isinstance(value, str):
            continue
        url = urlsplit(value)
        if url.scheme == 'yandexbank' and url.netloc == 'screen.open' and url.path == '/pdf_load':
            ids = parse_qs(url.query).get('agreement_id', [])
            if len(ids) == 1 and ids[0]:
                values.add(ids[0])
    return sorted(values)


def resolve_accounts(records: list) -> dict:
    own_names = set()
    for row in records:
        if row['kind'] == 'transfer_leg':
            own_names.update((row['account'], row['other_account']))
        own_names.update(account_phrase(value) for name, value in row['bank_fields'] if name == 'Пополнение')
    profiles = defaultdict(dict)
    for row in records:
        if row['account'] in own_names:
            for identifier in row['bank_account_ids']:
                profiles[identifier].setdefault(row['account'], row['source_id'])
    # Меняются только новые записи результата, не исходный JSON.
    for row in records:
        if row['currency'] == 'YPLUS_POINTS' or row['account'] in own_names:
            continue
        candidates = {}
        for identifier in row['bank_account_ids']:
            candidates.update(profiles.get(identifier, {}))
        if len(candidates) == 1:
            name, witness = next(iter(candidates.items()))
            row['account_evidence'] = {'kind': 'receipt_agreement', 'witness_ids': [witness],
                                       'original_account_hint': row['account']}
            row['account'] = name
            row['issues'] = [code for code in row['issues'] if code != 'unknown_account']
        elif len(candidates) > 1:
            row['issues'].append('ambiguous_bank_account_identity')
    return {key: {'names': list(names), 'witness_ids': list(names.values())}
            for key, names in profiles.items()}


def parse_record(raw: dict, index: int) -> dict:
    feed, details = raw['feed'], raw['details']
    source_id = text(feed['id'])
    if len(details) != 1:
        raise ValueError('Нужна одна карточка на каждый ID списка')
    detail = details[0]
    if detail['requestedId'] != source_id:
        raise ValueError('Карточка не связана с запросом исходного ID')
    native_id = text(detail['id'])
    timestamp = datetime.fromisoformat(text(detail['date']))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError('В дате API отсутствует часовой пояс')
    fields = read_fields(detail)
    direction, direction_evidence = read_direction(detail, fields)
    amount, currency = read_amount(detail['amount'], direction)
    title = detail['title']
    compound = title.get('compound')
    description = (text(compound['firstPart']) + ' → ' + text(compound['secondPart'])
                   if compound else text(title['plain']))
    account, other, kind = read_accounts(title, fields, amount, currency)
    status, cleared = STATUSES.get(detail['statusCode'], ('unknown', None))
    hint, issues = cashback_hint(detail)
    if not account:
        issues.append('unknown_account')
    if status == 'unknown':
        issues.append('unknown_status')
    feed_direction = feed['direction']
    if feed_direction is None and direction_evidence and feed['amount'].get('money') is None:
        feed_direction = direction
    if read_amount(feed['amount'], feed_direction) != (amount, currency):
        issues.append('feed_detail_amount_differs')
    if datetime.fromisoformat(text(feed['date'])) != timestamp:
        issues.append('feed_detail_date_differs')
    if feed['statusCode'] != detail['statusCode']:
        issues.append('feed_detail_status_differs')
    fraction = re.search(r'\.(\d+)', detail['date'])
    precision = ('millisecond' if len(fraction[1]) <= 3 else 'microsecond') if fraction else 'second'
    return {**record_base('yabank', index + 1), 'source_id': source_id, 'native_id': native_id,
            'bank_account_ids': bank_account_ids(detail),
            'direction_evidence': direction_evidence,
            'timestamp': timestamp.isoformat(), 'timestamp_precision': precision,
            'amount': str(amount), 'currency': currency, 'account': account, 'other_account': other,
            'kind': kind, 'status': status, 'cleared': cleared, 'description': description,
            'bank_fields': fields, 'bank_category': dict(fields).get('Категория'),
            'mcc': dict(fields).get('MCC'), 'cashback_hint': hint, 'issues': issues,
            'raw': {'feed': feed, 'detail': detail}}


def verify_pages(snapshot: dict) -> bool:
    if not snapshot.get('complete') or not snapshot.get('cursorExhausted'):
        return False
    if any(snapshot.get(key) for key in ('conflicts', 'detailErrors', 'unreadIds')):
        return False
    pages = snapshot.get('pages')
    if not isinstance(pages, list) or not pages or pages[-1].get('cursor') not in (None, ''):
        return False
    items, cursors = [], set()
    for page in pages:
        if (not isinstance(page, dict) or not {'items', 'pendingItems', 'cursor'} <= page.keys()
                or not isinstance(page.get('items'), list)
                or not isinstance(page.get('pendingItems'), (list, type(None)))):
            return False
        cursor = page.get('cursor')
        if cursor and (not isinstance(cursor, str) or cursor in cursors):
            return False
        if cursor:
            cursors.add(cursor)
        items.extend(page['items'] + (page['pendingItems'] or []))
    # Повторы и выходы за период хранятся сборщиком отдельно. Нельзя принять их за потерянные строки.
    return (not snapshot.get('excluded') and not snapshot.get('repeatedObservations')
            and items == snapshot['records'])


def read_history(snapshot: dict) -> dict:
    if (not isinstance(snapshot, dict) or snapshot.get('adapter') != 'yandex-bank-graphql'
            or snapshot.get('schema_version') != 1 or not isinstance(snapshot.get('records'), list)
            or not isinstance(snapshot.get('details'), list)):
        raise ValueError('Ожидается JSON читающего сборщика Яндекс Банка')
    details = defaultdict(list)
    for row in snapshot['details']:
        if not isinstance(row, dict) or not isinstance(row.get('requestedId'), str):
            raise ValueError('Карточка API должна быть объектом')
        details[row.get('requestedId')].append(row)
    if any(not isinstance(feed, dict) for feed in snapshot['records']):
        raise ValueError('Элемент списка API должен быть объектом')
    raw = [{'feed': feed, 'details': details.get(feed.get('id'), [])}
           for feed in snapshot['records']]
    result = report('yandex-bank-api', raw, parse_record)
    result['bank_account_identity_names'] = resolve_accounts(result['records'])
    feed_ids = [row.get('id') for row in snapshot['records']]
    counts = Counter(feed_ids)
    result['unmatched_details'] = [row for row in snapshot['details'] if row.get('requestedId') not in counts]
    for row in result['records']:
        if counts[row['source_id']] > 1:
            row['issues'].append('duplicate_source_id')
        result['issues'].extend({'locator': row['locator'], 'code': code} for code in row['issues'])
    if result['unmatched_details']:
        result['issues'].append({'code': 'unmatched_details'})
    is_complete = verify_pages(snapshot)
    if not is_complete:
        result['issues'].append({'code': 'collection_coverage_unverified'})
    result['coverage'] = 'snapshot_complete' if is_complete else 'unverified'
    result['ready'] = not result['rejected'] and not result['issues']
    result['period'] = snapshot.get('period')
    result['limitations'] = ['snapshot_is_not_live_balance_reconciliation',
                             'native_id_namespace_not_used_for_deduplication',
                             'categories_and_tags_not_assigned', 'no_writes']
    return result
