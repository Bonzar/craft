"""Чтение отдельной копии SQLite backup + WAL. Оригинал не открывается SQLite."""

import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone as utc_timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import amount_decimal, record_base, report
from .markers import extract_markers

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=utc_timezone.utc)
MICROSECONDS = 1_000_000
SQLITE_HEADER = b'SQLite format 3\x00'
TRANSACTION_TYPES = {0: 'expense', 1: 'income', 2: 'transfer_leg'}
CENT_CURRENCIES = {'RUB', 'EUR', 'USD', 'TRY', 'AMD'}
CENT = Decimal('0.01')
SQLITE_NOISE_LIMIT = Decimal('0.00000001')
REQUIRED = {
    'ZTRANSACTION': {'Z_PK', 'ZRECORDNAME', 'ZTYPE', 'ZACCOUNT', 'ZAMOUNT', 'ZDATE',
                     'ZNOTES', 'ZPENDING', 'ZRECONCILED', 'ZUPCOMING', 'ZDELETEAFTERDATE',
                     'ZREFERENCED_TRANSACTION', 'ZCATEGORY', 'ZTRANSACTION_DESCRIPTION',
                     'ZTRANSACTION_TAG', 'ZTRANSACTION_PAYEE'},
    'ZACCOUNT': {'Z_PK', 'ZNAME', 'ZRECORDNAME', 'ZCURRENCY', 'ZCURRENT_BALANCE',
                 'ZCURRENT_UNCLEARED_BALANCE', 'ZLAST_RECONCILED_BALANCE',
                 'ZHIDDEN', 'ZDELETED_LOCALLY'},
    'ZCURRENCY': {'Z_PK', 'ZCODE'},
    'ZTRANSACTION_CATEGORY': {'Z_PK', 'ZNAME', 'ZPARENT'},
    'ZTRANSACTION_DESCRIPTION': {'Z_PK', 'ZNAME'},
    'ZTRANSACTION_TAG': {'Z_PK', 'ZNAME'},
    'ZTRANSACTION_PAYEE': {'Z_PK', 'ZNAME'},
    'ZSPLIT': {'Z_PK', 'ZTRANSACTION', 'ZCATEGORY', 'ZAMOUNT'},
}
READ_TRANSACTIONS = '''
SELECT t.Z_PK AS pk,t.ZRECORDNAME AS id,t.ZTYPE AS type,t.ZACCOUNT AS account_pk,
 CAST(t.ZAMOUNT AS TEXT) AS amount,CAST(t.ZDATE AS TEXT) AS date,
 t.ZNOTES AS notes,t.ZPENDING AS pending,t.ZRECONCILED AS reconciled,
 t.ZUPCOMING AS upcoming,t.ZDELETEAFTERDATE AS deleted,t.ZCATEGORY AS category_pk,
 d.ZNAME AS description,tag.ZNAME AS tag,p.ZNAME AS payee,
 other.ZRECORDNAME AS linked_id,other.Z_PK AS linked_locator,other.ZACCOUNT AS other_account_pk
FROM ZTRANSACTION t
LEFT JOIN ZTRANSACTION_DESCRIPTION d ON d.Z_PK=t.ZTRANSACTION_DESCRIPTION
LEFT JOIN ZTRANSACTION_TAG tag ON tag.Z_PK=t.ZTRANSACTION_TAG
LEFT JOIN ZTRANSACTION_PAYEE p ON p.Z_PK=t.ZTRANSACTION_PAYEE
LEFT JOIN ZTRANSACTION other ON other.Z_PK=t.ZREFERENCED_TRANSACTION
ORDER BY t.ZDATE,t.Z_PK
'''


def fingerprint(path: Path) -> tuple | None:
    if not path.exists():
        return None
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


@contextmanager
def snapshot_connection(source: Path):
    with source.open('rb') as stream:
        if stream.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            raise ValueError('Ожидается SQLite-резервная копия Debit & Credit')
    originals = [source, Path(str(source) + '-wal')]
    before = [fingerprint(path) for path in originals]
    with tempfile.TemporaryDirectory(prefix='debit-credit-backup-') as directory:
        target = Path(directory) / 'snapshot.sqlite'
        for original, suffix in zip(originals, ('', '-wal')):
            if original.exists():
                shutil.copy2(original, Path(str(target) + suffix))
        if before != [fingerprint(path) for path in originals]:
            raise ValueError('Резервная копия изменилась во время чтения')
        connection = sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA query_only=ON')
            if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Проверка целостности резервной копии не пройдена')
            yield connection
        finally:
            connection.close()


