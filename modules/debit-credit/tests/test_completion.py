"""Подтверждение прежних записей по точным меткам из любого источника."""

import copy
import unittest

from adapters import completion
from adapters.matching import compare_records
from test_matching import entry, marker, operation


def comparison(kind='expense', source='other-bank', **changes):
    amount = '18' if kind == 'income' else '-18'
    record = operation('payment', **{'source': source, 'kind': kind, 'amount': amount, **changes})
    actual = entry(kind=kind, amount='20' if kind == 'income' else '-20',
                   cleared=False, status='pending', markers=[marker('payment', source=source)],
                   splits=[], linked_locator=None)
    mapping = {source: {'Карта': {'account': 'Карта Пэй',
                                 'source_currency': 'RUB', 'currency': 'RUB'}}}
    return compare_records([record], [actual], mapping, window_seconds=120)


def transfer_comparison():
    source = [operation('out', kind='transfer_leg', other_account='Запас', amount='-18'),
              operation('in', kind='transfer_leg', account='Запас', other_account='Карта', amount='18')]
    ledger = [entry('out', kind='transfer_leg', amount='-20', other_account='Запас',
                    cleared=False, status='pending', linked_locator=2, markers=[marker('out')], splits=[]),
              entry('in', locator=2, kind='transfer_leg', amount='20', account='Запас',
                    other_account='Карта Пэй', cleared=False, status='pending',
                    linked_locator=1, markers=[marker('in')], splits=[])]
    mapping = {'yabank': {name: {'account': target, 'currency': 'RUB', 'source_currency': 'RUB'}
                         for name, target in [('Карта', 'Карта Пэй'), ('Запас', 'Запас')]}}
    return compare_records(source, ledger, mapping, window_seconds=120)


class CompletionTest(unittest.TestCase):
    def test_pending_expense_and_income_are_confirmed_on_the_same_records(self):
        for kind in ('expense', 'income'):
            value = comparison(kind)
            original = copy.deepcopy(value)
            result = completion.plan_confirmations(value)
            self.assertEqual(result['updates'], [{'locator': 1, 'set': {
                'amount': '18' if kind == 'income' else '-18', 'cleared': True}}])
            self.assertEqual(result['decisions'], [{'input_index': 0, 'action': 'confirm_existing'}])
            self.assertFalse(result['ready'])
            self.assertEqual(value, original)

    def test_still_preliminary_amount_changes_leave_the_existing_record_untouched(self):
        result = completion.plan_confirmations(comparison(status='pending', cleared=False))
        self.assertEqual(result['updates'], [])
        self.assertEqual(result['decisions'][0]['action'], 'keep_preliminary')

    def test_nonidentity_matches_and_other_field_changes_require_review(self):
        for field, value in [('currency', 'USD'),
                             ('account', 'Неизвестный'), ('other_account', 'Неизвестный'),
                             ('timestamp', '2026-07-05T10:00:00+03:00')]:
            result = completion.plan_confirmations(comparison(**{field: value}))
            self.assertEqual(result['updates'], [], field)
        value = comparison()
        value['records'][0]['match'] = 'candidate'
        self.assertEqual(completion.plan_confirmations(value)['updates'], [])

    def test_unknown_failed_canceled_and_sign_changed_operations_require_review(self):
        for changes in ({'status': 'unknown'}, {'status': 'failed'}, {'status': 'canceled'},
                        {'amount': '18'}, {'amount': '0'}, {'cleared': None}):
            self.assertEqual(completion.plan_confirmations(comparison(**changes))['updates'], [])

    def test_missing_source_kind_can_confirm_only_an_ordinary_operation(self):
        value = comparison()
        value['records'][0]['record']['kind'] = None
        self.assertEqual(len(completion.plan_confirmations(value)['updates']), 1)

    def test_reimport_after_confirmation_proposes_no_second_update(self):
        value = comparison()
        actual = value['ledger_records'][0]
        actual.update(amount='-18', cleared=True, status='completed')
        value['records'][0]['differences'] = {0: {}}
        result = completion.plan_confirmations(value)
        self.assertEqual(result['updates'], [])
        self.assertEqual(result['decisions'][0]['action'], 'already_confirmed')

    def test_both_native_transfer_legs_are_confirmed_together(self):
        result = completion.plan_confirmations(transfer_comparison())
        self.assertEqual(result['updates'], [
            {'locator': 1, 'set': {'amount': '-18', 'cleared': True}},
            {'locator': 2, 'set': {'amount': '18', 'cleared': True}}])

    def test_missing_pending_unlinked_or_unequal_transfer_peer_blocks_both_legs(self):
        for defect in ('pending', 'missing', 'unlinked', 'amount'):
            value = transfer_comparison()
            if defect == 'pending':
                value['records'][1]['record'].update(status='pending', cleared=False)
            elif defect == 'missing':
                value['records'].pop()
                value['input_count'] = 1
                value['unreferenced_ledger'] = [1]
            elif defect == 'unlinked':
                value['ledger_records'][1]['linked_locator'] = None
            else:
                value['records'][1]['record']['amount'] = '19'
            self.assertEqual(completion.plan_confirmations(value)['updates'], [], defect)

    def test_split_and_shared_source_candidates_require_review(self):
        value = comparison()
        value['ledger_records'][0]['splits'] = [{'amount': '-20'}]
        self.assertEqual(completion.plan_confirmations(value)['updates'], [])
        value = comparison()
        value['records'][0]['issues'].append('shared_candidate')
        self.assertEqual(completion.plan_confirmations(value)['updates'], [])
