"""Настройки переноса из коллекций Craft: счета, контрольные точки, правила категорий, решения."""

import json
import re
import urllib.request
from datetime import date
from decimal import Decimal

ACCOUNTS = 'Счета источников'
CONTROL_POINTS = 'Контрольные точки'
RULES = 'Правила категорий и тегов'
COLLECTION_NAMES = (ACCOUNTS, CONTROL_POINTS, RULES)
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


def read_accounts(items: list) -> dict:
    """Счета источников с их счётом приложения; строка без счёта приложения ни с чем не связана."""
    accounts = {}
    for item in items:
        props = item.get('properties', {})
        source = props.get('source')
        if source not in SOURCE_KEYS and source not in OTHER_SOURCES:
            raise ValueError(f'Неизвестный источник счёта: {source}')
        key, history = SOURCE_KEYS.get(source), text(props.get('historyid'))
        app, unit = text(props.get('appaccount')), text(props.get('unit'))
        if key is None or not history or not app:
            continue
        if history in accounts.get(key, {}):
            raise ValueError(f'Повтор счёта источника: {history}')
        if not UNIT.fullmatch(unit):
            raise ValueError(f'У счёта нужна единица: {history}')
        accounts.setdefault(key, {})[history] = {
            'account': app, 'unit': unit, 'balance_id': text(props.get('balanceid')) or None}
    return accounts


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


RULE_SCOPES = {'продавец', 'операция', 'счёт'}


def read_rule(item: dict) -> tuple:
    props = item.get('properties', {})
    scope, match = props.get('by'), text(item.get('match'))
    path, tag, mark = category_path(props.get('category')), text(props.get('tag')) or None, props.get('action')
    if scope not in RULE_SCOPES or not match:
        raise ValueError(f'У правила нужны «по чему» и что искать: {match}')
    if mark is not None and (mark not in MARKS or (scope == 'продавец')
                             or (scope == 'счёт' and MARKS[mark] != 'skip')):
        raise ValueError(f'Отметка не подходит правилу: {match}')
    action = MARKS.get(mark, 'record')
    if scope == 'счёт' and (action != 'skip' or path or tag):
        raise ValueError(f'Правило на счёт только отмечает «не записывать»: {match}')
    if action == 'record' and not (path or tag):
        raise ValueError(f'У правила нужна категория или тег: {match}')
    key = (source_key(props), match.lower() if scope == 'продавец' else match)
    return scope, key, {'action': action, 'category_path': path, 'tag': tag}


def read_rules(items: list) -> tuple[dict, dict, set]:
    """Правила продавца, одной операции по её постоянному номеру и счёта, который не переносим."""
    rules, decisions, skipped = {}, {}, set()
    for item in items:
        if not text(item.get('properties', {}).get('approved')):
            continue
        scope, key, value = read_rule(item)
        if scope == 'счёт':
            if key in skipped:
                raise ValueError(f'Повтор правила: {key[1]}')
            skipped.add(key)
            continue
        target = rules if scope == 'продавец' else decisions
        if key in target:
            raise ValueError(f'Повтор правила: {key[1]}')
        target[key] = value if scope == 'операция' else {
            'category_path': value['category_path'], 'tag': value['tag']}
    return rules, decisions, skipped


def parse_config(collections: dict) -> dict:
    missing = [name for name in COLLECTION_NAMES if not isinstance(collections.get(name), list)]
    if missing:
        raise ValueError('Нет коллекций: ' + ', '.join(missing))
    accounts = read_accounts(collections[ACCOUNTS])
    rules, decisions, skipped = read_rules(collections[RULES])
    return {'accounts': accounts, 'not_transferred': skipped,
            'control_points': read_control_points(collections[CONTROL_POINTS]),
            'rules': rules, 'decisions': decisions}


def open_json(url: str, headers: dict) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode('utf-8'))


def fetch_collections(base: str, opener=open_json) -> dict:
    """Читает коллекции переноса через connect-ссылку Craft, только GET."""
    base, headers = base.rstrip('/'), {'User-Agent': BROWSER_AGENT, 'Accept': 'application/json'}
    listing = opener(base + '/collections', headers).get('items', [])
    result = {}
    for name in COLLECTION_NAMES:
        found = [c for c in listing if c.get('name') == name]
        if len(found) != 1:
            raise ValueError(f'Коллекция должна быть одна: {name}')
        result[name] = opener(f'{base}/collections/{found[0]["id"]}/items', headers).get('items', [])
    return result
