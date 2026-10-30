"""В пачку попадают только взаимно однозначные новые переводы с обеими сторонами."""

import copy
import unittest
from datetime import datetime

from adapters.transfer_plan import build_transfer_plan
from adapters.backup_additions import validate_additions
from test_matching import operation, entry, marker
from test_source_resolutions import snapshot

START = datetime.fromisoformat('2026-07-01T00:00:00+03:00')
END = datetime.fromisoformat('2026-08-01T00:00:00+03:00')
MAPPING = {'yabank': {name: {'account': name, 'source_currency': 'RUB', 'currency': 'RUB'}
                      for name in ('Card', 'Save')}}


def leg(identifier, outgoing=True, **changes):
    return operation(identifier, **{'account': 'Card' if outgoing else 'Save',
        'other_account': 'Save' if outgoing else 'Card', 'kind': 'transfer_leg',
        'amount': '-100' if outgoing else '100', 'issues': [], **changes})


def plan(source_rows, ledger_rows=(), accounts=('Card', 'Save')):
    source = {**snapshot(*source_rows), 'coverage': 'snapshot_complete'}
    ledger = {**snapshot(*ledger_rows), 'coverage': 'backup_snapshot', 'date_filter': None,
              'accounts': [{'name': name, 'currency': 'RUB', 'deleted_locally': None}
                           for name in accounts]}
    return build_transfer_plan(source, ledger, MAPPING, START, END,
                               pair_window_seconds=300, ledger_window_seconds=129600)


class TransferPlanTest(unittest.TestCase):
    def test_new_pair_has_reciprocal_plan_keys_and_own_source_markers(self):
        result = plan([leg('out'), leg('in', False)])
        self.assertEqual(result['selected_pair_count'], 1)
        rows = result['additions']
        self.assertEqual(rows[0]['peer_key'], rows[1]['key'])
        self.assertIn('id=out]', rows[0]['notes'])
        self.assertIn('id=in]', rows[1]['notes'])
        self.assertTrue(all(row['description'] for row in rows))
        validate_additions(rows, [])

    def test_repeated_identical_transfers_are_deferred_instead_of_paired_arbitrarily(self):
        result = plan([leg('a'), leg('b'), leg('c', False), leg('d', False)])
        self.assertEqual(result['selected_pair_count'], 0)
        self.assertEqual([x['state'] for x in result['records']], ['ambiguous_pair'] * 4)

    def test_existing_markers_prevent_reimport_even_outside_time_window(self):
        existing = entry(markers=[marker('out')], notes='[dc-sync:v1 source=yabank id=out]',
                         timestamp='2026-01-01T00:00:00+03:00')
        result = plan([leg('out'), leg('in', False)], [existing])
        self.assertEqual(result['selected_pair_count'], 0)
        self.assertIn('already_marked', {x['state'] for x in result['records']})

    def exchange_record(self):
        return entry(account='Save', kind='transfer_leg', notes='', amount='100',
                     markers=[marker('exchange')], timestamp='2026-07-04T10:01:00+03:00')

    def test_record_marked_for_another_operation_of_this_snapshot_does_not_block_a_transfer(self):
        other = operation('exchange', account='Save', amount='100', kind=None, issues=[],
                          timestamp='2026-07-04T10:01:00+03:00')
        result = plan([leg('out'), leg('in', False), other], [self.exchange_record()])
        self.assertEqual(result['selected_pair_count'], 1)

    def test_record_marked_for_an_operation_missing_from_the_snapshot_still_defers_a_transfer(self):
        result = plan([leg('out'), leg('in', False)], [self.exchange_record()])
        self.assertEqual(result['selected_pair_count'], 0)
        self.assertTrue(all(x['state'] == 'existing_candidate' for x in result['records']))

    def test_nearby_expense_with_same_amount_defers_transfer_for_review(self):
        existing = entry(account='Card', kind='expense', notes='',
                         timestamp='2026-07-04T20:00:00+03:00')
        result = plan([leg('out'), leg('in', False)], [existing])
        self.assertEqual(result['selected_pair_count'], 0)
        self.assertTrue(all(x['state'] == 'existing_candidate' for x in result['records']))

    def test_missing_account_and_missing_peer_remain_visible(self):
        absent = plan([leg('out'), leg('in', False)], accounts=('Card',))
        self.assertTrue(all(x['state'] == 'account_absent_or_ambiguous' for x in absent['records']))
        unpaired = plan([leg('out')])
        self.assertEqual(unpaired['records'][0]['state'], 'unpaired')

    def test_nontransfer_pending_and_outside_period_are_excluded_explicitly(self):
        result = plan([leg('ordinary', kind='expense'), leg('pending', status='pending'),
                       leg('outside', timestamp=END.isoformat())])
        self.assertEqual([x['state'] for x in result['records']],
                         ['not_transfer', 'requires_review', 'outside_period'])

    def test_error_is_reported_for_incomplete_source_or_filtered_app_snapshot(self):
        source = snapshot(leg('out'), leg('in', False))
        ledger = {**snapshot(), 'coverage': 'backup_snapshot', 'accounts': []}
        with self.assertRaises(ValueError):
            build_transfer_plan(source, ledger, MAPPING, START, END,
                                pair_window_seconds=300, ledger_window_seconds=129600)

    def test_bank_comments_are_preserved_and_source_records_are_not_mutated(self):
        source_rows = [leg('out', raw={'detail': {'comment': 'На ремонт'}}), leg('in', False)]
        saved = copy.deepcopy(source_rows)
        result = plan(source_rows)
        self.assertIn('На ремонт', result['additions'][0]['notes'])
        self.assertEqual(source_rows, saved)