def validate_schema(connection: sqlite3.Connection) -> None:
    for table, required in REQUIRED.items():
        columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}
        if not required <= columns:
            raise ValueError(f'Неподдерживаемая схема backup: {table}')


def read_accounts(connection: sqlite3.Connection) -> list[dict]:
    query = '''SELECT a.Z_PK AS pk,a.ZNAME AS name,a.ZRECORDNAME AS id,c.ZCODE AS currency,
      CAST(a.ZCURRENT_BALANCE AS TEXT) AS balance,
      CAST(a.ZCURRENT_UNCLEARED_BALANCE AS TEXT) AS uncleared_balance,
      CAST(a.ZLAST_RECONCILED_BALANCE AS TEXT) AS last_reconciled_balance,
      a.ZHIDDEN AS hidden,a.ZDELETED_LOCALLY AS deleted_locally
      FROM ZACCOUNT a LEFT JOIN ZCURRENCY c ON c.Z_PK=a.ZCURRENCY ORDER BY a.Z_PK'''
    accounts = [dict(row) for row in connection.execute(query)]
    for account in accounts:
        if not account['name'] or not account['currency']:
            raise ValueError('У счёта отсутствует имя или валюта')
        if account['id'] is not None and (not isinstance(account['id'], str) or not account['id']):
            raise ValueError('Некорректный iCloud-ID счёта')
    return accounts


def category_path(category_pk: int | None, categories: dict) -> list[str]:
    names, visited = [], set()
    while category_pk is not None:
        if category_pk in visited or category_pk not in categories:
            raise ValueError('Неоднозначная или отсутствующая категория')
        visited.add(category_pk)
        row = categories[category_pk]
        names.append(row['ZNAME'])
        category_pk = row['ZPARENT']
    return list(reversed(names))


def read_splits(connection: sqlite3.Connection, categories: dict) -> dict:
    splits = {}
    query = 'SELECT ZTRANSACTION,ZCATEGORY,CAST(ZAMOUNT AS TEXT) AS amount FROM ZSPLIT ORDER BY Z_PK'
    for row in connection.execute(query):
        splits.setdefault(row['ZTRANSACTION'], []).append({
            'amount': str(amount_decimal(row['amount'])),
            'category_path': category_path(row['ZCATEGORY'], categories),
        })
    return splits


def apple_date(value: str, zone: ZoneInfo) -> datetime:
    try:
        micros = int(Decimal(value) * MICROSECONDS)
        return (APPLE_EPOCH + timedelta(microseconds=micros)).astimezone(zone)
    except (TypeError, ArithmeticError, OverflowError) as error:
        raise ValueError('Некорректная дата backup') from error


def normalize_money(value: str, currency: str) -> tuple[Decimal, list[str]]:
    amount = amount_decimal(value)
    if currency not in CENT_CURRENCIES:
        return amount, ['currency_precision_unknown']
    rounded = amount.quantize(CENT)
    if abs(amount - rounded) > SQLITE_NOISE_LIMIT or (rounded == 0 and amount != 0):
        return amount, ['amount_precision_requires_review']
    return rounded, []


def normalize_splits(values: list, currency: str) -> tuple[list, list]:
    result, issues = [], []
    for value in values:
        amount, amount_issues = normalize_money(value['amount'], currency)
        result.append({**value, 'amount': format(amount.normalize(), 'f'), 'raw_amount': value['amount']})
        issues.extend(amount_issues)
    return result, issues


