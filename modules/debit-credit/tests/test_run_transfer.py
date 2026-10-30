"""Перенос одной командой: что идёт в копию, что на разбор, сходятся ли остатки."""

import unittest
from datetime import datetime

from adapters.run_transfer import check_balances, plan_transfer
from test_matching import marker, operation

START = datetime.fromisoformat('2026-07-01T00:00:00+03:00')
END = datetime.fromisoformat('2026-08-01T00:00:00+03:00')
WINDOWS = {'window_seconds': 120, 'pair_window_seconds': 300, 'ledger_window_seconds': 129600,
           'parent_window_seconds': 300}


def bank(identifier, **changes):
    return operation(identifier, **{'source': 'tbank', 'account': '1', 'description': 'Самокат',
                                    'kind': None, 'issues': [], **changes})


def source(*records, balances=None):
    value = {'schema_version': 1, 'adapter': 'tbank-api', 'source': 'tbank', 'input_count': len(records),
             'records': list(records), 'rejected': [], 'issues': [], 'coverage': 'snapshot_complete',
             'period': {'fromInclusive': START.isoformat(), 'toExclusive': END.isoformat()},
             'ready': True, 'balances': balances}
    return value


def app_account(name, balance='0', uncleared=None):
    return {'name': name, 'currency': 'RUB', 'deleted_locally': None, 'balance': balance,
            'uncleared_balance': balance if uncleared is None else uncleared}


def ledger(*records, accounts=None):
    return {'schema_version': 1, 'adapter': 'debit-credit-backup', 'input_count': len(records),
            'records': list(records), 'rejected': [], 'issues': [], 'coverage': 'backup_snapshot',
            'date_filter': None, 'ready': True,
            'accounts': accounts or [app_account('Карта Тинь'), app_account('Тинь (кред)')]}


def entry(identifier, **changes):
    return operation(identifier, **{'source': 'debit-credit', 'account': 'Карта Тинь',
                                    'eligible_for_matching': True, 'notes': '', **changes})


def config(**changes):
    value = {'accounts': {'tbank': {
                 '1': {'account': 'Карта Тинь', 'unit': 'RUB', 'balance_id': '1'},
                 '2': {'account': 'Тинь (кред)', 'unit': 'RUB', 'balance_id': '2'}}},
             'not_transferred': set(), 'control_points': {},
             'rules': {('tbank', 'самокат'): {'category_path': ['Транспорт', 'Самокат'], 'tag': None}},
             'decisions': {}}
    value.update(changes)
    return value


def run(sources, book=None, settings=None):
    return plan_transfer(sources, book or ledger(), settings or config(), START, END, **WINDOWS)


def reasons(result):
    return {row['source_id']: row['reason'] for row in result['review']}


