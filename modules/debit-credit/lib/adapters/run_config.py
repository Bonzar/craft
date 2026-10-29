"""Настройки переноса из коллекций Craft: счета, контрольные точки, правила категорий, решения."""

import json
import re
import urllib.request
from datetime import date
from decimal import Decimal

ACCOUNTS = 'Счета источников'
CONTROL_POINTS = 'Контрольные точки'
RULES = 'Правила категорий и тегов'
DECISIONS = 'Решения по операциям'
COLLECTION_NAMES = (ACCOUNTS, CONTROL_POINTS, RULES, DECISIONS)
SOURCE_KEYS = {'Яндекс Банк': 'yabank', 'Т-Банк': 'tbank', 'Сбер': 'sber'}
OTHER_SOURCES = {'Билайн', 'ВТБ', 'Бейдж', 'ВкусВилл', 'Apple ID', 'Нет источника'}
MARKS = {'не записывать': 'skip', 'уже в приложении': 'same'}
UNIT = re.compile(r'[A-Z][A-Z_]*')
BROWSER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)'


def text(value) -> str:
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError('Ожидается текст в коллекции')
    return value.strip()


def category_path(value) -> list:
    return [part.strip() for part in text(value).split('/') if part.strip()]


def read_accounts(items: list) -> tuple[dict, set]:
    accounts, skipped = {}, set()
    for item in items:
        props = item.get('properties', {})
        source = props.get('source')
        if source not in SOURCE_KEYS and source not in OTHER_SOURCES:
            raise ValueError(f'Неизвестный источник счёта: {source}')
        key, history = SOURCE_KEYS.get(source), text(props.get('historyid'))
        if key is None or not history:
            continue
        if history in accounts.get(key, {}) or (key, history) in skipped:
            raise ValueError(f'Повтор счёта источника: {history}')
        if props.get('transfer') is not True:
            skipped.add((key, history))
            continue
        app, unit = text(props.get('appaccount')), text(props.get('unit'))
        if not app or not UNIT.fullmatch(unit):
            raise ValueError(f'У переносимого счёта нужны счёт приложения и единица: {history}')
        accounts.setdefault(key, {})[history] = {
            'account': app, 'unit': unit, 'balance_id': text(props.get('balanceid')) or None}
    return accounts, skipped


def read_control_points(items: list) -> dict:
    points = {}
    for item in items:
        props, name = item.get('properties', {}), text(item.get('account'))
        day = text(props.get('date'))
        date.fromisoformat(day)
        if not name or name in points or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError(f'Повтор или неполная контрольная точка: {name}')
        balances = [props.get('bankbalance'), props.get('appbalance')]
        if any(type(v) not in (int, float) for v in balances):
            raise ValueError(f'Остатки контрольной точки должны быть числами: {name}')
        points[name] = {'date': day, 'bank_balance': str(Decimal(str(balances[0]))),
                        'app_balance': str(Decimal(str(balances[1])))}
    return points


def source_key(props: dict) -> str:
    key = SOURCE_KEYS.get(props.get('source'))
    if key is None:
        raise ValueError(f'Неизвестный источник: {props.get("source")}')
    return key


def read_rules(items: list) -> dict:
    rules = {}
    for item in items:
        props = item.get('properties', {})
        payee, path = text(item.get('payee')).lower(), category_path(props.get('category'))
        tag = text(props.get('tag')) or None
        if not text(props.get('approved')):
            continue
        key = (source_key(props), payee)
        if not payee or not (path or tag):
            raise ValueError('У правила нужны продавец и категория или тег')
        if key in rules:
            raise ValueError(f'Повтор правила для продавца: {payee}')
        rules[key] = {'category_path': path, 'tag': tag}
    return rules


def read_decisions(items: list) -> dict:
    decisions = {}
    for item in items:
        props = item.get('properties', {})
        key = (source_key(props), text(item.get('sourceid')))
        mark, path, tag = props.get('action'), category_path(props.get('category')), text(props.get('tag')) or None
        if mark is not None and mark not in MARKS:
            raise ValueError(f'Неизвестная отметка решения: {mark}')
        action = MARKS.get(mark, 'record')
        if not key[1] or (action == 'record' and not (path or tag)):
            raise ValueError(f'Неполное решение по операции: {key[1]}')
        if key in decisions:
            raise ValueError(f'Повтор решения по операции: {key[1]}')
        decisions[key] = {'action': action, 'category_path': path, 'tag': tag}
    return decisions


def parse_config(collections: dict) -> dict:
    missing = [name for name in COLLECTION_NAMES if not isinstance(collections.get(name), list)]
    if missing:
        raise ValueError('Нет коллекций: ' + ', '.join(missing))
    accounts, skipped = read_accounts(collections[ACCOUNTS])
    return {'accounts': accounts, 'not_transferred': skipped,
            'control_points': read_control_points(collections[CONTROL_POINTS]),
            'rules': read_rules(collections[RULES]), 'decisions': read_decisions(collections[DECISIONS])}


def open_json(url: str, headers: dict) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode('utf-8'))


def fetch_collections(base: str, opener=open_json) -> dict:
    """Читает четыре коллекции переноса через connect-ссылку Craft, только GET."""
    base, headers = base.rstrip('/'), {'User-Agent': BROWSER_AGENT, 'Accept': 'application/json'}
    listing = opener(base + '/collections', headers).get('items', [])
    result = {}
    for name in COLLECTION_NAMES:
        found = [c for c in listing if c.get('name') == name]
        if len(found) != 1:
            raise ValueError(f'Коллекция должна быть одна: {name}')
        result[name] = opener(f'{base}/collections/{found[0]["id"]}/items', headers).get('items', [])
    return result