def parse_transaction(raw: dict, accounts: dict, categories: dict, splits: dict, zone: ZoneInfo) -> dict:
    if raw['id'] is not None and (not isinstance(raw['id'], str) or not raw['id']):
        raise ValueError('Некорректный iCloud-ID операции')
    if raw['type'] not in TRANSACTION_TYPES or raw['account_pk'] not in accounts:
        raise ValueError('Неизвестен тип или счёт операции')
    account = accounts[raw['account_pk']]
    other = accounts.get(raw['other_account_pk'])
    if raw['type'] == 2 and (not other or raw['linked_locator'] is None):
        raise ValueError('У перевода отсутствует связанная сторона')
    amount, issues = normalize_money(raw['amount'], account['currency'])
    if (raw['type'] == 0 and amount >= 0) or (raw['type'] == 1 and amount <= 0):
        raise ValueError('Направление суммы противоречит типу операции')
    timestamp = apple_date(raw['date'], zone)
    path = category_path(raw['category_pk'], categories)
    pending = raw['pending']
    cleared = {0: True, 1: False}.get(pending)
    if cleared is None:
        issues.append('unknown_status')
    upcoming = raw['upcoming'] == 1
    split_values, split_issues = normalize_splits(splits.get(raw['pk'], []), account['currency'])
    issues.extend(split_issues)
    return {**record_base('debit-credit', raw['pk']), 'source_id': raw['id'],
            'timestamp': timestamp.isoformat(),
            'timestamp_precision': 'microsecond' if timestamp.microsecond else 'second',
            'amount': format(amount.normalize(), 'f'), 'currency': account['currency'], 'account': account['name'],
            'account_id': account['id'], 'other_account': other['name'] if other else None,
            'linked_id': raw['linked_id'], 'linked_locator': raw['linked_locator'],
            'kind': TRANSACTION_TYPES[raw['type']],
            'status': {True: 'completed', False: 'pending'}.get(cleared, 'unknown'),
            'cleared': cleared, 'reconciled': raw['reconciled'], 'upcoming': upcoming,
            'eligible_for_matching': not upcoming and raw['deleted'] is None and not issues,
            'description': raw['description'] or '', 'notes': raw['notes'] or '',
            'category': path[-1] if path else None, 'category_path': path,
            'splits': split_values, 'tag': raw['tag'], 'payee': raw['payee'] or '', 'issues': issues,
            'markers': [m.as_dict() for m in extract_markers(raw['notes'] or '')], 'raw': raw}


def read_backup(path: Path | str, timezone: str, from_date: str | None = None,
                to_date: str | None = None) -> dict:
    zone = ZoneInfo(timezone)
    start = date.fromisoformat(from_date) if from_date else None
    end = date.fromisoformat(to_date) if to_date else None
    if start and end and start > end:
        raise ValueError('Начало периода позже конца')
    try:
        with snapshot_connection(Path(path)) as connection:
            validate_schema(connection)
            account_list = read_accounts(connection)
            accounts = {row['pk']: row for row in account_list}
            categories = {row['Z_PK']: dict(row) for row in connection.execute('SELECT Z_PK,ZNAME,ZPARENT FROM ZTRANSACTION_CATEGORY')}
            splits = read_splits(connection, categories)
            rows = [dict(row) for row in connection.execute(READ_TRANSACTIONS)]
    except sqlite3.Error as error:
        raise ValueError('Резервная копия SQLite не прочитана') from error
    if start or end:
        rows = [row for row in rows if (not start or apple_date(row['date'], zone).date() >= start)
                and (not end or apple_date(row['date'], zone).date() <= end)]
    result = report('debit-credit-backup', rows,
                    lambda row, _: parse_transaction(row, accounts, categories, splits, zone))
    ids = [row['source_id'] for row in result['records'] if row['source_id'] is not None]
    if len(ids) != len(set(ids)):
        result['issues'].append({'code': 'duplicate_source_id'})
    for row in result['records']:
        result['issues'].extend({'locator': row['locator'], 'code': code} for code in row['issues'])
    result['ready'] = not result['rejected'] and not result['issues']
    result['accounts'] = account_list
    result['cloud_identity'] = {
        'transactions_without_id': sum(row['source_id'] is None for row in result['records']),
        'accounts_without_id': sum(row['id'] is None for row in account_list),
        'local_locators_are_snapshot_only': True,
    }
    result['coverage'] = 'backup_snapshot'
    result['date_filter'] = {'from_date': from_date, 'to_date': to_date} if start or end else None
    result['limitations'] = ['backup_freshness_requires_verification', 'schema_specific_read_only_adapter']
    return result
