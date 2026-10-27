"""История даёт проверяемые варианты, а не одобряет категорию или тег."""

import copy
import unittest

from adapters.classification import add_classification_evidence
from adapters.matching import compare_records
from adapters.review import build_review
from test_matching import operation, entry, MAPPING


def review(sources, ledger):
    return build_review(compare_records(sources, ledger, MAPPING, window_seconds=120))


def profile_for(result, case_index):
    index = result['records'][case_index]['classification']['profile_index']
    return result['classification_profiles'][index]


class ClassificationTest(unittest.TestCase):
    def test_category_is_proposed_with_exact_history_reference_without_changing_source(self):
        old = operation('old', description='Shop')
        new = operation('new', description='Shop', timestamp='2026-08-01T10:00:00+03:00')
        before = review([old, new], [entry(category_path=['Продукты'], kind='expense', tag='Свидание')])
        original = copy.deepcopy(before)
        result = add_classification_evidence(before)
        profile = profile_for(result, 1)
        self.assertEqual(profile['category_options'][0]['value'], ['Продукты'])
        self.assertEqual(profile['evidence'][0], {'case_index': 0, 'ledger_index': 0})
        self.assertEqual(result['records'][1]['classification']['status'], 'single_category_evidence')
        self.assertNotIn('tag', result['records'][1]['classification'])
        self.assertEqual(profile['tag_examples'][0]['value'], 'Свидание')
        self.assertFalse(result['ready'])
        self.assertEqual(before, original)

    def test_conflicting_categories_are_preserved_as_options(self):
        sources = [operation('a', description='Shop'), operation('b', description='Shop', amount='-200')]
        ledger = [entry('a', category_path=['Продукты']),
                  entry('b', amount='-200', category_path=['Для дома'])]
        result = add_classification_evidence(review(sources, ledger))
        self.assertEqual(result['records'][0]['classification']['status'], 'conflicting_category_evidence')
        self.assertEqual(len(profile_for(result, 0)['category_options']), 2)

    def test_shared_candidates_do_not_become_historical_evidence(self):
        result = add_classification_evidence(review([operation('one'), operation('two')],
                                                   [entry(category_path=['Продукты'])]))
        self.assertEqual(profile_for(result, 0)['evidence'], [])
        self.assertEqual(result['records'][0]['classification']['status'], 'no_category_evidence')

    def test_profiles_are_isolated_by_account_currency_direction_and_role(self):
        sources = [operation('a'), operation('b', account='Other'),
                   operation('c', currency='EUR'), operation('d', amount='100'),
                   operation('e', role='cashback')]
        result = add_classification_evidence(review(sources, []))
        self.assertEqual(len(result['classification_profiles']), 5)

    def test_accounting_category_is_shown_separately_from_historical_options(self):
        source = operation(category_path=['Баллы'], category='Баллы', role='cashback',
                           accounting={'rule': 'cashback', 'derived': True})
        result = add_classification_evidence(review([source], [entry(category_path=['Wrong'])]))
        self.assertEqual(result['records'][0]['classification']['rule_category_path'], ['Баллы'])
        self.assertEqual(profile_for(result, 0)['evidence'], [])

    def test_split_categories_are_not_reduced_to_one_category(self):
        result = add_classification_evidence(review([operation()], [entry(
            category_path=['Продукты'], splits=[{'amount': '-50'}, {'amount': '-50'}])]))
        self.assertEqual(profile_for(result, 0)['category_options'], [])

    def test_error_is_reported_when_review_input_count_does_not_match_cases(self):
        broken = review([operation()], [])
        broken['input_count'] = 2
        with self.assertRaises(ValueError):
            add_classification_evidence(broken)
