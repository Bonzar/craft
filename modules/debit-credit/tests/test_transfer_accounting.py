"""Один подтверждённый перевод разворачивается в две связанные стороны без потерь."""

import copy
import unittest

from adapters.transfer_accounting import expand_transfers
from adapters.matching import compare_records
from adapters.markers import extract_markers
from adapters.transfer_plan import build_transfer_plan
from test_matching import operation, entry
from test_source_resolutions import snapshot
from test_transfer_plan import START, END, MAPPING


def transfer(**changes):
    return operation('topup', account='Card', other_account='Save', kind='transfer',
                     amount='-650', issues=[], **changes)


def source(*rows):
    return {**snapshot(*rows), 'coverage': 'snapshot_complete'}


def ledger(*rows):
    return {**snapshot(*rows), 'coverage': 'backup_snapshot', 'date_filter': None,
            'accounts': [{'name': name, 'currency': 'RUB'} for name in ('Card', 'Save')]}


class TransferAccountingTest(unittest.TestCase):
    def test_one_source_payment_becomes_one_native_transfer_and_reimport_is_skipped(self):
        original = source(transfer())
        saved = copy.deepcopy(original)
        expected = expand_transfers(original, MAPPING)
        self.assertEqual(original, saved)
        self.assertEqual(expected['transfer_source_input_count'], 1)
        self.assertEqual(expected['input_count'], 2)
        self.assertEqual(expected['transfer_origins'], [{'input_index': 0, 'record_indices': [0, 1]}])
        self.assertEqual([r['kind'] for r in expected['records']], ['transfer_leg'] * 2)
        prepared = build_transfer_plan(expected, ledger(), MAPPING, START, END,
                                      pair_window_seconds=0, ledger_window_seconds=120)
        self.assertEqual(prepared['selected_pair_count'], 1)
        self.assertEqual({(r['account'], r['amount']) for r in prepared['additions']},
                         {('Card', '-650'), ('Save', '650')})
        actual = [entry(str(i), **{k: r[k] for k in
                  ('account', 'amount', 'currency', 'timestamp', 'kind', 'cleared', 'notes')},
                  other_account=prepared['additions'][1-i]['account'],
                  markers=[m.as_dict() for m in extract_markers(r['notes'])])
                  for i, r in enumerate(prepared['additions'])]
        checked = compare_records(expected['records'], actual, MAPPING, window_seconds=0)
        self.assertTrue(all(r['match'] == 'marker' and not r['differences'][r['ledger_indices'][0]]
                            for r in checked['records']))
        repeated = build_transfer_plan(expected, ledger(*actual), MAPPING, START, END,
                                       pair_window_seconds=0, ledger_window_seconds=120)
        self.assertEqual(repeated['additions'], [])
        self.assertEqual(repeated['state_counts'], {'already_marked': 2})

    def test_incoming_transfer_derives_an_outgoing_counterpart(self):
        row = {**transfer(), 'amount': '650'}
        result = expand_transfers(source(row), MAPPING)
        peer = result['records'][1]
        self.assertEqual((peer['account'], peer['other_account'], peer['amount']),
                         ('Save', 'Card', '-650'))
        self.assertEqual(peer['source_id'], row['source_id'])
        self.assertIsNone(peer.get('native_id'))

    def test_counterpart_amount_is_not_rounded_by_decimal_context(self):
        exact = '12345678901234567890123456789.12'
        result = expand_transfers(source({**transfer(), 'amount': '-' + exact}), MAPPING)
        self.assertEqual(result['records'][1]['amount'], exact)

    def test_pending_transfer_keeps_both_sides_pending(self):
        result = expand_transfers(source({**transfer(), 'status': 'pending', 'cleared': False}), MAPPING)
        self.assertEqual([r['cleared'] for r in result['records']], [False, False])
        self.assertEqual([r['status'] for r in result['records']], ['pending', 'pending'])

    def test_derived_counterpart_does_not_claim_the_source_bank_comment_as_its_own(self):
        result = expand_transfers(source(transfer(bank_comment='Назначение исходного перевода')), MAPPING)
        self.assertEqual(result['records'][0]['bank_comment'], 'Назначение исходного перевода')
        self.assertIsNone(result['records'][1].get('bank_comment'))

    def test_existing_legs_and_nontransfers_are_preserved_without_new_rows(self):
        rows = [{**transfer(), 'kind': 'transfer_leg'}, operation('purchase', kind='expense')]
        result = expand_transfers(source(*rows), MAPPING)
        self.assertEqual(result['records'], rows)
        self.assertEqual(result['transfer_derived_count'], 0)

    def test_unresolved_transfer_is_preserved_with_a_reason(self):
        for changes in [{'other_account': None}, {'source_id': None}, {'cleared': None},
                        {'status': 'failed'}, {'issues': ['unknown_account']}, {'role': 'custom'}]:
            with self.subTest(changes=changes):
                row = {**transfer(), **changes}
                result = expand_transfers(source(row), MAPPING)
                self.assertEqual(len(result['records']), 1)
                self.assertEqual(result['records'][0]['amount'], '-650')
                self.assertTrue(result['records'][0]['issues'])
                self.assertFalse(result['ready'])

    def test_another_currency_is_not_assumed_to_exchange_one_to_one(self):
        mapping = copy.deepcopy(MAPPING)
        mapping['yabank']['Save']['source_currency'] = 'EUR'
        mapping['yabank']['Save']['currency'] = 'EUR'
        result = expand_transfers(source(transfer()), mapping)
        self.assertEqual(result['transfer_derived_count'], 0)
        self.assertIn('transfer_currency_requires_review', result['records'][0]['issues'])

    def test_rejected_rows_and_duplicate_source_payments_are_not_lost(self):
        original = source(transfer(), transfer())
        original['rejected'] = [{'locator': 3, 'reason': 'Invalid input', 'raw': {}}]
        original['input_count'] = 3
        result = expand_transfers(original, MAPPING)
        self.assertEqual(result['input_count'], 5)
        self.assertEqual(result['rejected'], original['rejected'])
        self.assertEqual(len(result['records']), 4)
        self.assertEqual(sorted(i for o in result['transfer_origins'] for i in o['record_indices']),
                         list(range(4)))

    def test_a_second_expansion_is_rejected(self):
        expected = expand_transfers(source(transfer()), MAPPING)
        with self.assertRaises(ValueError):
            expand_transfers(expected, MAPPING)

    def test_cashback_provenance_is_preserved_when_a_transfer_counterpart_is_added(self):
        from adapters.yandex_accounting import expand_accounting
        raw = source(transfer(), operation('buy', account='Card', cashback_hint='10', issues=[]))
        cashback = expand_accounting(raw, parent_window_seconds=120)
        result = expand_transfers(cashback, MAPPING)
        self.assertEqual(result['source_input_count'], 2)
        self.assertEqual(result['derived_count'], 2)
        self.assertEqual(result['input_count'], 4)
        self.assertEqual(result['origins'], [
            {'input_index': 0, 'record_indices': [0, 3]},
            {'input_index': 1, 'record_indices': [1, 2]}])

    def test_forged_source_counts_are_rejected(self):
        original = source(transfer())
        for metadata in ({'source_input_count': 0}, {'derived_count': True},
                         {'origins': [{'input_index': -1, 'record_indices': [0]}]}):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                expand_transfers({**original, **metadata}, MAPPING)