class PurchaseTest(unittest.TestCase):
    def test_purchase_with_an_approved_rule_goes_to_the_copy_with_its_category(self):
        result = run([source(bank('p1'))])
        self.assertEqual(result['review'], [])
        [row] = result['additions']
        self.assertEqual((row['kind'], row['account'], row['amount'], row['category_path']),
                         ('expense', 'Карта Тинь', '-100', ['Транспорт', 'Самокат']))
        self.assertIn('[dc-sync:v1 source=tbank id=p1]', row['notes'])
        self.assertEqual(result['outcomes']['p1'], 'added_by_rule')

    def test_purchase_without_a_rule_goes_to_review_and_not_to_the_copy(self):
        result = run([source(bank('p2', description='Лавка'))])
        self.assertEqual(result['additions'], [])
        self.assertEqual(reasons(result), {'p2': 'needs_category'})

    def test_tag_only_rule_still_needs_a_category(self):
        settings = config(rules={('tbank', 'самокат'): {'category_path': [], 'tag': 'Поездка'}})
        result = run([source(bank('p3'))], settings=settings)
        self.assertEqual(result['additions'], [])
        self.assertEqual(result['review'][0]['suggested_tag'], 'Поездка')

    def test_rule_tag_is_kept_together_with_a_decision_category(self):
        settings = config(rules={('tbank', 'самокат'): {'category_path': [], 'tag': 'Поездка'}},
                          decisions={('tbank', 'p4'): {'action': 'record', 'category_path': ['Такси'], 'tag': None}})
        [row] = run([source(bank('p4'))], settings=settings)['additions']
        self.assertEqual((row['category_path'], row['tag']), (['Такси'], 'Поездка'))

    def test_decisions_skip_or_confirm_an_existing_record(self):
        settings = config(decisions={('tbank', 's1'): {'action': 'skip', 'category_path': [], 'tag': None},
                                     ('tbank', 's2'): {'action': 'same', 'category_path': [], 'tag': None}})
        book = ledger(entry('old', amount='-50', description='Лавка'))
        result = run([source(bank('s1', description='Лавка'), bank('s2', amount='-50', description='Лавка'))],
                     book, settings)
        self.assertEqual(result['additions'], [])
        self.assertEqual(result['review'], [])
        self.assertEqual((result['outcomes']['s1'], result['outcomes']['s2']), ('skipped', 'same_as_existing'))
        self.assertEqual(result['unreconciled'], [])

    def test_already_marked_operation_is_not_added_again(self):
        book = ledger(entry('old', kind='expense', markers=[marker('p1', source='tbank')]))
        result = run([source(bank('p1'))], book)
        self.assertEqual(result['additions'], [])
        self.assertEqual(result['outcomes']['p1'], 'already_recorded')

    def test_existing_record_without_marker_is_listed_and_its_account_is_not_written(self):
        result = run([source(bank('p5'))], ledger(entry('old')))
        self.assertEqual(result['additions'], [])
        self.assertEqual(result['outcomes']['p5'], 'account_not_reconciled')
        self.assertEqual([r['source_id'] for r in result['unreconciled']], ['old'])


class ScopeTest(unittest.TestCase):
    def test_account_marked_not_transferred_is_left_out_without_review(self):
        settings = config(not_transferred={('tbank', '3')})
        result = run([source(bank('n1', account='3'))], settings=settings)
        self.assertEqual((result['additions'], result['review']), ([], []))
        self.assertEqual(result['outcomes']['n1'], 'not_transferred')

    def test_operation_before_the_control_point_is_left_out(self):
        settings = config(control_points={'Карта Тинь': {'date': '2026-07-10', 'bank_balance': '0',
                                                          'app_balance': '0'}})
        result = run([source(bank('b1'))], settings=settings)
        self.assertEqual(result['outcomes']['b1'], 'before_control_point')
        self.assertEqual(result['additions'], [])

    def test_failed_operation_is_not_recorded(self):
        result = run([source(bank('f1', status='failed', not_recorded_reason='failed_operation'))])
        self.assertEqual(result['outcomes']['f1'], 'not_recorded')
        self.assertEqual(result['review'], [])

    def test_transfer_pair_gets_both_sides_with_keys_unique_across_sources(self):
        legs = [bank('t-out', kind='transfer_leg', other_account='2', description='Перевод'),
                bank('t-in', kind='transfer_leg', account='2', other_account='1', amount='100',
                     description='Перевод')]
        result = run([source(*legs)])
        self.assertEqual(len(result['additions']), 2)
        keys = {row['key'] for row in result['additions']}
        self.assertTrue(all(key.startswith('tbank-') for key in keys))
        self.assertEqual({row['peer_key'] for row in result['additions']}, keys)
        self.assertEqual(result['outcomes']['t-out'], 'transfer_added')

    def test_second_side_of_a_two_sided_bank_transfer_is_neither_written_nor_reviewed(self):
        out = bank('t-out', kind='transfer', other_account='2', description='Между своими счетами')
        alias = bank('t-in', kind='transfer', account='2', other_account='1', amount='100',
                     description='Между своими счетами',
                     resolution={'same_operation_as': {'source': 'tbank', 'id': 't-out'},
                                 'reason': 'tbank_own_transfer_pair'})
        result = run([source(out, alias)])
        self.assertEqual(result['review'], [])
        self.assertEqual(len(result['additions']), 2)
        self.assertEqual(result['outcomes']['t-in'], 'same_operation')


