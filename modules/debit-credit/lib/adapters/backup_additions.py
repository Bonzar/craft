"""Новые операции с явными полями. Не определяет смысл или категорию за человека."""

import uuid
from collections import defaultdict
from decimal import Decimal

from .backup_metadata import allocate_key, date_values, description_key
from .common import amount_decimal
from .debit_credit_backup import normalize_money
from .markers import extract_markers

FIELDS = {'key', 'peer_key', 'kind', 'account', 'currency', 'amount', 'timestamp',
          'cleared', 'description', 'notes', 'category_path', 'tag', 'payee'}
KIND_CODES = {'expense': 0, 'income': 1, 'transfer_leg': 2}
TRANSACTION_COLUMNS = {'Z_PK', 'Z_ENT', 'Z_OPT', 'ZCHANGETRIGGER', 'ZEXTERNALCHANGEEDIT',
    'ZIMPORTED', 'ZORIGINATINGSOURCE', 'ZPENDING', 'ZPENDING_UPDATE', 'ZRECONCILED', 'ZTYPE',
    'ZUPCOMING', 'ZUPCOMINGCHANGETOKEN', 'ZACCOUNT', 'ZCATEGORY', 'ZRECURRING_TRANSACTION',
    'ZREFERENCED_TRANSACTION', 'ZTRANSACTION_DESCRIPTION', 'ZTRANSACTION_PAYEE', 'ZTRANSACTION_TAG',
    'ZDATE', 'ZDAY', 'ZDELETEAFTERDATE', 'ZEFFECTIVEDATE', 'ZPOSTEDDATE', 'ZAMOUNT', 'ZTAX',
    'ZEXTERNALCHANGEUSERID', 'ZNOTES', 'ZOWNERNAME', 'ZRECORDCHANGETAG', 'ZRECORDNAME', 'ZEXTERNALID'}
NEW_REQUIRED = {'Z_PK', 'Z_ENT', 'Z_OPT', 'ZCHANGETRIGGER', 'ZORIGINATINGSOURCE', 'ZPENDING',
                'ZPENDING_UPDATE', 'ZRECONCILED', 'ZTYPE', 'ZACCOUNT', 'ZDATE', 'ZDAY', 'ZAMOUNT',
                'ZTAX', 'ZNOTES', 'ZRECORDNAME', 'ZREFERENCED_TRANSACTION', 'ZCATEGORY',
                'ZTRANSACTION_DESCRIPTION', 'ZTRANSACTION_TAG', 'ZTRANSACTION_PAYEE'}


def marker_keys(notes: str) -> set:
    return {(m.source, m.id, m.role) for m in extract_markers(notes)}


def validate_additions(rows: list, existing: list) -> dict:
    if not isinstance(rows, list):
        raise ValueError('Ожидается список новых операций')
    found, markers = {}, set().union(*(marker_keys(r['notes']) for r in existing))
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError('Не заполнены явные поля новой операции')
        if not isinstance(row['key'], str) or not row['key'] or row['key'] in found:
            raise ValueError('Ключ новой операции должен быть уникальным')
        if not isinstance(row['kind'], str) or row['kind'] not in KIND_CODES or type(row['cleared']) is not bool:
            raise ValueError('Нужны тип и завершённость новой операции')
        for field in ('account', 'currency', 'timestamp', 'description'):
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError('Не заполнено поле новой операции')
        if not isinstance(row['notes'], str) or not isinstance(row['amount'], str):
            raise ValueError('Заметка и сумма должны быть строками')
        keys = marker_keys(row['notes'])
        if not keys or keys & markers:
            raise ValueError('Отсутствует метка источника или метка уже записана')
        markers.update(keys)
        amount = amount_decimal(row['amount'])
        exact, issues = normalize_money(row['amount'], row['currency'])
        if issues or amount != exact or amount == 0:
            raise ValueError('Нужна точная ненулевая сумма в поддержанной валюте')
        if (row['kind'] == 'expense' and amount > 0) or (row['kind'] == 'income' and amount < 0):
            raise ValueError('Знак суммы не соответствует типу операции')
        if (not isinstance(row['category_path'], list)
                or any(not isinstance(s, str) or not s for s in row['category_path'])):
            raise ValueError('Некорректный путь категории')
        for field in ('tag', 'payee'):
            if row[field] is not None and (not isinstance(row[field], str) or not row[field]):
                raise ValueError('Тег и контрагент должны быть строками или null')
        found[row['key']] = row
    validate_peers(found)
    return found


def validate_peers(rows: dict) -> None:
    for row in rows.values():
        key = row['peer_key']
        if row['kind'] != 'transfer_leg':
            if key is not None:
                raise ValueError('Обычная операция не должна иметь сторону перевода')
            continue
        if not isinstance(key, str) or key not in rows or key == row['key']:
            raise ValueError('Не найдена вторая сторона перевода')
        peer = rows[key]
        if (peer['kind'] != 'transfer_leg' or peer['peer_key'] != row['key']
                or (peer['account'], peer['currency']) == (row['account'], row['currency'])
                or amount_decimal(peer['amount']) * amount_decimal(row['amount']) >= 0):
            raise ValueError('Некорректная пара сторон перевода')
        if row['currency'] == peer['currency'] and amount_decimal(row['amount']) + amount_decimal(peer['amount']):
            raise ValueError('Суммы перевода в одной валюте не сходятся')


