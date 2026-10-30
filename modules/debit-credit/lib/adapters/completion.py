"""Подтверждение прежней незавершённой записи, независимо от банка и роли."""

from copy import deepcopy

from .common import amount_decimal
from .debit_credit_backup import normalize_money
from .matching import identity, index_markers
from .review import validate_comparison

FINANCIAL_FIELDS = {'amount', 'cleared'}
KINDS = {'expense', 'income', 'transfer_leg'}


def confirmation_peer_is_valid(row: dict, records: dict, updates: dict) -> bool:
    peer = records.get(row.get('linked_locator'))
    if (not peer or peer['locator'] not in updates or peer['kind'] != 'transfer_leg'
            or peer.get('linked_locator') != row['locator']
            or peer['currency'] != row['currency'] or peer['account'] == row['account']
            or row.get('other_account') != peer['account']
            or peer.get('other_account') != row['account']):
        return False
    amounts = [amount_decimal(updates[r['locator']].get('amount', r['amount'])) for r in (row, peer)]
    return (updates[peer['locator']].get('cleared') is True
            and amounts[0] == amounts[1].copy_negate())


def validate_confirmation_updates(snapshot: dict, updates: list) -> None:
    records = {r['locator']: r for r in snapshot['records']}
    markers = index_markers(snapshot['records'])
    financial = {u['locator']: u['set'] for u in updates if FINANCIAL_FIELDS & u['set'].keys()}
    for locator, changes in financial.items():
        row = records.get(locator)
        if (not row or not changes.keys() <= FINANCIAL_FIELDS or changes.get('cleared') is not True
                or row['cleared'] is not False or row['kind'] not in KINDS
                or row.get('eligible_for_matching') is False or row.get('splits')
                or not row.get('markers')
                or any(len(markers[(m['source'], m['id'], m.get('role'))]) != 1
                       for m in row['markers'])):
            raise ValueError('Нужна однозначно помеченная незавершённая операция')
        amount = changes.get('amount', row['amount'])
        normalized, issues = normalize_money(amount, row['currency'])
        old = amount_decimal(row['amount'])
        if (issues or normalized != amount_decimal(amount) or not normalized or not old
                or (normalized > 0) != (old > 0)
                or (row['kind'] == 'expense' and normalized > 0)
                or (row['kind'] == 'income' and normalized < 0)):
            raise ValueError('Нужна точная итоговая сумма с прежним направлением')
        if row['kind'] == 'transfer_leg':
            if not confirmation_peer_is_valid(row, records, financial):
                raise ValueError('Обе стороны перевода должны подтверждаться согласованно')
        elif row.get('linked_locator') is not None or row.get('other_account'):
            raise ValueError('Обычная операция не должна содержать сторону перевода')


def confirmation(row: dict, ledger: list) -> tuple:
    source = row['record']
    if source.get('cleared') is False and source.get('status') == 'pending':
        return 'keep_preliminary', None
    if (source.get('cleared') is not True or source.get('status') != 'completed'
            or row['issues'] or row['match'] != 'marker' or len(row['ledger_indices']) != 1):
        return 'requires_review', None
    position = row['ledger_indices'][0]
    actual = ledger[position]
    changes = row['differences'].get(position, row['differences'].get(str(position), {}))
    key = identity(source)
    if (not key or not any((m['source'], m['id'], m.get('role')) == key
                           for m in actual.get('markers', []))
            or not changes.keys() <= FINANCIAL_FIELDS or actual.get('kind') not in KINDS
            or source.get('kind') not in (None, actual['kind'])
            or actual.get('eligible_for_matching') is False or actual.get('splits')):
        return 'requires_review', None
    if actual.get('cleared') is True:
        return ('already_confirmed' if not changes else 'requires_review'), None
    # Операцию банка без вида нельзя подтверждать в сторону перевода: записывать нечего
    # только у уже завершённой записи, как обмен баллов, записанный переводом.
    if source.get('kind') is None and actual['kind'] == 'transfer_leg':
        return 'requires_review', None
    if actual.get('cleared') is not False:
        return 'requires_review', None
    update = {'locator': actual['locator'], 'set': {'amount': source['amount'], 'cleared': True}}
    return 'confirm_existing', update


def plan_confirmations(comparison: dict) -> dict:
    """Предложение для явного плана копии. Само по себе ничего не записывает."""
    validate_comparison(comparison)
    decisions, proposed = [], []
    for i, row in enumerate(comparison['records']):
        action, update = confirmation(row, comparison['ledger_records'])
        decisions.append({'input_index': i, 'action': action})
        if update is not None:
            proposed.append((i, update))
    if len({u['locator'] for _, u in proposed}) != len(proposed):
        raise ValueError('Несколько подтверждений ссылаются на одну запись')
    snapshot = {'records': comparison['ledger_records']}
    records = {r['locator']: r for r in snapshot['records']}
    all_updates = {u['locator']: u['set'] for _, u in proposed}
    updates = []
    for i, update in proposed:
        row = records[update['locator']]
        related = [update]
        if row['kind'] == 'transfer_leg':
            if not confirmation_peer_is_valid(row, records, all_updates):
                decisions[i]['action'] = 'requires_review'
                continue
            peer = row['linked_locator']
            related.append({'locator': peer, 'set': all_updates[peer]})
        try:
            validate_confirmation_updates(snapshot, related)
        except ValueError:
            decisions[i]['action'] = 'requires_review'
            continue
        updates.append(update)
    return {'input_count': comparison['input_count'], 'decisions': decisions, 'updates': updates,
            'rejected': deepcopy(comparison['rejected']), 'ready': False,
            'limitations': ['proposal_only', 'does_not_add_or_write_operations',
                            'split_and_cross_currency_transfer_confirmations_require_review']}
