"""Варианты классификации с точными примерами. История не становится правилом сама."""

from collections import Counter
from copy import deepcopy

from .common import amount_decimal
from .matching import validate_records

EVIDENCE_ASSESSMENTS = {'candidate_requires_review', 'marked_fields_match'}
NON_CATEGORY_KINDS = {'transfer', 'transfer_leg'}


def profile_key(case: dict) -> tuple:
    source = case['source']
    return (source['source'], source.get('account'), source.get('currency'),
            str(amount_decimal(source['amount']).compare(0)), source.get('description'),
            source.get('role'), source.get('mcc'))


def evidence_candidate(case: dict) -> dict | None:
    source = case['source']
    if (case['assessment'] not in EVIDENCE_ASSESSMENTS or case['issues']
            or source.get('status') != 'completed' or source.get('accounting')
            or len(case['candidates']) != 1):
        return None
    candidate = case['candidates'][0]
    if (candidate.get('splits') or candidate.get('kind') in NON_CATEGORY_KINDS
            or set(candidate.get('differences', {})) - {'timestamp'}):
        return None
    return candidate


def options(values: list) -> list:
    counts = Counter(tuple(value) if isinstance(value, list) else value for value in values)
    return [{'value': list(value) if isinstance(value, tuple) else value, 'count': count}
            for value, count in sorted(counts.items(), key=lambda pair: (-pair[1], str(pair[0])))]


def build_profile(key: tuple, cases: list) -> dict:
    evidence = [(case, candidate) for case in cases
                if (candidate := evidence_candidate(case)) is not None]
    paths = [candidate.get('category_path') for _, candidate in evidence]
    if any(path is not None and (not isinstance(path, list)
                                or any(not isinstance(part, str) for part in path)) for path in paths):
        raise ValueError('Путь категории должен быть списком названий')
    return {'source': key[0], 'account': key[1], 'currency': key[2], 'direction': key[3],
            'description': key[4], 'role': key[5], 'mcc': key[6],
            'case_indices': [case['case_index'] for case in cases],
            'evidence': [{'case_index': case['case_index'], 'ledger_index': candidate['ledger_index']}
                         for case, candidate in evidence],
            'category_options': options([path for path in paths if path]),
            'kind_options': options([candidate.get('kind') for _, candidate in evidence]),
            'description_examples': options([candidate.get('description') for _, candidate in evidence]),
            'tag_examples': options([candidate.get('tag') for _, candidate in evidence])}


def classification(case: dict, profile: dict, index: int) -> dict:
    source, count = case['source'], len(profile['category_options'])
    rule = source.get('accounting', {}).get('rule')
    status = ('single_category_evidence' if count == 1 else
              'conflicting_category_evidence' if count > 1 else 'no_category_evidence')
    if case['assessment'] == 'confirmed_source_alias':
        status = 'confirmed_alias'
    elif source.get('kind') in NON_CATEGORY_KINDS:
        status = 'category_not_applicable'
    elif rule in {'cashback', 'rounding'}:
        status = 'rule_category' if source.get('category_path') else 'requires_purchase_context'
    result = {'profile_index': index, 'status': status, 'approved': False,
              'requires_context': ['tag']}
    if rule in {'cashback', 'rounding'} and source.get('category_path'):
        result['rule_category_path'] = deepcopy(source['category_path'])
    return result


def validate_review(review: dict) -> None:
    if (not isinstance(review, dict) or review.get('adapter') != 'review'
            or not isinstance(review.get('records'), list)
            or not isinstance(review.get('rejected'), list)
            or type(review.get('input_count')) is not int
            or review['input_count'] != len(review['records']) + len(review['rejected'])):
        raise ValueError('Нужен полный результат разбора')
    if [case.get('case_index') for case in review['records']] != list(range(len(review['records']))):
        raise ValueError('Нарушен порядок случаев разбора')
    validate_records([case['source'] for case in review['records']])


def add_classification_evidence(review: dict) -> dict:
    validate_review(review)
    result = deepcopy(review)
    grouped = {}
    for case in result['records']:
        grouped.setdefault(profile_key(case), []).append(case)
    profiles = []
    for key, cases in grouped.items():
        profile = build_profile(key, cases)
        index = len(profiles)
        profiles.append(profile)
        for case in cases:
            # Дополняются только случаи новой копии результата.
            case['classification'] = classification(case, profile, index)
    if sum(len(profile['case_indices']) for profile in profiles) != len(result['records']):
        raise RuntimeError('Не все случаи получили сведения о классификации')
    result.update(classification_profiles=profiles, ready=False,
                  classification_counts=dict(Counter(case['classification']['status']
                                                     for case in result['records'])))
    return result
