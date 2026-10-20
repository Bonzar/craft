"""Отчёт сохраняет все случаи и показывает основания без одобрения записи."""

import copy
import unittest

from adapters.matching import compare_records
from adapters.review import build_review
from test_matching import MAPPING, operation, entry, marker


class ReviewTest(unittest.TestCase):
    def comparison(self, sources, ledger):
        return compare_records(sources, ledger, MAPPING, window_seconds=120)

    def test_identical_operations_remain_separate_in_one_context_group(self):
        comparison = self.comparison([operation('one'), operation('two')], [entry()])
        original = copy.deepcopy(comparison)
        result = build_review(comparison)
        self.assertEqual(len(result['records']), 2)
        self.assertEqual(result['groups'][0]['case_indices'], [0, 1])
        self.assertEqual(result['groups'][0]['count'], 2)
        self.assertEqual(result['records'][0]['candidates'][0]['ledger_index'], 0)
        self.assertIn('shared_candidate', result['records'][0]['issues'])
        self.assertFalse(result['ready'])
        self.assertIsNone(result['human_question_count'])
        self.assertEqual(comparison, original)

    def test_historical_fields_are_evidence_and_never_approved_values(self):
        ledger = [entry(category_path=['Транспорт', 'Такси'], tag='Свидание',
                        description='Такси', notes='На концерт')]
        result = build_review(self.comparison([operation()], ledger))
        candidate = result['records'][0]['candidates'][0]
        self.assertEqual(candidate['category_path'], ['Транспорт', 'Такси'])
        self.assertEqual(candidate['tag'], 'Свидание')
        self.assertEqual(candidate['notes'], 'На концерт')
        self.assertEqual(result['records'][0]['assessment'], 'candidate_requires_review')

    def test_no_candidate_is_reported_as_unresolved_not_missing(self):
        result = build_review(self.comparison([operation()], []))
        self.assertEqual(result['records'][0]['assessment'], 'no_candidate')
        self.assertEqual(result['records'][0]['source']['source_id'], 'bank-1')
        self.assertEqual(result['groups'][0]['first_timestamp'], operation()['timestamp'])

    def test_accounts_currencies_statuses_and_directions_do_not_mix(self):
        sources = [operation(), operation('two', amount='100'),
                   operation('three', currency='EUR'), operation('four', account='Other'),
                   operation('five', status='failed')]
        result = build_review(self.comparison(sources, []))
        self.assertEqual(len(result['groups']), 5)
        self.assertEqual(sum(g['count'] for g in result['groups']), 5)

    def test_rejected_input_and_unreferenced_app_records_remain_visible(self):
        comparison = self.comparison([operation()], [entry(amount='-999')])
        comparison['input_count'] = 2
        comparison['rejected'] = [{'locator': 2, 'reason': 'bad_amount'}]
        result = build_review(comparison)
        self.assertEqual(result['rejected'], comparison['rejected'])
        self.assertEqual(result['unreferenced_ledger'][0]['amount'], '-999')
        self.assertEqual(result['input_count'], len(result['records']) + len(result['rejected']))

    def test_broken_input_count_and_candidate_references_are_rejected(self):
        comparison = self.comparison([operation()], [entry()])
        for change in ('count', 'reference'):
            broken = copy.deepcopy(comparison)
            if change == 'count':
                broken['input_count'] += 1
            else:
                broken['records'][0]['ledger_indices'] = [999]
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_review(broken)

    def test_confirmed_alias_points_to_canonical_case_without_dropping_either(self):
        from adapters.source_resolutions import apply_resolutions
        from test_source_resolutions import snapshot, resolution
        canonical, alias = operation('canonical'), operation('alias')
        source = apply_resolutions(snapshot(canonical, alias), [resolution(alias,
            same_operation_as={'source': 'yabank', 'id': 'canonical'})])
        result = build_review(self.comparison(source['records'], []))
        self.assertEqual(len(result['records']), 2)
        self.assertEqual(result['records'][1]['same_operation_case_index'], 0)
        self.assertEqual(result['records'][1]['assessment'], 'confirmed_source_alias')
        self.assertEqual(result['records'][1]['source_match_assessment'], 'no_candidate')

    def test_old_pending_records_are_reported_without_new_source_operations(self):
        ledger = [entry('old', timestamp='2026-01-01T10:00:00+03:00',
                        cleared=False, status='pending', kind='expense',
                        markers=[marker('old-bank-id')]), entry('completed')]
        comparison = self.comparison([], ledger)
        original = copy.deepcopy(comparison)
        result = build_review(comparison)
        self.assertEqual(result['ledger_input_count'], 2)
        self.assertEqual([r['ledger_index'] for r in result['pending_rechecks']], [0])
        self.assertEqual(result['pending_rechecks'][0]['recheck_sources'],
                         [marker('old-bank-id')])
        self.assertEqual(result['pending_rechecks'][0]['timestamp'], ledger[0]['timestamp'])
        self.assertEqual(comparison, original)

    def test_matched_pending_expenses_incomes_and_transfers_from_all_sources_are_reported(self):
        ledger = [entry(str(i), cleared=False, status='pending', kind=kind,
                        markers=[marker('bank-1', source=source)])
                  for i, (kind, source) in enumerate(
                      [('expense', 'yabank'), ('income', 'other-bank'), ('transfer', 'cash')])]
        result = build_review(self.comparison([operation()], ledger))
        self.assertEqual([r['ledger_index'] for r in result['pending_rechecks']], [0, 1, 2])
        self.assertEqual([r['recheck_sources'][0]['source'] for r in result['pending_rechecks']],
                         ['yabank', 'other-bank', 'cash'])
        self.assertIn(0, [c['ledger_index'] for c in result['records'][0]['candidates']])

    def test_pending_records_without_markers_are_reported_for_source_clarification(self):
        result = build_review(self.comparison([], [entry(cleared=False, status='pending')]))
        row = result['pending_rechecks'][0]
        self.assertEqual(row['recheck_sources'], [])
        self.assertIn('missing_source_identity', row['recheck_issues'])
        self.assertEqual(row['source'], 'debit-credit')
        self.assertFalse(result['ready'])

    def test_cashback_aliases_and_duplicate_pending_records_are_not_collapsed(self):
        identities = [marker('purchase', role='cashback'), marker('accrual')]
        ledger = [entry(str(i), cleared=False, status='pending', kind='income',
                        eligible_for_matching=False, markers=identities) for i in range(2)]
        result = build_review(self.comparison([], ledger))
        self.assertEqual(len(result['pending_rechecks']), 2)
        for row in result['pending_rechecks']:
            self.assertEqual(row['recheck_sources'], identities)
            self.assertIn('ledger_requires_review', row['recheck_issues'])


if __name__ == '__main__':
    unittest.main()