def existing_key(connection, table: str, name: str | None) -> int | None:
    if name is None:
        return None
    if table not in {'ZTRANSACTION_TAG', 'ZTRANSACTION_PAYEE'}:
        raise ValueError('Неподдерживаемый справочник')
    matches = connection.execute('SELECT Z_PK FROM ' + table + ' WHERE ZNAME=?', (name,)).fetchall()
    if len(matches) != 1:
        raise ValueError('Значение справочника отсутствует или неоднозначно')
    return matches[0][0]


def category_key(connection, path: list) -> int | None:
    parent = None
    for name in path:
        matches = connection.execute('SELECT Z_PK FROM ZTRANSACTION_CATEGORY WHERE ZNAME=? AND ZPARENT IS ?',
                                     (name, parent)).fetchall()
        if len(matches) != 1:
            raise ValueError('Категория отсутствует или неоднозначна')
        parent = matches[0][0]
    return parent


def account_key(connection, row: dict) -> int:
    matches = connection.execute('''SELECT a.Z_PK FROM ZACCOUNT a JOIN ZCURRENCY c ON c.Z_PK=a.ZCURRENCY
      WHERE a.ZNAME=? AND c.ZCODE=? AND (a.ZDELETED_LOCALLY IS NULL OR a.ZDELETED_LOCALLY=0)''',
                                 (row['account'], row['currency'])).fetchall()
    if len(matches) != 1:
        raise ValueError('Счёт отсутствует или неоднозначен')
    return matches[0][0]


def new_values(connection, row: dict, allocated: dict, columns: list, zone, descriptions: dict) -> dict:
    key, entity = allocated[row['key']]
    stamp, day = date_values(row['timestamp'], zone)
    values = dict.fromkeys(columns)
    values.update(Z_PK=key, Z_ENT=entity, Z_OPT=1, ZCHANGETRIGGER=0, ZORIGINATINGSOURCE=0,
                  ZPENDING=int(not row['cleared']), ZPENDING_UPDATE=0, ZRECONCILED=0,
                  ZTYPE=KIND_CODES[row['kind']], ZACCOUNT=account_key(connection, row),
                  ZDATE=stamp, ZDAY=day, ZAMOUNT=float(amount_decimal(row['amount'])), ZTAX=0,
                  ZNOTES=row['notes'], ZRECORDNAME=str(uuid.uuid4()).upper(),
                  ZREFERENCED_TRANSACTION=allocated[row['peer_key']][0] if row['peer_key'] else None,
                  ZCATEGORY=category_key(connection, row['category_path']),
                  ZTRANSACTION_DESCRIPTION=description_key(connection, row['description'], KIND_CODES[row['kind']], descriptions),
                  ZTRANSACTION_TAG=existing_key(connection, 'ZTRANSACTION_TAG', row['tag']),
                  ZTRANSACTION_PAYEE=existing_key(connection, 'ZTRANSACTION_PAYEE', row['payee']))
    return values


def update_balances(connection, deltas: dict) -> dict:
    changed = {}
    for key, delta in deltas.items():
        row = dict(connection.execute('SELECT * FROM ZACCOUNT WHERE Z_PK=?', (key,)).fetchone())
        values = {'ZCURRENT_BALANCE': float(amount_decimal(str(row['ZCURRENT_BALANCE'])) + delta['cleared']),
                  'ZCURRENT_UNCLEARED_BALANCE': float(amount_decimal(str(row['ZCURRENT_UNCLEARED_BALANCE'])) + delta['all']),
                  'Z_OPT': row['Z_OPT'] + 1}
        connection.execute('UPDATE ZACCOUNT SET ZCURRENT_BALANCE=?,ZCURRENT_UNCLEARED_BALANCE=?,Z_OPT=? WHERE Z_PK=?',
                           (*values.values(), key))
        changed[key] = {**row, **values}
    return changed


def apply_additions(connection, rows: list, zone, descriptions: dict) -> tuple:
    columns = [r[1] for r in connection.execute('PRAGMA table_info(ZTRANSACTION)')]
    if rows and (not NEW_REQUIRED <= set(columns) or not set(columns) <= TRANSACTION_COLUMNS):
        raise ValueError('Неподдерживаемая схема новых операций')
    allocated = {r['key']: allocate_key(connection, 'Transaction') for r in rows}
    created, deltas = {}, defaultdict(lambda: {'cleared': Decimal(0), 'all': Decimal(0)})
    for row in rows:
        values = new_values(connection, row, allocated, columns, zone, descriptions)
        names = ','.join(values)
        connection.execute('INSERT INTO ZTRANSACTION (' + names + ') VALUES (' + ','.join('?' for _ in values) + ')',
                           tuple(values.values()))
        created[values['Z_PK']] = values
        deltas[values['ZACCOUNT']]['all'] += amount_decimal(row['amount'])
        if row['cleared']:
            deltas[values['ZACCOUNT']]['cleared'] += amount_decimal(row['amount'])
    return created, update_balances(connection, deltas), {r['key']: allocated[r['key']][0] for r in rows}
