"""Проверка фактического результата добавления по двум снимкам приложения."""

import copy
import unittest

from adapters.import_verification import verify_additions


def leg(identifier, account, other, amount, source_id, linked_id):
    return {'source_id': identifier, 'timestamp': '2026-07-08T09:08:12+03:00',
            'timestamp_precision': 'second', 'amount': amount, 'currency': 'RUB',
            'account': account, 'other_account': other, 'kind': 'transfer_leg',
            'cleared': True, 'description': '', 'category': None, 'tag': None, 'payee': '',
            'notes': '[dc-sync:v1 source=yabank id=' + source_id + ']',
            'markers': [{'source': 'yabank', 'id': source_id, 'role': None}],
            'linked_id': linked_id}


def account(name, balance):
    return {'id': name, 'name': name, 'currency': 'RUB',
            'balance': balance, 'uncleared_balance': balance}


def data():
    expected = [leg('a', 'Баллы', 'Карта', '-10', 'out', 'b'),
                leg('b', 'Карта', 'Баллы', '10', 'in', 'a')]
    before = {'records': [], 'rejected': [], 'issues': [], 'accounts': [account('Баллы', '100'), account('Карта', '20')]}
    after = {**copy.deepcopy(before), 'records': copy.deepcopy(expected),
             'accounts': [account('Баллы', '90'), account('Карта', '30')]}
    return before, after, expected


class ImportVerificationTest(unittest.TestCase):
    def test_complete_linked_transfer_with_correct_balances_passes(self):
        self.assertTrue(verify_additions(*data())['ready'])

    def test_unlinked_income_and_expense_do_not_pass_as_transfer(self):
        before, after, expected = data()
        after['records'][0].update(kind='expense', linked_id=None)
        after['records'][1].update(kind='income', linked_id=None)
        report = verify_additions(before, after, expected)
        self.assertFalse(report['ready'])
        self.assertIn('transfer_link_missing', {i['code'] for i in report['issues']})

    def test_duplicate_marker_and_missing_marker_are_detected(self):
        before, after, expected = data()
        after['records'][1]['markers'] = copy.deepcopy(after['records'][0]['markers'])
        report = verify_additions(before, after, expected)
        self.assertFalse(report['ready'])
        self.assertEqual({r['match_count'] for r in report['records']}, {0, 2})

    def test_wrong_amount_notes_and_cleared_flag_are_detected(self):
        before, after, expected = data()
        after['records'][0].update(amount='-11', notes='changed', cleared=False)
        fields = verify_additions(before, after, expected)['records'][0]['different_fields']
        self.assertEqual(set(fields), {'amount', 'notes', 'cleared'})

    def test_wrong_cached_balance_fails_even_when_rows_are_correct(self):
        before, after, expected = data()
        after['accounts'][0]['balance'] = '100'
        self.assertIn('account_balance_delta_differs',
                      {i['code'] for i in verify_additions(before, after, expected)['issues']})

    def test_changes_to_existing_records_and_unexpected_new_records_are_visible(self):
        before, after, expected = data()
        old = leg('old', 'Баллы', 'Карта', '-5', 'older', 'other')
        before['records'] = [copy.deepcopy(old)]
        after['records'].append({**old, 'notes': 'changed'})
        after['records'].append({**copy.deepcopy(old), 'source_id': 'unexpected'})
        report = verify_additions(before, after, expected)
        self.assertEqual(report['changed_existing_ids'], ['old'])
        self.assertEqual(report['unexpected_new_ids'], ['unexpected'])
        self.assertFalse(report['ready'])

    def test_reusing_existing_app_id_is_not_reported_as_a_new_import(self):
        before, after, expected = data()
        before['records'] = copy.deepcopy(after['records'])
        self.assertIn('expected_record_not_new',
                      {i['code'] for i in verify_additions(before, after, expected)['issues']})

    def test_reader_errors_prevent_success_and_inputs_are_not_mutated(self):
        values = data()
        values[1]['rejected'] = [{'reason': 'unsupported row'}]
        before = copy.deepcopy(values)
        report = verify_additions(*values)
        self.assertFalse(report['ready'])
        self.assertEqual(values, before)


if __name__ == '__main__':
    unittest.main()
