"""Разбор снимков открытых карточек. В этом адаптере нет сетевых запросов."""

from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from .common import amount_decimal, normalize_space, parse_date, record_base, report

CARD_PATH = '/my/transactions-history/operation'
STATUSES = {'выполнено': ('completed', True), 'в обработке': ('pending', False),
            'отмена': ('canceled', None), 'не выполнено': ('canceled', None),
            'не выполнено / отмена': ('canceled', None)}
SERVICE_ROWS = {'чат с поддержкой', 'получить справку', 'получить квитанцию'}
CASHBACK_SOURCES = {'по карте пэй', 'от сервиса', 'яндекс плюс'}
DETAIL_LABELS = {'способ оплаты', 'статус', 'категория', 'mcc', 'назначение',
                 'округление', 'куда', 'перевод', 'автопополнение', 'пополнение',
                 'получатель', 'банк получателя', 'банк отправителя', 'счёт'}


def operation_id(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'bank.yandex.ru'
            or parsed.path != CARD_PATH or parsed.fragment):
        raise ValueError('Разрешена только карточка операции в истории Яндекс Банка')
    ids = parse_qs(parsed.query, keep_blank_values=True).get('id', [])
    if len(ids) != 1 or not ids[0].strip():
        raise ValueError('Карточка не содержит единственного идентификатора операции')
    return ids[0]


def read_cards(cards: list) -> dict:
    if not isinstance(cards, list):
        raise ValueError('Ожидается массив снимков карточек')
    result = report('yandex-bank-cards', cards, parse_card)
    seen = set()
    for row in result['records']:
        key = (row['source'], row['source_id'])
        if key in seen:
            result['issues'].append({'locator': row['locator'], 'code': 'duplicate_source_id'})
        seen.add(key)
        for code in row['issues']:
            result['issues'].append({'locator': row['locator'], 'code': code})
    result['ready'] = not result['rejected'] and not result['issues']
    result['limitations'] = ['coverage_not_proven_by_card_list',
                             'browser_collection_requires_supervised_session',
                             'url_id_stability_requires_live_verification']
    return result


