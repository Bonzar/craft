"""Одна исходная операция перевода и её ожидаемая сторона на другом своём счёте."""

from copy import deepcopy

from .common import amount_decimal
from .matching import identity, mapped_record, validate_mapping, validate_records
from .movement_control import validate_snapshot

COUNTERPART_ROLE = 'transfer-counterpart'


def transfer_issues(row: dict, mapping: dict) -> list:
    if (not identity(row) or row.get('role') or row.get('issues')
            or not row.get('other_account') or not amount_decimal(row['amount'])
            or (row.get('status'), row.get('cleared')) not in
               {('completed', True), ('pending', False)}):
        return ['transfer_requires_review']
    mapped, issues = mapped_record(row, mapping)
    if issues:
        return issues
    accounts = mapping[row['source']]
    other = accounts[row['other_account']]
    if (other['source_currency'] != row['currency']
            or other['currency'] != mapped['currency']):
        return ['transfer_currency_requires_review']
    if mapped['account'] == mapped['other_account']:
        return ['transfer_same_account_requires_review']
    return []


def counterpart(row: dict) -> dict:
    peer = deepcopy(row)
    peer.update(account=row['other_account'], other_account=row['account'],
                amount=str(-amount_decimal(row['amount'])), kind='transfer_leg',
                role=COUNTERPART_ROLE, locator=str(row['locator']) + ':transfer-counterpart',
                native_id=None, markers=[], category=None, category_path=[], tag=None)
    # Реквизиты банковской карточки доказывают исходную сторону, а не другой счёт.
    for key in ('resolution', 'account_evidence', 'accounting'):
        peer.pop(key, None)
    peer.update(bank_account_ids=[], bank_fields=[], bank_category=None, mcc=None,
                cashback_hint=None, cashback_cleared=None)
    peer['transfer_accounting'] = {'derived': True, 'source_id': row['source_id']}
    return peer


def validated_origins(snapshot: dict) -> list:
    origins = deepcopy(snapshot.get('origins', [
        {'input_index': i, 'record_indices': [i]} for i in range(len(snapshot['records']))]))
    if (not isinstance(origins, list) or any(not isinstance(o, dict)
            or type(o.get('input_index')) is not int or not isinstance(o.get('record_indices'), list)
            or any(type(i) is not int for i in o['record_indices']) for o in origins)
            or sorted(i for o in origins for i in o['record_indices'])
               != list(range(len(snapshot['records'])))):
        raise ValueError('Не сошлось происхождение входных операций')
    return origins


def expand_transfers(snapshot: dict, mapping: dict) -> dict:
    validate_snapshot(snapshot)
    validate_mapping(mapping)
    if 'transfer_origins' in snapshot or any('transfer_accounting' in r for r in snapshot['records']):
        raise ValueError('Повторный разворот переводов запрещён')
    result = deepcopy(snapshot)
    records = result['records']
    origins = validated_origins(snapshot)
    transfers = [{'input_index': i, 'record_indices': [i]} for i in range(len(records))]
    parent_origins = {i: origin for origin in origins for i in origin['record_indices']}
    issues = result.setdefault('issues', [])
    for i, source in enumerate(snapshot['records']):
        if source.get('kind') != 'transfer' or source.get('resolution', {}).get('same_operation_as'):
            continue
        unresolved = transfer_issues(source, mapping)
        if unresolved:
            records[i]['issues'] = list(dict.fromkeys([*source.get('issues', []), *unresolved]))
            issues.extend({'locator': source['locator'], 'code': code} for code in unresolved)
            continue
        records[i]['kind'] = 'transfer_leg'
        records[i]['transfer_accounting'] = {'derived': False, 'before_kind': 'transfer'}
        peer_index = len(records)
        records.append(counterpart(source))
        transfers[i]['record_indices'].append(peer_index)
        parent_origins[i]['record_indices'].append(peer_index)
    added = len(records) - len(snapshot['records'])
    validate_records(records)
    if sorted(i for o in origins for i in o['record_indices']) != list(range(len(records))):
        raise RuntimeError('Не сошёлся счёт ожидаемых сторон перевода')
    result.update(adapter='transfer-accounting', input_count=snapshot['input_count'] + added,
                  source_input_count=snapshot.get('source_input_count', snapshot['input_count']),
                  source_record_count=snapshot.get('source_record_count', len(snapshot['records'])),
                  derived_count=snapshot.get('derived_count', 0) + added, origins=origins,
                  transfer_source_input_count=snapshot['input_count'], transfer_derived_count=added,
                  transfer_origins=transfers, ready=False,
                  limitations=[*snapshot.get('limitations', []), 'same_currency_transfers_only',
                               'counterpart_from_resolved_accounts', 'no_writes'])
    return result
