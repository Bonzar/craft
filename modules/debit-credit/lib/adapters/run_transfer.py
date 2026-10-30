"""Перенос одной командой: однозначное — в план копии, остальное — на разбор с причиной."""

from collections import Counter
from datetime import datetime
from decimal import Decimal

from .backup_additions import validate_additions
from .completion import plan_confirmations
from .common import amount_decimal
from .markers import Marker
from .matching import compare_records
from .transfer_accounting import expand_transfers
from .transfer_plan import build_transfer_plan
from .yandex_accounting import expand_accounting

FAILED_STATUSES = {'failed', 'canceled'}


def same_name(first: str, second: str) -> bool:
    # В приложении встречается неразрывный пробел, в коллекции — обычный.
    return ' '.join(first.split()) == ' '.join(second.split())


def build_mapping(config: dict, ledger: dict) -> dict:
    """Карта счетов в формате сопоставления: имя и валюта счёта берутся из приложения."""
    accounts = [a for a in ledger['accounts'] if not a.get('deleted_locally')]
    mapping = {}
    for key, rows in config['accounts'].items():
        for history, row in rows.items():
            found = [a for a in accounts if same_name(a['name'], row['account'])]
            if len(found) == 1:
                mapping.setdefault(key, {})[history] = {
                    'account': found[0]['name'], 'source_currency': row['unit'],
                    'currency': found[0]['currency']}
    return mapping


def control_date(config: dict, account: str) -> datetime | None:
    point = next((p for name, p in config['control_points'].items() if same_name(name, account)), None)
    return None if point is None else datetime.fromisoformat(point['date'] + 'T00:00:00+03:00')


def addition(key: str, row: dict, target: dict, category_path: list, tag: str | None) -> dict:
    marker = Marker(row['source'], row['source_id'], row.get('role')).render()
    notes = '\n'.join(text for text in (row.get('notes'), marker) if isinstance(text, str) and text.strip())
    amount = amount_decimal(row['amount'])
    return {'key': key, 'peer_key': None, 'kind': 'expense' if amount < 0 else 'income',
            'account': target['account'], 'currency': target['currency'], 'amount': row['amount'],
            'timestamp': row['timestamp'], 'cleared': row.get('cleared') is True,
            'description': row.get('description') or category_path[-1], 'notes': notes,
            'category_path': category_path, 'tag': tag, 'payee': None}


def labels(row: dict, config: dict) -> tuple[list, str | None, str | None]:
    """Категория и тег: разовое решение по номеру операции, затем одобренное правило продавца."""
    decision = config['decisions'].get((row['source'], row['source_id']), {})
    rule = config['rules'].get((row['source'], (row.get('description') or '').strip().lower()), {})
    path = decision.get('category_path') or row.get('category_path') or rule.get('category_path') or []
    tag = decision.get('tag') or rule.get('tag')
    return path, tag, decision.get('action')


MARKED_OUTCOMES = {'already_confirmed': 'already_recorded', 'keep_preliminary': 'already_recorded',
                   'confirm_existing': 'confirmed'}


def marked_outcome(action: str | None) -> tuple:
    """Запись с меткой уже есть: сходится, подтверждается или идёт на разбор, но не теряется."""
    outcome = MARKED_OUTCOMES.get(action)
    return (outcome, None) if outcome else ('review', 'marker_requires_review')


def purchase_outcome(i: int, item: dict, key: str, mapping: dict, config: dict, confirmed: str | None) -> tuple:
    row = item['record']
    path, tag, action = labels(row, config)
    if action == 'skip':
        return 'skipped', None
    if item['match'] in ('marker', 'marker_conflict'):
        return marked_outcome(confirmed) if item['match'] == 'marker' else ('review', 'marker_conflict')
    if item['match'] == 'candidate':
        return ('same_as_existing', None) if action == 'same' else ('review', 'existing_without_marker')
    if action == 'same':
        return 'review', 'same_without_existing'
    issues = [code for code in item['issues'] if code != 'rounding_parent_requires_review'
              or len(row.get('accounting', {}).get('parent_candidates', [])) != 1]
    if issues:
        return 'review', 'source_issues'
    if not path:
        return 'review', 'needs_category'
    target = mapping[key][row['account']]
    added = addition(f'{key}-{i}', row, target, path, tag)
    return ('added_by_decision' if action == 'record' else 'added_by_rule'), added


def scope_outcome(row: dict, key: str, target: dict | None, config: dict, start, end) -> str | None:
    """Почему запись вне переноса; None — запись в переносе."""
    moment = datetime.fromisoformat(row['timestamp'])
    point = control_date(config, target['account']) if target else None
    if row.get('not_recorded_reason') or row.get('status') in FAILED_STATUSES:
        return 'not_recorded'
    # Банк показал перевод двумя операциями; вторая сторона уже учтена через первую.
    if row.get('resolution', {}).get('same_operation_as'):
        return 'same_operation'
    if (key, row.get('account')) in config['not_transferred']:
        return 'not_transferred'
    if not start <= moment < end:
        return 'outside_period'
    if point is not None and moment < point:
        return 'before_control_point'
    return None


def scoped_transfers(transfers: dict, excluded: dict, key: str) -> tuple[list, set, set]:
    """Пара пишется, только если обе стороны в переносе; сторона без пары идёт на разбор."""
    additions = {row['key']: row for row in transfers['additions']}
    kept, selected, orphaned = [], set(), set()
    for i, j in transfers['selected_pairs']:
        out = {i, j} & excluded.keys()
        if out:
            orphaned |= {i, j} - out
            continue
        selected |= {i, j}
        for index in (i, j):
            row = additions[f'transfer-{index}']
            kept.append({**row, 'key': f'{key}-{row["key"]}', 'peer_key': f'{key}-{row["peer_key"]}'})
    return kept, selected, orphaned


