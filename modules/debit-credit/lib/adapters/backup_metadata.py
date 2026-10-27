"""Значения и справочник описаний для правок и новых операций копии."""

import sqlite3
import uuid
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .debit_credit_backup import APPLE_EPOCH

DESCRIPTION_COLUMNS = {'Z_PK', 'Z_ENT', 'Z_OPT', 'ZPENDING_UPDATE', 'ZTYPE',
                       'ZUSAGERATING', 'ZNAME', 'ZRECORDNAME'}
ENTITY_TABLES = {'Transaction': 'ZTRANSACTION', 'Transaction_description': 'ZTRANSACTION_DESCRIPTION'}


def allocate_key(connection: sqlite3.Connection, name: str) -> tuple:
    table = ENTITY_TABLES[name]
    entities = connection.execute('SELECT * FROM Z_PRIMARYKEY WHERE Z_NAME=?', (name,)).fetchall()
    if len(entities) != 1:
        raise ValueError('Неоднозначный или отсутствующий счётчик Core Data')
    entity = entities[0]
    maximum = connection.execute('SELECT MAX(Z_PK) FROM ' + table).fetchone()[0] or 0
    key = max(entity['Z_MAX'], maximum) + 1
    connection.execute('UPDATE Z_PRIMARYKEY SET Z_MAX=? WHERE Z_ENT=?', (key, entity['Z_ENT']))
    return key, entity['Z_ENT']


def description_key(connection: sqlite3.Connection, name: str, kind: int, created: dict) -> int | None:
    if not name:
        return None
    columns = {r[1] for r in connection.execute('PRAGMA table_info(ZTRANSACTION_DESCRIPTION)')}
    if not DESCRIPTION_COLUMNS <= columns:
        raise ValueError('Неподдерживаемая схема описаний')
    matches = connection.execute('SELECT Z_PK FROM ZTRANSACTION_DESCRIPTION WHERE ZNAME=? AND ZTYPE=?',
                                 (name, kind)).fetchall()
    if len(matches) > 1:
        raise ValueError('Неоднозначное описание')
    if matches:
        return matches[0][0]
    key, entity = allocate_key(connection, 'Transaction_description')
    connection.execute('''INSERT INTO ZTRANSACTION_DESCRIPTION
      (Z_PK,Z_ENT,Z_OPT,ZPENDING_UPDATE,ZTYPE,ZUSAGERATING,ZNAME,ZRECORDNAME)
      VALUES (?,?,1,0,?,0,?,?)''', (key, entity, kind, name, str(uuid.uuid4()).upper()))
    created[key] = dict(connection.execute('SELECT * FROM ZTRANSACTION_DESCRIPTION WHERE Z_PK=?', (key,)).fetchone())
    return key


def date_values(value: str, zone: ZoneInfo) -> tuple:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('Для правки даты нужен часовой пояс')
    day = datetime.combine(stamp.astimezone(zone).date(), time(), zone)
    return (stamp - APPLE_EPOCH).total_seconds(), (day - APPLE_EPOCH).total_seconds()


