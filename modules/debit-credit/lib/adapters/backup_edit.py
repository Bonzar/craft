"""Правка явного списка полей в отдельной копии. Исходник и приложение не меняются."""

import hashlib
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .debit_credit_backup import read_backup, snapshot_connection
from .backup_metadata import date_values, description_key
from .backup_additions import apply_additions, validate_additions
from .common import amount_decimal
from .debit_credit_backup import normalize_money

EDITABLE_FIELDS = {'timestamp', 'description', 'notes'}
EDIT_TABLES = {'ZTRANSACTION', 'ZTRANSACTION_DESCRIPTION', 'Z_PRIMARYKEY', 'ZACCOUNT'}

def validate_target(source: Path, target: Path) -> None:
    related = [Path(str(target) + suffix) for suffix in ('', '-wal', '-shm', '-journal')]
    if any(path.exists() or path.is_symlink() for path in related) or target.resolve() == source.resolve():
        raise FileExistsError('Выход должен быть новым отдельным файлом без SQLite-файлов рядом')


def backup_fingerprint(path: Path | str) -> dict:
    source = Path(path)
    wal = Path(str(source) + '-wal')
    return {'database': hashlib.sha256(source.read_bytes()).hexdigest(),
            'wal': hashlib.sha256(wal.read_bytes()).hexdigest() if wal.exists() else None}


def validate_plan(plan: dict, current: dict) -> list:
    if (not isinstance(plan, dict) or not {'schema_version', 'source_sha256', 'updates'} <= set(plan)
            or set(plan) - {'schema_version', 'source_sha256', 'updates', 'additions'}
            or plan['schema_version'] != 1 or plan['source_sha256'] != current):
        raise ValueError('План не соответствует исходной резервной копии')
    updates = plan['updates']
    if not isinstance(updates, list) or (not updates and not plan.get('additions')):
        raise ValueError('Нужен непустой список правок или новых операций')
    seen = set()
    for row in updates:
        if not isinstance(row, dict) or set(row) != {'locator', 'set'}:
            raise ValueError('Некорректная правка')
        key, changes = row['locator'], row['set']
        if type(key) is not int or key <= 0 or key in seen:
            raise ValueError('Каждая правка должна иметь отдельный локальный указатель')
        if (not isinstance(changes, dict) or not changes or not changes.keys() <= EDITABLE_FIELDS
                or any(not isinstance(value, str) for value in changes.values())):
            raise ValueError('Разрешены только явные строковые дата, описание и заметка')
        seen.add(key)
    return updates


def table_state(connection: sqlite3.Connection) -> dict:
    names = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {name: Counter(tuple(row) for row in connection.execute(
                'SELECT * FROM "' + name.replace('"', '""') + '"')) for name in names}


def indexed_table(connection: sqlite3.Connection, table: str, state: dict) -> dict:
    columns = [r[1] for r in connection.execute('PRAGMA table_info(' + table + ')')]
    return {row[0]: dict(zip(columns, row)) for row in state[table]}


def apply_updates(connection: sqlite3.Connection, updates: list, zone: ZoneInfo) -> tuple:
    allowed, created = {}, {}
    for item in updates:
        key, changes = item['locator'], item['set']
        row = connection.execute('SELECT * FROM ZTRANSACTION WHERE Z_PK=?', (key,)).fetchone()
        if row is None:
            raise ValueError('Операция для правки не найдена')
        values = {'Z_OPT': row['Z_OPT'] + 1}
        if 'timestamp' in changes:
            values['ZDATE'], values['ZDAY'] = date_values(changes['timestamp'], zone)
        if 'notes' in changes:
            values['ZNOTES'] = changes['notes']
        if 'description' in changes:
            values['ZTRANSACTION_DESCRIPTION'] = description_key(connection, changes['description'], row['ZTYPE'], created)
        columns = ','.join(name + '=?' for name in values)
        connection.execute('UPDATE ZTRANSACTION SET ' + columns + ' WHERE Z_PK=?', (*values.values(), key))
        allowed[key] = values
    return allowed, created


