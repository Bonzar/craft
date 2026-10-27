"""Общие денежные значения, даты и отчёт без потерь входных строк."""

import re
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

MONTHS = {name: index for index, name in enumerate((
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'), 1)}
AMOUNT_PATTERN = re.compile(r'[+-]? ?(?:\d+|\d{1,3}(?: \d{3})+)(?:[.,]\d+)?')
RUSSIAN_DATE = re.compile(
    r'(\d{1,2}) ([а-яё]+)(?: (\d{4}))?(?: г\.?)?(?: в|,)? (\d{1,2}):(\d{2})')


def normalize_space(value: str) -> str:
    return ' '.join(value.split())


def amount_decimal(value: str) -> Decimal:
    value = normalize_space(value).replace('−', '-')
    if not AMOUNT_PATTERN.fullmatch(value):
        raise ValueError('Некорректный формат суммы')
    return Decimal(value.replace(' ', '').replace(',', '.'))


def parse_date(value: str, timezone: str, reference: datetime | None = None) -> tuple[str, bool]:
    value = normalize_space(value)
    zone = ZoneInfo(timezone)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is not None:
        return localize(parsed, zone).isoformat(), False
    relative = re.fullmatch(r'(Сегодня|Вчера|Позавчера), (\d{1,2}):(\d{2})', value, re.I)
    if relative and reference is not None:
        offset = {'сегодня': 0, 'вчера': 1, 'позавчера': 2}[relative[1].lower()]
        day = reference.astimezone(zone) - timedelta(days=offset)
        parsed = day.replace(hour=int(relative[2]), minute=int(relative[3]), second=0, microsecond=0)
        return parsed.isoformat(), True
    match = RUSSIAN_DATE.fullmatch(value.lower())
    if not match or match[2] not in MONTHS:
        raise ValueError('Не распознана дата операции')
    if match[3] is None and reference is None:
        raise ValueError('Год отсутствует, дата чтения не указана')
    year = int(match[3]) if match[3] else reference.astimezone(zone).year
    parsed = datetime(year, MONTHS[match[2]], int(match[1]), int(match[4]), int(match[5]))
    if match[3] is None and localize(parsed, zone) > reference.astimezone(zone):
        parsed = parsed.replace(year=year - 1)
    return localize(parsed, zone).isoformat(), match[3] is None


def localize(value: datetime, zone: ZoneInfo) -> datetime:
    if value.tzinfo is not None:
        return value
    first, second = value.replace(tzinfo=zone, fold=0), value.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError('Местное время неоднозначно или отсутствует при смене часового пояса')
    return first


def report(adapter: str, raw_items: list, parser) -> dict:
    records, rejected = [], []
    for index, raw in enumerate(raw_items):
        try:
            records.append(parser(raw, index))
        except (ValueError, TypeError, KeyError) as error:
            rejected.append({'locator': index + 1, 'reason': str(error), 'raw': raw})
    if len(raw_items) != len(records) + len(rejected):
        raise RuntimeError('Нарушен счёт входных операций')
    return {'schema_version': 1, 'adapter': adapter, 'input_count': len(raw_items),
            'records': records, 'rejected': rejected, 'issues': [],
            'coverage': 'unverified', 'ready': not rejected}


def record_base(source: str, locator: int) -> dict:
    return {'source': source, 'source_id': None, 'locator': locator,
            'timestamp': None, 'timestamp_precision': 'minute',
            'amount': None, 'currency': None, 'account': None,
            'other_account': None, 'kind': None, 'status': 'unknown',
            'cleared': None, 'description': '', 'notes': '', 'category': None,
            'tag': None, 'markers': []}