class WritePlanScopeTest(unittest.TestCase):
    def legs(self, **out):
        return [bank('t-out', kind='transfer_leg', other_account='2', description='Перевод', **out),
                bank('t-in', kind='transfer_leg', account='2', other_account='1', amount='100',
                     description='Перевод')]

    def pending(self, **changes):
        return entry('old', kind='expense', amount='-90', cleared=False,
                     markers=[marker('c1', source='tbank')], **changes)

    def test_excluded_transfer_pair_is_not_written(self):
        result = run([source(*self.legs())], settings=config(not_transferred={('tbank', '1'), ('tbank', '2')}))
        self.assertEqual(result['additions'], [])
        self.assertEqual(result['review'], [])

    def test_pair_with_one_leg_before_the_control_point_goes_to_review(self):
        settings = config(control_points={'Карта Тинь': {'date': '2026-07-10', 'bank_balance': '0',
                                                          'app_balance': '0'}})
        result = run([source(*self.legs())], settings=settings)
        self.assertEqual(result['additions'], [])
        self.assertEqual(result['outcomes']['t-out'], 'before_control_point')
        self.assertEqual(reasons(result), {'t-in': 'peer_out_of_scope'})

    def test_confirmation_is_written_only_in_scope(self):
        result = run([source(bank('c1'))], ledger(self.pending()))
        self.assertEqual([u['set'] for u in result['updates']], [{'amount': '-100', 'cleared': True}])
        self.assertEqual(result['outcomes']['c1'], 'confirmed')
        outside = bank('c1', timestamp='2026-06-30T10:00:00+03:00')
        self.assertEqual(run([source(outside)], ledger(self.pending()))['updates'], [])
        excluded = run([source(bank('c1'))], ledger(self.pending()), config(not_transferred={('tbank', '1')}))
        self.assertEqual(excluded['updates'], [])


class MarkedRecordTest(unittest.TestCase):
    def test_marked_record_that_differs_goes_to_review(self):
        book = ledger(entry('old', kind='expense', amount='-90', markers=[marker('p1', source='tbank')]))
        result = run([source(bank('p1'))], book)
        self.assertEqual(result['updates'], [])
        self.assertEqual(reasons(result), {'p1': 'marker_requires_review'})

    def test_marked_record_that_agrees_is_already_recorded(self):
        book = ledger(entry('old', kind='expense', markers=[marker('p1', source='tbank')]))
        result = run([source(bank('p1'))], book)
        self.assertEqual((result['review'], result['outcomes']['p1']), ([], 'already_recorded'))

    def test_already_in_app_decision_without_an_existing_record_goes_to_review(self):
        settings = config(decisions={('tbank', 'p9'): {'action': 'same', 'category_path': [], 'tag': None}})
        result = run([source(bank('p9'))], settings=settings)
        self.assertEqual(result['additions'], [])
        self.assertEqual(reasons(result), {'p9': 'same_without_existing'})