def validate_snapshot(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('Снимок карточки должен быть объектом')
    required = ('url', 'captured_at', 'timezone', 'title', 'amount', 'date')
    if any(not isinstance(raw.get(key), str) or not raw[key].strip() for key in required):
        raise ValueError('Не заполнены строковые поля снимка карточки')
    if type(raw.get('is_points')) is not bool:
        raise ValueError('Не определён признак баллов')
    if not isinstance(raw.get('details'), list):
        raise ValueError('Отсутствуют реквизиты карточки')
    details = {}
    for item in raw['details']:
        if (not isinstance(item, list) or len(item) != 2
                or any(not isinstance(value, str) or not value.strip() for value in item)):
            raise ValueError('Реквизит должен быть парой непустых строк')
        label, value = normalize_space(item[0]).lower(), normalize_space(item[1])
        if label in details:
            raise ValueError('Повторяющиеся подписи реквизитов требуют проверки')
        details[label] = value
    accounts = raw.get('transfer_accounts')
    if (not isinstance(accounts, list) or len(accounts) not in (0, 2)
            or any(not isinstance(account, str) or not account.strip() for account in accounts)):
        raise ValueError('Для внутреннего перевода нужны два счёта в порядке откуда → куда')
    return details


def parse_card(raw: dict, index: int) -> dict:
    if isinstance(raw, dict) and 'rows' in raw:
        raw = prepare_dom_snapshot(raw)
    details = validate_snapshot(raw)
    source_id = operation_id(raw['url'])
    captured = datetime.fromisoformat(raw['captured_at'])
    if captured.tzinfo is None:
        raise ValueError('У даты чтения отсутствует часовой пояс')
    timestamp, inferred = parse_date(raw['date'], raw['timezone'], captured)
    amount, currency = parse_amount(raw['amount'], raw['is_points'])
    status, cleared = STATUSES.get(details.get('статус', '').lower(), ('unknown', None))
    accounts = raw['transfer_accounts']
    account, other = account_for_leg(details, raw['is_points'], accounts, amount)
    issues = ([] if status != 'unknown' else ['unknown_status'])
    if not account:
        issues.append('unknown_account')
    if inferred:
        issues.append('inferred_date')
    return {**record_base('yabank', index + 1), 'source_id': source_id,
            'timestamp': timestamp, 'amount': str(amount), 'currency': currency,
            'account': account, 'other_account': other,
            'kind': 'transfer_leg' if accounts else None,
            'status': status, 'cleared': cleared, 'description': raw['title'],
            'bank_category': details.get('категория'), 'mcc': details.get('mcc'),
            'cashback_hint': cashback_hint(details), 'issues': issues, 'raw': dict(raw)}


def parse_amount(value: str, is_points: bool):
    value = normalize_space(value).replace('−', '-')
    if not value.startswith(('+', '-')):
        raise ValueError('Направление суммы не указано явно')
    if is_points:
        return amount_decimal(value), 'YPLUS_POINTS'
    if not value.endswith('₽'):
        raise ValueError('Распознавание валюты этой карточки не поддерживается')
    return amount_decimal(value[:-1].strip()), 'RUB'


def account_for_leg(details: dict, is_points: bool, accounts: list, amount) -> tuple:
    if accounts:
        return (accounts[0], accounts[1]) if amount < 0 else (accounts[1], accounts[0])
    if is_points:
        return 'ЯПлюс Баллы', None
    # Названия источника сохраняются без эвристик и без подстановки карты по умолчанию.
    values = {details[key] for key in ('способ оплаты', 'пополнение', 'счёт') if key in details}
    return (values.pop(), None) if len(values) == 1 else (None, None)


def cashback_hint(details: dict) -> str | None:
    if 'кешбэк' not in details:
        return None
    value = amount_decimal(details['кешбэк'])
    if value < 0:
        raise ValueError('Отрицательный кешбэк требует отдельного разбора')
    return str(value)


def prepare_dom_snapshot(raw: dict) -> dict:
    rows = raw['rows']
    if not isinstance(rows, list):
        raise ValueError('DOM-реквизиты должны быть массивом строк карточки')
    details, cashback_total, components = parse_dom_rows(rows)
    if components:
        summed = sum(components)
        if cashback_total is not None and cashback_total != summed:
            raise ValueError('Итог кешбэка не совпадает с его составляющими')
        cashback_total = summed
    if cashback_total is not None:
        details.append(['Кешбэк', str(cashback_total)])
    return {**raw, 'is_points': raw.get('pointsAmount'),
            'transfer_accounts': raw.get('transferTitles'), 'details': details}


def parse_dom_rows(rows: list) -> tuple:
    details, components, cashback_total = [], [], None
    in_cashback, has_cashback, cashback_sources = False, False, set()
    for raw_row in rows:
        if (not isinstance(raw_row, list) or not raw_row
                or any(not isinstance(value, str) or not value.strip() for value in raw_row)):
            raise ValueError('Строка DOM содержит пустой или некорректный реквизит')
        row = [normalize_space(value) for value in raw_row]
        label = row[0].lower()
        if label == 'кешбэк':
            if has_cashback or len(row) > 2:
                raise ValueError('Неоднозначный заголовок кешбэка')
            has_cashback, in_cashback = True, True
            cashback_total = amount_decimal(row[1]) if len(row) == 2 else None
        elif label in CASHBACK_SOURCES:
            if not in_cashback or len(row) != 2 or label in cashback_sources:
                raise ValueError('Неоднозначная составляющая кешбэка')
            cashback_sources.add(label)
            components.append(amount_decimal(row[1]))
        elif label in SERVICE_ROWS and len(row) == 1:
            in_cashback = False
        else:
            if len(row) < 2 or (in_cashback and label not in DETAIL_LABELS):
                raise ValueError('Не распознана строка DOM карточки')
            in_cashback = False
            details.append([row[0], ' '.join(row[1:])])
    if has_cashback and cashback_total is None and not components:
        raise ValueError('В карточке есть кешбэк, но сумма не прочитана')
    return details, cashback_total, components
