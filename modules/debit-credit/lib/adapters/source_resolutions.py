"""Явные подтверждения человека к одному снимку источника, без потери исходных строк."""

from collections import defaultdict
from copy import deepcopy

from .matching import validate_records

EXPECTED_FIELDS = {'timestamp', 'amount', 'currency', 'account', 'status', 'cleared'}
EDITABLE_FIELDS = {'account', 'other_account', 'kind'}
KINDS = {'expense', 'income', 'transfer', 'transfer_leg'}


def record_key(value: dict) -> tuple:
    source, identifier = value.get('source'), value.get('id', value.get('source_id'))
    if not isinstance(source, str) or not source or not isinstance(identifier, str) or not identifier:
        raise ValueError('Подтверждению нужны источник и ID')
    return source, identifier


def validate_changes(spec: dict) -> None:
    changes = spec.get('set')
    if not isinstance(changes, dict) or set(changes) - EDITABLE_FIELDS:
        raise ValueError('Неподдержанные поля подтверждения')
    if any(not isinstance(value, str) or not value for value in changes.values()):
        raise ValueError('Значения подтверждения должны быть непустыми строками')
    if 'kind' in changes and changes['kind'] not in KINDS:
        raise ValueError('Неизвестный тип операции')
    if not changes and not spec.get('same_operation_as'):
        raise ValueError('Пустое подтверждение')
    if not isinstance(spec.get('reason'), str) or not spec['reason'].strip():
        raise ValueError('Нужно основание подтверждения')


def validate_specs(records: list, specs: list) -> dict:
    if not isinstance(specs, list):
        raise ValueError('Нужен список подтверждений')
    positions = defaultdict(list)
    for i, row in enumerate(records):
        if row['source_id'] is not None:
            positions[record_key(row)].append(i)
    selected = {}
    aliases = {}
    for spec in specs:
        if not isinstance(spec, dict):
            raise ValueError('Нужно объектное подтверждение')
        key = record_key(spec)
        if key in selected or len(positions[key]) != 1:
            raise ValueError('Цель подтверждения отсутствует, повторяется или неоднозначна')
        validate_changes(spec)
        expected = spec.get('expect')
        row = records[positions[key][0]]
        if (not isinstance(expected, dict) or set(expected) != EXPECTED_FIELDS
                or any(row[field] != value for field, value in expected.items())):
            raise ValueError('Исходные значения операции не совпали с подтверждением')
        if spec.get('same_operation_as'):
            target = record_key(spec['same_operation_as'])
            if target == key or len(positions[target]) != 1:
                raise ValueError('Неоднозначная или отсутствующая цель связи')
            aliases[key] = target
        selected[key] = spec
    if any(target in aliases for target in aliases.values()):
        raise ValueError('Цепочка подтверждений должна вести прямо к исходной операции')
    return selected


def resolve_record(row: dict, spec: dict | None) -> dict:
    result = deepcopy(row)
    if spec is None:
        return result
    if 'resolution' in row:
        raise ValueError('Подтверждения применяются к исходному снимку, не поверх прежних')
    result.update(spec['set'])
    result['resolution'] = {'before': {key: row.get(key) for key in spec['set']},
                            'set': deepcopy(spec['set']), 'reason': spec['reason']}
    if spec.get('same_operation_as'):
        result['resolution']['same_operation_as'] = deepcopy(spec['same_operation_as'])
    if spec['set'].get('account'):
        result['issues'] = [code for code in row.get('issues', []) if code != 'unknown_account']
    return result


def apply_resolutions(snapshot: dict, specs: list) -> dict:
    if (not isinstance(snapshot, dict) or not isinstance(snapshot.get('records'), list)
            or not isinstance(snapshot.get('rejected'), list)
            or type(snapshot.get('input_count')) is not int
            or snapshot['input_count'] != len(snapshot['records']) + len(snapshot['rejected'])):
        raise ValueError('Не сошёлся счёт входа источника')
    validate_records(snapshot['records'])
    selected = validate_specs(snapshot['records'], specs)
    result = deepcopy(snapshot)
    result['records'] = [resolve_record(row, selected.get(record_key(row))
                                      if row['source_id'] is not None else None)
                         for row in snapshot['records']]
    validate_records(result['records'])
    result['resolution_count'] = len(selected)
    result['ready'] = False
    old_record_issues = {(row['locator'], code) for row in snapshot['records']
                         for code in row.get('issues', [])}
    global_issues = [issue for issue in snapshot.get('issues', [])
                     if set(issue) != {'locator', 'code'}
                     or (issue['locator'], issue['code']) not in old_record_issues]
    result['issues'] = deepcopy(global_issues) + [
        {'locator': row['locator'], 'code': code}
        for row in result['records'] for code in row.get('issues', [])]
    return result