class ReconciledAccountTest(unittest.TestCase):
    point = {'Карта Тинь': {'date': '2026-07-02', 'bank_balance': '0', 'app_balance': '0'}}

    def manual(self, timestamp):
        return entry('manual', amount='-500', description='Лавка', timestamp=timestamp)

    def test_unmarked_record_after_the_control_point_stops_writes_to_its_account(self):
        result = run([source(bank('p1'))], ledger(self.manual('2026-07-03T10:00:00+03:00')),
                     config(control_points=self.point))
        self.assertEqual(result['additions'], [])
        self.assertEqual(result['outcomes']['p1'], 'account_not_reconciled')
        self.assertEqual([(r['account'], r['amount']) for r in result['unreconciled']], [('Карта Тинь', '-500')])

    def test_unmarked_record_before_the_control_point_does_not_stop_writes(self):
        result = run([source(bank('p1'))], ledger(self.manual('2026-07-01T10:00:00+03:00')),
                     config(control_points=self.point))
        self.assertEqual(len(result['additions']), 1)
        self.assertEqual(result['unreconciled'], [])

    def test_without_a_control_point_unmarked_records_of_the_period_stop_writes(self):
        result = run([source(bank('p1'))], ledger(self.manual('2026-07-03T10:00:00+03:00')))
        self.assertEqual(result['additions'], [])
        self.assertEqual(len(result['unreconciled']), 1)

    def test_transfer_is_written_without_searching_for_similar_records(self):
        other = entry('other', account='Тинь (кред)', kind='transfer_leg', amount='100', other_account='Карта Тинь',
                      markers=[marker('elsewhere', source='tbank')])
        legs = [bank('t-out', kind='transfer_leg', other_account='2', description='Перевод'),
                bank('t-in', kind='transfer_leg', account='2', other_account='1', amount='100',
                     description='Перевод')]
        result = run([source(*legs)], ledger(other))
        self.assertEqual(len(result['additions']), 2)


class LoanRepaymentTest(unittest.TestCase):
    def setup(self, repaid):
        settings = config(accounts={'tbank': {
            '1': {'account': 'Карта Тинь', 'unit': 'RUB', 'balance_id': '1'},
            'Кубышка': {'account': 'Тинь (кубышка)', 'unit': 'RUB', 'balance_id': None}}})
        loan = entry('loan', account='Тинь (кубышка)', kind='transfer_leg', amount='-30000', other_account='Карта Тинь',
                     markers=[marker('loan', source='tbank')], timestamp='2026-07-02T10:00:00+03:00')
        book = ledger(loan, accounts=[app_account('Карта Тинь'), app_account('Тинь (кубышка)', '-30000')])
        repay = bank('repay', kind='transfer', other_account='Кубышка', amount=f'-{repaid}',
                     description='Пополнение Кубышки')
        return run([source(repay)], book, settings)

    def test_repayment_above_the_loan_goes_to_review(self):
        result = self.setup('30531')
        self.assertEqual(result['additions'], [])
        self.assertEqual(set(reasons(result).values()), {'repayment_exceeds_loan'})

    def test_repayment_of_the_whole_loan_is_written(self):
        result = self.setup('30000')
        self.assertEqual(len(result['additions']), 2)


class BalanceTest(unittest.TestCase):
    def balances(self, *rows):
        return {'accounts': [{'account': a, 'account_name': None, 'amount': v, 'currency': 'RUB'}
                             for a, v in rows], 'observed_at': 'T', 'errors': []}

    def test_app_balance_is_compared_with_the_sum_of_its_bank_accounts(self):
        book = ledger(accounts=[app_account('Карта Тинь', '10', '7.50'), app_account('Тинь (кред)', '-5')])
        result = check_balances(book, [source(balances=self.balances(('1', '7.50'), ('2', '-4')))], config())
        self.assertEqual(result['Карта Тинь']['state'], 'matches')
        self.assertEqual(result['Тинь (кред)'], {'state': 'differs', 'bank': '-4', 'app': '-5',
                                                 'difference': '1'})

    def test_account_with_an_unread_bank_side_is_not_checked(self):
        settings = config(accounts={'tbank': {
            '1': {'account': 'Карта Тинь', 'unit': 'RUB', 'balance_id': '1'},
            'Кубышка': {'account': 'Карта Тинь', 'unit': 'RUB', 'balance_id': None}}})
        result = check_balances(ledger(), [source(balances=self.balances(('1', '0')))], settings)
        self.assertEqual(result['Карта Тинь']['state'], 'not_checked')

    def test_source_without_balances_leaves_its_accounts_unchecked(self):
        result = check_balances(ledger(), [source(balances=None)], config())
        self.assertEqual({v['state'] for v in result.values()}, {'not_checked'})


if __name__ == '__main__':
    unittest.main()
