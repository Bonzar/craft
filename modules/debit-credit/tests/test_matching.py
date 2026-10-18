"""Сопоставление не склеивает одинаковые покупки и не подтверждает их за человека."""

import copy
import unittest

from adapters.matching import compare_records


MAPPING = {'yabank': {'Карта': {
    'account': 'Карта Пэй', 'source_currency': 'RUB', 'currency': 'RUB'}}}


def operation(identifier='bank-1', **changes):
    return {'source': 'yabank', 'source_id': identifier, 'locator': 1,
            'timestamp': '2026-07-04T10:00:00+03:00', 'timestamp_precision': 'minute',
            'amount': '-100', 'currency': 'RUB', 'account': 'Карта',
            'cleared': True, 'status': 'completed', 'markers': [], **changes}


def entry(identifier='app-1', **changes):
    return operation(identifier, **{'source': 'debit-credit', 'account': 'Карта Пэй', **changes})


def marker(identifier='bank-1', role=None, source='yabank'):
    return {'source': source, 'id': identifier, 'role': role}


class MatchingTest(unittest.TestCase):
    def compare(self, sources, ledger, mapping=None):
        return compare_records(sources, ledger, MAPPING if mapping is None else mapping,
                               window_seconds=120)

    def test_marker_links_operation_and_reports_changed_amount_and_status(self):
        ledger = [entry(amount='-90', cleared=False, markers=[marker()])]
        result = self.compare([operation()], ledger)
        row = result['records'][0]
        self.assertEqual(row['match'], 'marker')
        self.assertEqual(row['ledger_indices'], [0])
        self.assertEqual(set(row['differences'][0]), {'amount', 'cleared'})
        self.assertFalse(result['ready'])

    def test_identical_purchases_remain_two_rows_with_shared_candidate(self):
        result = self.compare([operation('one'), operation('two')], [entry()])
        self.assertEqual(result['input_count'], 2)
        self.assertEqual(len(result['records']), 2)
        for row in result['records']:
            self.assertEqual(row['match'], 'candidate')
            self.assertEqual(row['ledger_indices'], [0])
            self.assertIn('shared_candidate', row['issues'])

    def test_cashback_marker_does_not_match_purchase_with_same_id(self):
        result = self.compare([operation()], [entry(markers=[marker(role='cashback')])])
        self.assertEqual(result['records'][0]['match'], 'no_candidate')

    def test_same_id_in_another_source_does_not_match(self):
        result = self.compare([operation()], [entry(markers=[marker(source='other-bank')])])
        self.assertEqual(result['records'][0]['match'], 'no_candidate')

    def test_two_app_records_with_same_marker_are_reported_as_conflict(self):
        ledger = [entry('one', markers=[marker()]), entry('two', markers=[marker()])]
        result = self.compare([operation()], ledger)
        self.assertEqual(result['records'][0]['match'], 'marker_conflict')
        self.assertEqual(result['records'][0]['ledger_indices'], [0, 1])

    def test_different_bank_ids_already_marked_in_app_are_not_candidates(self):
        ledger = [entry(markers=[marker('different')])]
        self.assertEqual(self.compare([operation()], ledger)['records'][0]['match'],
                         'no_candidate')

    def test_repeated_source_id_is_preserved_and_flagged(self):
        result = self.compare([operation(), operation(amount='-90')], [])
        self.assertEqual(len(result['records']), 2)
        for row in result['records']:
            self.assertIn('repeated_source_identity', row['issues'])

    def test_unknown_account_is_not_guessed_from_name(self):
        result = self.compare([operation()], [entry(account='Карта')], {})
        self.assertIn('unmapped_account', result['records'][0]['issues'])
        self.assertEqual(result['records'][0]['ledger_indices'], [])

    def test_alias_markers_match_the_same_record_without_creating_another(self):
        ledger = [entry(markers=[marker(), marker('alias')])]
        result = self.compare([operation('alias')], ledger)
        self.assertEqual(result['records'][0]['ledger_indices'], [0])

    def test_pending_or_excluded_app_records_are_not_silently_confirmed(self):
        ledger = [entry(eligible_for_matching=False, markers=[marker()])]
        row = self.compare([operation()], ledger)['records'][0]
        self.assertEqual(row['match'], 'marker')
        self.assertIn('ledger_requires_review', row['issues'])

    def test_minute_precision_matches_seconds_and_other_timezone(self):
        ledger = [entry(timestamp='2026-07-04T07:00:59+00:00',
                        timestamp_precision='second', markers=[marker()])]
        row = self.compare([operation()], ledger)['records'][0]
        self.assertNotIn('timestamp', row['differences'][0])

    def test_points_unit_mapping_must_be_explicit(self):
        source = operation(currency='YPLUS_POINTS', account='Баллы', amount='20')
        ledger = [entry(account='ЯПлюс Баллы', amount='20')]
        mapping = {'yabank': {'Баллы': {'account': 'ЯПлюс Баллы',
                                      'source_currency': 'YPLUS_POINTS', 'currency': 'RUB'}}}
        self.assertEqual(self.compare([source], ledger, mapping)['records'][0]['match'],
                         'candidate')
        self.assertEqual(self.compare([source], ledger)['records'][0]['match'], 'no_candidate')

    def test_unreferenced_ledger_records_and_source_issues_remain_visible(self):
        ledger = [entry(amount='-999')]
        result = self.compare([operation(issues=['inferred_date'])], ledger)
        self.assertEqual(result['unreferenced_ledger'], [0])
        self.assertEqual(result['ledger_records'], ledger)
        self.assertIn('inferred_date', result['records'][0]['issues'])
        self.assertEqual(result['records'][0]['record']['source_id'], 'bank-1')

    def test_inputs_are_not_mutated(self):
        values = ([operation()], [entry()], MAPPING)
        original = copy.deepcopy(values)
        self.compare(*values)
        self.assertEqual(values, original)

    def test_error_is_returned_for_naive_dates_or_nonfinite_amounts(self):
        for changes in ({'timestamp': '2026-07-04T10:00:00'}, {'amount': 'NaN'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.compare([operation(**changes)], [])

    def test_error_is_returned_for_empty_source_id(self):
        with self.assertRaises(ValueError):
            self.compare([operation('')], [])

    def test_no_id_rows_are_preserved_with_multiplicity(self):
        result = self.compare([operation(None), operation(None)], [entry(), entry('two')])
        self.assertEqual(len(result['records']), 2)
        self.assertEqual([r['ledger_indices'] for r in result['records']], [[0, 1], [0, 1]])

    def test_candidate_search_respects_window_boundary(self):
        sources = [operation()]
        inside = entry(timestamp='2026-07-04T10:02:59+03:00', timestamp_precision='second')
        outside = entry('two', timestamp='2026-07-04T10:03:01+03:00',
                        timestamp_precision='second')
        row = self.compare(sources, [inside, outside])['records'][0]
        self.assertEqual(row['ledger_indices'], [0])

    def test_marker_link_reports_wrong_account_even_outside_search_window(self):
        row = self.compare([operation()], [entry(account='Другая карта',
            timestamp='2026-07-08T10:00:00+03:00', markers=[marker()])])['records'][0]
        self.assertEqual(row['match'], 'marker')
        self.assertEqual(set(row['differences'][0]), {'account', 'timestamp'})


if __name__ == '__main__':
    unittest.main()