def scoped_confirmations(comparison: dict, confirmations: dict, excluded: dict) -> tuple[list, dict, set]:
    """Подтверждения в переносе; у стороны перевода вторая сторона тоже должна быть в переносе."""
    indices = [d['input_index'] for d in confirmations['decisions'] if d['action'] == 'confirm_existing']
    by_locator = dict(zip((u['locator'] for u in confirmations['updates']), indices))
    ledger = {row['locator']: row for row in comparison['ledger_records']}
    actions = {d['input_index']: d['action'] for d in confirmations['decisions']}
    updates, orphaned = [], set()
    for update, i in zip(confirmations['updates'], indices):
        peer = by_locator.get(ledger[update['locator']].get('linked_locator'))
        if i in excluded:
            continue
        if peer is not None and peer in excluded:
            orphaned.add(i)
            continue
        updates.append(update)
    return updates, actions, orphaned


def plan_source(snapshot: dict, ledger: dict, config: dict, mapping: dict, start, end, windows: dict) -> dict:
    key = snapshot['source']
    expected = snapshot
    if key == 'yabank':
        expected = expand_accounting(expected, parent_window_seconds=windows['parent_window_seconds'])
    expected = expand_transfers(expected, mapping)
    comparison = compare_records(expected['records'], ledger['records'], mapping,
                                 window_seconds=windows['window_seconds'])
    transfers = build_transfer_plan(expected, ledger, mapping, start, end,
                                    pair_window_seconds=windows['pair_window_seconds'],
                                    ledger_window_seconds=windows['ledger_window_seconds'])
    targets = [mapping.get(key, {}).get(item['record'].get('account')) for item in comparison['records']]
    excluded = {i: outcome for i, item in enumerate(comparison['records'])
                if (outcome := scope_outcome(item['record'], key, targets[i], config, start, end))}
    additions, selected, transfer_orphans = scoped_transfers(transfers, excluded, key)
    updates, actions, confirm_orphans = scoped_confirmations(
        comparison, plan_confirmations(comparison), excluded)
    outcomes, review = {}, []
    for i, item in enumerate(comparison['records']):
        row = item['record']
        outcome, extra = excluded.get(i), None
        if outcome:
            pass
        elif targets[i] is None:
            outcome, extra = 'review', 'unmapped_account'
        elif i in transfer_orphans or i in confirm_orphans:
            outcome, extra = 'review', 'peer_out_of_scope'
        elif row.get('kind') == 'transfer_leg':
            if i in selected:
                outcome = 'transfer_added'
            elif item['match'] == 'marker':
                outcome, extra = marked_outcome(actions.get(i))
            else:
                outcome, extra = 'review', transfers['records'][i]['state']
        else:
            outcome, extra = purchase_outcome(i, item, key, mapping, config, actions.get(i))
            if isinstance(extra, dict):
                additions.append(extra)
                extra = None
        outcomes[row['source_id'] if not row.get('role') else f'{row["source_id"]}:{row["role"]}'] = outcome
        if outcome == 'review':
            path, tag, _ = labels(row, config)
            review.append({'source': key, 'source_id': row['source_id'], 'role': row.get('role'),
                           'reason': extra, 'timestamp': row['timestamp'], 'amount': row['amount'],
                           'account': row.get('account'), 'description': row.get('description'),
                           'suggested_category': path or None, 'suggested_tag': tag})
    return {'additions': additions, 'updates': updates, 'outcomes': outcomes, 'review': review}


def plan_transfer(sources: list, ledger: dict, config: dict, start: datetime, end: datetime, **windows) -> dict:
    mapping = build_mapping(config, ledger)
    if len({s['source'] for s in sources}) != len(sources):
        raise ValueError('Каждый источник передаётся один раз')
    parts = [plan_source(s, ledger, config, mapping, start, end, windows) for s in sources]
    additions = [row for part in parts for row in part['additions']]
    updates = [row for part in parts for row in part['updates']]
    validate_additions(additions, ledger['records'])
    outcomes = {k: v for part in parts for k, v in part['outcomes'].items()}
    return {'additions': additions, 'updates': updates, 'outcomes': outcomes,
            'outcome_counts': dict(Counter(outcomes.values())),
            'review': [row for part in parts for row in part['review']]}


def check_balances(ledger: dict, sources: list, config: dict) -> dict:
    """Остаток приложения с незавершёнными против суммы банковских счетов, если все они прочитаны."""
    read = {}
    for snapshot in sources:
        balances = snapshot.get('balances') or {}
        for row in balances.get('accounts') or []:
            read[(snapshot['source'], row['account'])] = Decimal(row['amount'])
    sides = {}
    for key, rows in config['accounts'].items():
        for row in rows.values():
            bank = read.get((key, row['balance_id'])) if row['balance_id'] else None
            sides.setdefault(row['account'], []).append(bank)
    result = {}
    for account in ledger['accounts']:
        parts = next((v for name, v in sides.items() if same_name(name, account['name'])), None)
        if parts is None:
            continue
        if any(part is None for part in parts):
            result[account['name']] = {'state': 'not_checked', 'reason': 'bank_balance_not_read'}
            continue
        bank, app = sum(parts, Decimal(0)), Decimal(account['uncleared_balance'])
        state = 'matches' if bank == app else 'differs'
        result[account['name']] = ({'state': state} if state == 'matches' else
                                   {'state': state, 'bank': str(bank), 'app': str(app),
                                    'difference': str(bank - app)})
    return result
