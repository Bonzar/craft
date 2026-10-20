"""Общий разбор результатов сопоставления без решений о записи в приложение."""

from collections import Counter
from copy import deepcopy
from datetime import datetime

from .common import amount_decimal
from .matching import validate_records

MATCHES = {'marker', 'marker_conflict', 'candidate', 'no_candidate'}


def validate_comparison(comparison: dict) -> None:
    if not isinstance(comparison, dict) or comparison.get('adapter') != 'comparison':
        raise ValueError('Нужен результат сопоставления')
    rows, rejected = comparison.get('records'), comparison.get('rejected')
    ledger = comparison.get('ledger_records')
    if not all(isinstance(value, list) for value in (rows, rejected, ledger)):
        raise ValueError('Нужны операции, отклонённый вход и записи приложения')
    if (type(comparison.get('input_count')) is not int
            or comparison['input_count'] != len(rows) + len(rejected)):
        raise ValueError('Не сошёлся счёт входа')
    validate_records(ledger)
    validate_records([row['record'] for row in rows])
    for row in rows:
        if row.get('match') not in MATCHES or not isinstance(row.get('issues'), list):
            raise ValueError('Неизвестный результат сопоставления')
        indices = row.get('ledger_indices')
        if (not isinstance(indices, list) or len(indices) != len(set(indices))
                or any(type(i) is not int or not 0 <= i < len(ledger) for i in indices)):
            raise ValueError('Некорректные ссылки на записи приложения')
    expected = set(range(len(ledger))) - {i for row in rows for i in row['ledger_indices']}
    if set(comparison.get('unreferenced_ledger', [])) != expected:
        raise ValueError('Не сошёлся состав записей приложения без сопоставления')


def visible_record(record: dict) -> dict:
    # Полный исходный ответ остаётся во входе с хешем, в вопросе нужны поля операции.
    return deepcopy({key: value for key, value in record.items() if key != 'raw'})


def make_case(index: int, row: dict, ledger: list) -> dict:
    candidates = []
    for position in row['ledger_indices']:
        differences = row['differences'].get(position, row['differences'].get(str(position), {}))
        candidates.append({**visible_record(ledger[position]), 'ledger_index': position,
                           'differences': deepcopy(differences)})
    assessment = row['match']
    if assessment == 'candidate':
        assessment = 'candidate_requires_review'
    elif assessment == 'marker':
        assessment = ('marked_fields_match' if not row['issues'] and candidates
                      and not candidates[0]['differences'] else 'marked_requires_review')
    return {'case_index': index, 'source': visible_record(row['record']),
            'assessment': assessment, 'issues': deepcopy(row['issues']),
            'candidates': candidates}


def group_key(case: dict) -> tuple:
    source = case['source']
    return (source['source'], source.get('account'), source.get('currency'),
            str(amount_decimal(source['amount']).compare(0)), source.get('description'),
            source.get('status'), case['assessment'], tuple(sorted(set(case['issues']))))


def build_groups(cases: list) -> list:
    grouped = {}
    for case in cases:
        key = group_key(case)
        if key not in grouped:
            grouped[key] = {'source': key[0], 'account': key[1], 'currency': key[2],
                            'direction': key[3], 'description': key[4], 'status': key[5],
                            'assessment': key[6], 'issues': list(key[7]), 'case_indices': []}
        grouped[key]['case_indices'].append(case['case_index'])
    for group in grouped.values():
        stamps = [cases[i]['source']['timestamp'] for i in group['case_indices']]
        # UTC offsets may differ, so ISO text order alone is not chronological order.
        group.update(count=len(stamps), first_timestamp=min(stamps, key=datetime.fromisoformat),
                     last_timestamp=max(stamps, key=datetime.fromisoformat))
    return list(grouped.values())


def link_confirmed_aliases(cases: list) -> None:
    positions = {}
    for case in cases:
        source = case['source']
        positions.setdefault((source['source'], source['source_id']), []).append(case['case_index'])
    for case in cases:
        alias = case['source'].get('resolution', {}).get('same_operation_as')
        if not alias:
            continue
        targets = positions.get((alias['source'], alias['id']), [])
        if len(targets) != 1 or targets[0] == case['case_index']:
            raise ValueError('Неоднозначная цель подтверждённого отображения')
        target = cases[targets[0]]
        if target['source'].get('resolution', {}).get('same_operation_as'):
            raise ValueError('Цель связи должна быть основной операцией')
        # Локально дополняем только что созданные случаи, вход не меняется.
        case.update(same_operation_case_index=targets[0],
                    source_match_assessment=case['assessment'], assessment='confirmed_source_alias')


def build_pending_rechecks(ledger: list) -> list:
    """Повторное чтение определяется всей переданной выгрузкой, не новым периодом."""
    result = []
    for index, row in enumerate(ledger):
        if row.get('cleared') is not False:
            continue
        identities = deepcopy(row.get('markers', []))
        issues = [] if identities else ['missing_source_identity']
        if row.get('eligible_for_matching') is False:
            issues.append('ledger_requires_review')
        result.append({**visible_record(row), 'ledger_index': index,
                       'recheck_sources': identities, 'recheck_issues': issues})
    if len(result) != sum(row.get('cleared') is False for row in ledger):
        raise RuntimeError('Не все незавершённые записи попали в повторную проверку')
    return result


def build_review(comparison: dict) -> dict:
    """Группы помогают разбору, их число не равно числу вопросов человеку."""
    validate_comparison(comparison)
    ledger = comparison['ledger_records']
    cases = [make_case(i, row, ledger) for i, row in enumerate(comparison['records'])]
    link_confirmed_aliases(cases)
    groups = build_groups(cases)
    if sorted(i for group in groups for i in group['case_indices']) != list(range(len(cases))):
        raise RuntimeError('Не все операции попали в разбор ровно один раз')
    return {'schema_version': 1, 'adapter': 'review', 'input_count': comparison['input_count'],
            'records': cases, 'rejected': deepcopy(comparison['rejected']), 'groups': groups,
            'assessment_counts': dict(Counter(case['assessment'] for case in cases)),
            'unreferenced_ledger': [{**visible_record(ledger[i]), 'ledger_index': i}
                                    for i in comparison['unreferenced_ledger']],
            'ledger_input_count': len(ledger), 'pending_rechecks': build_pending_rechecks(ledger),
            'issues': deepcopy(comparison['issues']), 'coverage': comparison['coverage'],
            'inputs_sha256': deepcopy(comparison.get('inputs_sha256', {})),
            'source_input': deepcopy(comparison.get('source_input', {})),
            'ledger_input': deepcopy(comparison.get('ledger_input', {})),
            'ready': False, 'human_question_count': None,
            'limitations': ['groups_are_not_questions', 'no_candidate_is_not_missing',
                            'pending_rechecks_cover_provided_ledger_only',
                            'pending_source_collection_not_performed',
                            'historical_fields_are_evidence_not_rules',
                            'accounting_expansion_not_performed', 'no_writes',
                            *comparison.get('limitations', [])]}