def verify_sql_changes(connection: sqlite3.Connection, before: dict, allowed: dict, created: dict,
                       transactions: dict, accounts: dict) -> dict:
    after = table_state(connection)
    if before.keys() != after.keys():
        raise ValueError('Изменился состав таблиц')
    for table in before.keys() - EDIT_TABLES:
        if before[table] != after[table]:
            raise ValueError('Изменена несогласованная таблица')
    old, new = [indexed_table(connection, 'ZTRANSACTION', data) for data in (before, after)]
    if old.keys() | transactions.keys() != new.keys() or old.keys() & transactions.keys():
        raise ValueError('Изменился состав операций')
    for key, row in old.items():
        wanted = {**row, **allowed.get(key, {})}
        if new[key] != wanted:
            raise ValueError('Изменено несогласованное поле операции')
    if any(new[key] != row for key, row in transactions.items()):
        raise ValueError('Новая операция не соответствует плану SQL')
    old, new = [indexed_table(connection, 'ZTRANSACTION_DESCRIPTION', data) for data in (before, after)]
    if new != {**old, **created}:
        raise ValueError('Несогласованная правка справочника описаний')
    old, new = [indexed_table(connection, 'Z_PRIMARYKEY', data) for data in (before, after)]
    wanted = {key: dict(row) for key, row in old.items()}
    for row in wanted.values():
        if row['Z_NAME'] == 'Transaction_description' and created:
            row['Z_MAX'] = max(row['Z_MAX'], max(created))
        if row['Z_NAME'] == 'Transaction' and transactions:
            row['Z_MAX'] = max(row['Z_MAX'], max(transactions))
    if new != wanted:
        raise ValueError('Несогласованная правка счётчиков Core Data')
    old, new = [indexed_table(connection, 'ZACCOUNT', data) for data in (before, after)]
    if new != {**old, **accounts}:
        raise ValueError('Несогласованная правка счетов')
    return {table: {'removed': sum((before[table] - after[table]).values()),
                    'added': sum((after[table] - before[table]).values())}
            for table in before if before[table] != after[table]}


def check_result(before: dict, after: dict, updates: list, additions: list, key_map: dict) -> None:
    if not before['ready'] or not after['ready']:
        raise ValueError('Снимки содержат ошибки')
    old = {r['locator']: r for r in before['records']}
    new = {r['locator']: r for r in after['records']}
    changes = {r['locator']: r['set'] for r in updates}
    if old.keys() | set(key_map.values()) != new.keys():
        raise ValueError('Изменилось количество операций')
    for key, row in old.items():
        if key not in changes and row != new[key]:
            raise ValueError('Изменилась операция вне плана')
    for key, values in changes.items():
        for field, wanted in values.items():
            actual = new[key][field]
            if field == 'timestamp':
                if abs((datetime.fromisoformat(wanted) - datetime.fromisoformat(actual)).total_seconds()) > 0.000002:
                    raise ValueError('Дата записана с недопустимой погрешностью')
            elif actual != wanted:
                raise ValueError('Поле не соответствует плану')
    check_additions(after, additions, key_map)
    check_balances(before, after, additions)


def check_additions(after: dict, rows: list, key_map: dict) -> None:
    index = {r['locator']: r for r in after['records']}
    planned = {r['key']: r for r in rows}
    for wanted in rows:
        actual = index[key_map[wanted['key']]]
        for field in ('kind', 'account', 'currency', 'cleared', 'description', 'notes', 'category_path', 'tag'):
            if actual[field] != wanted[field]:
                raise ValueError('Новая операция не соответствует плану')
        if actual['payee'] != (wanted['payee'] or '') or actual['splits']:
            raise ValueError('Неверные поля новой операции')
        if amount_decimal(actual['amount']) != amount_decimal(wanted['amount']):
            raise ValueError('Неверная сумма новой операции')
        if abs((datetime.fromisoformat(actual['timestamp']) - datetime.fromisoformat(wanted['timestamp'])).total_seconds()) > 0.000002:
            raise ValueError('Неверное время новой операции')
        peer = wanted['peer_key']
        if actual['linked_locator'] != (key_map[peer] if peer else None):
            raise ValueError('Неверная связь сторон перевода')
        if actual['other_account'] != (planned[peer]['account'] if peer else None):
            raise ValueError('Неверный счёт второй стороны перевода')


