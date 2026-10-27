"""Движение сравнивается отдельно от неподтверждённых остатков."""

import copy
import unittest
from datetime import datetime

from adapters.movement_control import compare_movements
from test_matching import MAPPING, operation, entry
from test_source_resolutions import snapshot

START = datetime.fromisoformat('2026-07-01T00:00:00+03:00')
END = datetime.fromisoformat('2026-08-01T00:00:00+03:00')


def control(source, ledger, mapping=MAPPING):
    return compare_movements(snapshot(*source), snapshot(*ledger), mapping, START, END)


class MovementControlTest(unittest.TestCase):
    def test_equal_net_flow_is_reported_without_claiming_balances_are_reconciled(self):
        result = control([operation()], [entry()])
        account = result['records'][0]
        self.assertEqual(account['completed_difference'], '0')
        self.assertEqual(account['source']['completed']['debit'], '-100')
        self.assertIsNone(account['opening_balance'])
        self.assertFalse(result['ready'])

    def test_repeated_payments_keep_their_count_and_amount(self):
        result = control([operation('one'), operation('two')], [entry()])
        account = result['records'][0]
        self.assertEqual(account['source']['completed']['count'], 2)
        self.assertEqual(account['completed_difference'], '-100')

    def test_pending_flow_is_separate_from_completed_flow(self):
        result = control([operation(cleared=False, status='pending')], [entry()])
        account = result['records'][0]
        self.assertEqual(account['source']['pending']['debit'], '-100')
        self.assertEqual(account['completed_difference'], '100')
        self.assertEqual(account['including_pending_difference'], '0')

    def test_aliases_failed_operations_and_outside_dates_remain_accounted_for(self):
        rows = [operation('alias', resolution={'same_operation_as': {'source': 'yabank', 'id': 'main'}}),
                operation('failed', status='failed'), operation('outside', timestamp=END.isoformat())]
        result = control(rows, [])
        self.assertEqual([x['state'] for x in result['source_dispositions']],
                         ['confirmed_alias', 'nonfinancial_status', 'outside_period'])

    def test_accounts_merged_in_app_do_not_count_internal_transfers(self):
        mapping = {'yabank': {name: {'account': 'Сейв', 'source_currency': 'RUB', 'currency': 'RUB'}
                             for name in ('A', 'B')}}
        result = control([operation(account='A', other_account='B', kind='transfer_leg')], [], mapping)
        self.assertEqual(result['source_dispositions'][0]['state'], 'internal_same_account')
        self.assertEqual(result['records'][0]['source']['completed']['count'], 0)

    def test_unmapped_rows_and_other_app_accounts_are_shown_separately(self):
        result = control([operation(account='Unknown')], [entry(account='Наличные')])
        self.assertEqual(result['source_dispositions'][0]['state'], 'unresolved')
        self.assertEqual(result['ledger_dispositions'][0]['state'], 'outside_source_scope')
        self.assertEqual(result['uncovered_ledger_accounts'], [{'account': 'Наличные', 'currency': 'RUB'}])

    def test_missing_source_coverage_is_not_equated_with_zero_bank_balance(self):
        result = control([], [entry()])
        account = result['records'][0]
        self.assertEqual(account['source']['completed']['net'], '0')
        self.assertFalse(account['source_coverage_verified'])
        self.assertIsNone(account['closing_balance'])

    def test_unknown_completion_is_unresolved_and_input_is_not_mutated(self):
        source, ledger = snapshot(operation(cleared=None, status='unknown')), snapshot(entry())
        original = copy.deepcopy((source, ledger, MAPPING))
        result = compare_movements(source, ledger, MAPPING, START, END)
        self.assertEqual(result['source_dispositions'][0]['state'], 'unresolved')
        self.assertEqual((source, ledger, MAPPING), original)

    def test_error_is_reported_for_naive_period_or_unaccounted_input(self):
        for start, source in [(START.replace(tzinfo=None), snapshot()),
                              (START, {**snapshot(), 'input_count': 1})]:
            with self.assertRaises(ValueError):
                compare_movements(source, snapshot(), MAPPING, start, END)
