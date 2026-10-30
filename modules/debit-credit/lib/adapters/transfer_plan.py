"""Пачка однозначных переводов. Непарные и похожие на существующие строки не вносятся."""

from collections import Counter
from datetime import datetime

from .backup_additions import validate_additions
from .common import amount_decimal
from .markers import Marker
from .matching import identity, index_markers, mapped_record, validate_mapping
from .movement_control import validate_snapshot


def initial_state(row: dict, repeated: Counter, start: datetime, end: datetime) -> str | None:
    if not start <= datetime.fromisoformat(row['timestamp']) < end:
        return 'outside_period'
    if row.get('kind') != 'transfer_leg':
        return 'not_transfer'
    if (row.get('status') != 'completed' or row.get('cleared') is not True or row.get('issues')
            or row.get('resolution', {}).get('same_operation_as') or not identity(row)
            or repeated[identity(row)] != 1 or not amount_decimal(row['amount'])):
        return 'requires_review'
    return None


def rule_peer(row: dict, valid: dict) -> list | None:
    """Стороны обмена баллов связаны правилом, а не близостью по времени."""
    accounting = row.get('accounting') or {}
    if accounting.get('rule') != 'points_exchange':
        return None
    return [j for j, other in valid.items() if other['source_id'] == accounting.get('peer')]


def pair_candidates(valid: dict, window: int) -> dict:
    candidates = {}
    for i, row in valid.items():
        peers = rule_peer(row, valid)
        if peers is not None:
            candidates[i] = peers
            continue
        found = []
        for j, other in valid.items():
            if i == j or (row['account'], row['other_account'], row['currency']) != (
                    other['other_account'], other['account'], other['currency']):
                continue
            if amount_decimal(row['amount']) + amount_decimal(other['amount']):
                continue
            gap = abs((datetime.fromisoformat(row['timestamp'])
                       - datetime.fromisoformat(other['timestamp'])).total_seconds())
            if gap <= window:
                found.append(j)
        candidates[i] = found
    return candidates


def existing_candidates(row: dict, ledger: list, window: int) -> list:
    stamp = datetime.fromisoformat(row['timestamp'])
    return [i for i, other in enumerate(ledger)
            if (row['account'], row['currency']) == (other['account'], other['currency'])
            and amount_decimal(row['amount']) == amount_decimal(other['amount'])
            and abs((stamp - datetime.fromisoformat(other['timestamp'])).total_seconds()) <= window]


def addition(row: dict, index: int, peer: int) -> dict:
    marker = Marker(row['source'], row['source_id'], row.get('role')).render()
    texts = [row.get('notes'), row.get('raw', {}).get('detail', {}).get('comment'), marker]
    notes = '\n'.join(dict.fromkeys(text for text in texts if isinstance(text, str) and text.strip()))
    direction = 'на' if amount_decimal(row['amount']) < 0 else 'с'
    return {'key': f'transfer-{index}', 'peer_key': f'transfer-{peer}', 'kind': 'transfer_leg',
            'account': row['account'], 'currency': row['currency'], 'amount': row['amount'],
            'timestamp': row['timestamp'], 'cleared': True,
            'description': f'Перевод {direction} {row["other_account"]}', 'notes': notes,
            'category_path': [], 'tag': None, 'payee': None}


def validate_inputs(source: dict, ledger: dict, mapping: dict, start, end, windows) -> None:
    validate_snapshot(source)
    validate_snapshot(ledger)
    validate_mapping(mapping)
    if (source.get('coverage') != 'snapshot_complete' or source['rejected'] or ledger['rejected']
            or ledger.get('coverage') != 'backup_snapshot' or ledger.get('date_filter')
            or ledger.get('issues') or not isinstance(ledger.get('accounts'), list)):
        raise ValueError('Нужны полный источник и полный снимок приложения без ошибок разбора')
    if any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
           for value in (start, end)) or start >= end:
        raise ValueError('Нужен возрастающий период с часовыми поясами')
    if any(type(value) is not int or value < 0 for value in windows):
        raise ValueError('Окна проверки должны быть целыми неотрицательными числами секунд')


def select_pair(i, j, valid, records, ledger, markers, accounts, window) -> str:
    rows = [valid[i], valid[j]]
    if any(markers.get(identity(row)) for row in rows):
        return 'already_marked'
    if any(accounts[(row['account'], row['currency'])] != 1 for row in rows):
        return 'account_absent_or_ambiguous'
    found = [] if window is None else sorted(set(existing_candidates(rows[0], ledger, window)
                                                 + existing_candidates(rows[1], ledger, window)))
    if found:
        records[i]['ledger_candidates'] = found
        records[j]['ledger_candidates'] = found
        return 'existing_candidate'
    return 'selected'


def build_transfer_plan(source: dict, ledger: dict, mapping: dict, start: datetime, end: datetime,
                        *, pair_window_seconds: int, ledger_window_seconds: int,
                        check_existing: bool = True) -> dict:
    validate_inputs(source, ledger, mapping, start, end, (pair_window_seconds, ledger_window_seconds))
    repeated = Counter(identity(row) for row in source['records'] if identity(row))
    records, valid = [], {}
    for i, row in enumerate(source['records']):
        state = initial_state(row, repeated, start, end)
        mapped, issues = mapped_record(row, mapping) if state is None else (None, [])
        if state is None:
            state = 'mapping_requires_review' if issues else (
                'internal_same_account' if mapped['account'] == mapped.get('other_account') else None)
        records.append({'source_index': i, 'state': state, 'issues': issues})
        if state is None:
            valid[i] = mapped
    candidates = pair_candidates(valid, pair_window_seconds)
    accounts = Counter((a['name'], a['currency']) for a in ledger['accounts'] if not a.get('deleted_locally'))
    markers = index_markers(ledger['records'])
    additions, selected = [], []
    for i, peers in candidates.items():
        if records[i]['state'] is not None:
            continue
        records[i]['peer_candidates'] = peers
        if len(peers) != 1 or candidates[peers[0]] != [i]:
            records[i]['state'] = 'unpaired' if not peers else 'ambiguous_pair'
            continue
        j = peers[0]
        state = select_pair(i, j, valid, records, ledger['records'], markers, accounts,
                            ledger_window_seconds if check_existing else None)
        records[i]['state'] = records[j]['state'] = state
        records[j]['peer_candidates'] = [i]
        if state == 'selected':
            additions.extend([addition(valid[i], i, j), addition(valid[j], j, i)])
            selected.append([i, j])
    if len(records) != len(source['records']) or any(row['state'] is None for row in records):
        raise RuntimeError('Не все исходные записи получили результат подготовки')
    validate_additions(additions, ledger['records'])
    return {'schema_version': 1, 'adapter': 'transfer-plan', 'input_count': source['input_count'],
            'records': records, 'rejected': [], 'issues': [], 'ready': False,
            'coverage': 'selected_transfers_only', 'selected_pairs': selected,
            'selected_pair_count': len(selected), 'additions': additions,
            'state_counts': dict(Counter(row['state'] for row in records)),
            'period': {'start': start.isoformat(), 'end': end.isoformat()},
            'pair_window_seconds': pair_window_seconds, 'ledger_window_seconds': ledger_window_seconds,
            'limitations': ['pairing_is_not_native_bank_link_proof', 'existing_candidates_require_review',
                            'no_account_creation', 'no_existing_record_changes', 'no_writes']}