def check_balances(before: dict, after: dict, additions: list) -> None:
    last = {a['pk']: a for a in after['accounts']}
    if {a['pk'] for a in before['accounts']} != last.keys():
        raise ValueError('Изменился состав счетов')
    for old in before['accounts']:
        actual = last[old['pk']]
        if {k: v for k, v in old.items() if k not in {'balance', 'uncleared_balance'}} != {
                k: v for k, v in actual.items() if k not in {'balance', 'uncleared_balance'}}:
            raise ValueError('Изменены несогласованные поля счёта')
        rows = [r for r in additions if (r['account'], r['currency']) == (old['name'], old['currency'])]
        for field in ('balance', 'uncleared_balance'):
            first, first_issues = normalize_money(old[field], old['currency'])
            end, end_issues = normalize_money(actual[field], old['currency'])
            delta = sum((amount_decimal(r['amount']) for r in rows if field == 'uncleared_balance' or r['cleared']), amount_decimal('0'))
            if first_issues or end_issues or end != first + delta:
                raise ValueError('Остаток счёта не соответствует новым операциям')


def prepare_backup(source: Path | str, target: Path | str, plan: dict, timezone: str) -> dict:
    source, target = Path(source), Path(target)
    validate_target(source, target)
    fingerprint = backup_fingerprint(source)
    updates = validate_plan(plan, fingerprint)
    zone = ZoneInfo(timezone)
    before_normalized = read_backup(source, timezone)
    additions = plan.get('additions', [])
    validate_additions(additions, before_normalized['records'])
    with tempfile.TemporaryDirectory(prefix='.dc-prepare-', dir=target.parent) as directory:
        stage = Path(directory) / 'candidate.dcbackup'
        descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        connection = sqlite3.connect(stage)
        connection.row_factory = sqlite3.Row
        try:
            with snapshot_connection(source) as original:
                original.backup(connection)
            connection.execute('PRAGMA journal_mode=DELETE')
            before = table_state(connection)
            allowed, created = apply_updates(connection, updates, zone)
            transactions, accounts, key_map = apply_additions(connection, additions, zone, created)
            connection.commit()
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Нарушена целостность SQLite')
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Нарушены внешние ключи SQLite')
            table_diff = verify_sql_changes(connection, before, allowed, created, transactions, accounts)
        except sqlite3.Error as error:
            raise ValueError('Изменённая копия не прошла обработку SQLite') from error
        finally:
            connection.close()
        after = read_backup(stage, timezone)
        check_result(before_normalized, after, updates, additions, key_map)
        if fingerprint != backup_fingerprint(source):
            raise ValueError('Исходная копия изменилась во время подготовки')
        validate_target(source, target)
        os.link(stage, target)
    return {'schema_version': 1, 'adapter': 'backup-preparation', 'input_count': len(updates) + len(additions),
            'records': [*updates, *additions], 'rejected': [], 'issues': [], 'ready': True,
            'coverage': 'explicit_edits_and_additions', 'source_sha256': fingerprint, 'new_record_locators': key_map,
            'candidate_sha256': backup_fingerprint(target), 'table_diff': table_diff,
            'source_records': before_normalized['input_count'], 'candidate_records': after['input_count'],
            'source_files_unchanged': True, 'app_restore_verified': False,
            'limitations': ['existing_edits_timestamp_description_notes_only', 'no_new_accounts_or_split_categories',
                            'does_not_register_or_restore_backup', 'restore_requires_app_verification']}
