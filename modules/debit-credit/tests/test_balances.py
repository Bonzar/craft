"""Остатки счетов, прочитанные сборщиком до и после истории."""

import unittest

from adapters.balances import read_balances


def account(identifier, amount, name='Счёт'):
    return {'account': identifier, 'account_name': name, 'amount': amount, 'currency': 'RUB'}


def observation(accounts, at='2026-10-29T10:00:00.000Z', error=None):
    return {'observed_at': at, 'accounts': accounts, 'error': error}


def balances(before, after, changed=False):
    return {'balances': {'before': before, 'after': after, 'changed': changed}}


class ReadBalancesTest(unittest.TestCase):
    def test_unchanged_balances_are_kept_per_account_at_the_second_reading(self):
        rows = [account('a', '10.50'), account('b', '0.00', None)]
        value, issues = read_balances(balances(observation(rows), observation(rows, '2026-10-29T10:05:00Z')))
        self.assertEqual(issues, [])
        self.assertEqual(value['observed_at'], '2026-10-29T10:05:00Z')
        self.assertEqual(value['accounts'], [
            {'account': 'a', 'account_name': 'Счёт', 'amount': '10.50', 'currency': 'RUB'},
            {'account': 'b', 'account_name': None, 'amount': '0.00', 'currency': 'RUB'}])

    def test_snapshot_without_balances_has_no_balances_and_no_issue(self):
        self.assertEqual(read_balances({}), (None, []))

    def test_balance_that_moved_during_reading_is_an_issue(self):
        value, issues = read_balances(balances(observation([account('a', '10.00')]),
                                               observation([account('a', '9.00')]), changed=True))
        self.assertEqual(issues, [{'code': 'balance_changed_during_read'}])
        self.assertEqual(value['accounts'][0]['amount'], '9.00')

    def test_changed_flag_is_checked_against_the_amounts(self):
        _, issues = read_balances(balances(observation([account('a', '10.00')]),
                                           observation([account('a', '9.00')]), changed=False))
        self.assertEqual(issues, [{'code': 'balance_changed_during_read'}])

    def test_unread_balances_are_an_issue_with_the_bank_error(self):
        value, issues = read_balances(balances(observation(None, error='offline'),
                                               observation([account('a', '1.00')]), changed=None))
        self.assertIsNone(value['accounts'])
        self.assertEqual(value['errors'], ['offline'])
        self.assertEqual(issues, [{'code': 'balances_unread'}])

    def test_malformed_balances_require_review(self):
        cases = [{'balances': []}, balances(observation([account('a', '1,00')]),
                                            observation([account('a', '1,00')])),
                 balances(observation([account('a', '1.00'), account('a', '1.00')]),
                          observation([account('a', '1.00'), account('a', '1.00')]))]
        for case in cases:
            with self.subTest(case=case):
                value, issues = read_balances(case)
                self.assertIsNone(value['accounts'])
                self.assertEqual(issues, [{'code': 'balances_require_review'}])


if __name__ == '__main__':
    unittest.main()
