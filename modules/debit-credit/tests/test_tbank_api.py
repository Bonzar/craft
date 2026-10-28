"""Лента Т-Банка: требования раздела «Т-Банк» без потери прочитанных операций."""

import unittest
from datetime import datetime, timezone

from adapters.matching import validate_records
from adapters.tbank_api import KUBYSHKA_ACCOUNT, read_history

CARD, CREDIT = '1000000001', '1000000002'
SAVINGS = '1000000009'
START = datetime(2026, 2, 1, 9, tzinfo=timezone.utc)


def money(value):
    return {'value': value, 'currency': {'code': 643, 'name': 'RUB', 'strCode': '643'}}


def operation(identifier, value, kind='Debit', account=CARD, seconds=0, **extra):
    row = {'id': identifier, 'account': account, 'type': kind, 'status': 'OK',
           'operationTime': {'milliseconds': int(START.timestamp() * 1000) + seconds * 1000},
           'amount': money(value), 'accountAmount': money(value), 'description': 'Самокат',
           'category': {'id': '1', 'name': 'Транспорт'}, 'mcc': 4121,
           'authorizationId': 'auth-1', 'operationId': {'source': 'PrimeAuth', 'value': 'op-' + identifier}}
    row.update(extra)
    return row


def inner_debit(identifier, value, source, target, seconds=0):
    return operation(identifier, value, 'Debit', source, seconds, description='Между своими счетами',
                     payment={'providerId': 'transfer-inner', 'bankAccountId': source,
                              'fieldsValues': {'bankContract': target}})


def inner_credit(identifier, value, source, target, seconds=0):
    return operation(identifier, value, 'Credit', target, seconds, description='Между своими счетами',
                     senderAgreement=source,
                     payment={'providerId': 'transfer-inner', 'bankAccountId': source,
                              'fieldsValues': {'bankContract': target}})


def snapshot(records, **extra):
    observations = list(records)
    result = {'schema_version': 1, 'adapter': 'tbank-timeline', 'complete': True,
              'period': {'fromInclusive': '2026-02-01T00:00:00+03:00',
                         'toExclusive': '2026-03-01T00:00:00+03:00'},
              'accounts': [CARD, CREDIT], 'chunks': [{'start': 0, 'end': 1, 'count': len(records)}],
              'records': records, 'observations': observations, 'excluded': [], 'conflicts': [],
              'repeatedObservations': 0}
    result.update(extra)
    return result


def by_id(result):
    return {row['source_id']: row for row in result['records']}


class TbankRecordTest(unittest.TestCase):
    def test_purchase_keeps_operation_id_amount_sign_and_time(self):
        result = read_history(snapshot([operation('p1', 350.5)]))
        row = by_id(result)['p1']
        self.assertEqual(row['source'], 'tbank')
        self.assertEqual(row['amount'], '-350.5')
        self.assertEqual(row['currency'], 'RUB')
        self.assertEqual(row['account'], CARD)
        self.assertEqual(datetime.fromisoformat(row['timestamp']), START)
        self.assertEqual(row['timestamp_precision'], 'second')
        self.assertEqual((row['status'], row['cleared']), ('completed', True))
        self.assertEqual(row['description'], 'Самокат')
        self.assertEqual(row['bank_category'], 'Транспорт')
        validate_records(result['records'])
        self.assertTrue(result['ready'])
        self.assertEqual(result['coverage'], 'snapshot_complete')
        self.assertEqual(result['source'], 'tbank')

    def test_authorization_number_is_not_the_operation_number(self):
        result = read_history(snapshot([operation('p1', 10), operation('p2', 20)]))
        self.assertEqual(set(by_id(result)), {'p1', 'p2'})
        self.assertEqual(result['issues'], [])

    def test_failed_operation_is_kept_and_marked_not_recorded(self):
        row = by_id(read_history(snapshot([operation('f1', 99, status='FAILED')])))['f1']
        self.assertEqual(row['status'], 'failed')
        self.assertIsNone(row['cleared'])
        self.assertEqual(row['not_recorded_reason'], 'failed_operation')

    def test_zero_cashback_is_kept_and_marked_not_recorded(self):
        cashback = operation('c0', 0, 'Credit', CREDIT, description='Зачисление кэшбэка',
                             operationId={'source': 'LoyaltyBonus', 'value': '1'})
        row = by_id(read_history(snapshot([cashback])))['c0']
        self.assertEqual(row['not_recorded_reason'], 'zero_cashback')

    def test_cashback_with_amount_is_an_ordinary_income(self):
        cashback = operation('c1', 120, 'Credit', CREDIT, description='Зачисление кэшбэка',
                             operationId={'source': 'LoyaltyBonus', 'value': '2'})
        row = by_id(read_history(snapshot([cashback])))['c1']
        self.assertEqual(row['amount'], '120')
        self.assertNotIn('not_recorded_reason', row)


class TbankTransferTest(unittest.TestCase):
    def test_own_transfer_of_two_operations_becomes_one_transfer(self):
        result = read_history(snapshot([inner_debit('d1', 5000, CARD, CREDIT),
                                        inner_credit('c1', 5000, CARD, CREDIT, seconds=1)]))
        debit, credit = by_id(result)['d1'], by_id(result)['c1']
        self.assertEqual((debit['kind'], debit['account'], debit['other_account']),
                         ('transfer', CARD, CREDIT))
        self.assertEqual(debit['amount'], '-5000')
        self.assertEqual(credit['resolution']['same_operation_as'], {'source': 'tbank', 'id': 'd1'})
        self.assertEqual((credit['kind'], credit['account'], credit['other_account']),
                         ('transfer', CREDIT, CARD))
        self.assertTrue(result['ready'])

    def test_transfer_to_an_unread_own_account_keeps_its_number(self):
        row = by_id(read_history(snapshot([inner_debit('d2', 300, CARD, SAVINGS)])))['d2']
        self.assertEqual((row['kind'], row['other_account']), ('transfer', SAVINGS))
        self.assertNotIn('resolution', row)

    def test_legs_too_far_apart_are_not_paired(self):
        result = read_history(snapshot([inner_debit('d3', 700, CARD, CREDIT),
                                        inner_credit('c3', 700, CARD, CREDIT, seconds=3600)]))
        self.assertNotIn('resolution', by_id(result)['c3'])
        self.assertIn('transfer_pair_not_found', by_id(result)['d3']['issues'])
        self.assertFalse(result['ready'])

    def test_two_debits_matching_one_credit_leave_the_credit_unresolved_in_any_order(self):
        rows = [inner_debit('d4', 500, CARD, CREDIT), inner_debit('d5', 500, CARD, CREDIT, seconds=30),
                inner_credit('c4', 500, CARD, CREDIT, seconds=10)]
        for order in (rows, list(reversed(rows))):
            result = by_id(read_history(snapshot(order)))
            self.assertNotIn('resolution', result['c4'])
            self.assertIn('ambiguous_transfer_pair', result['c4']['issues'])
            self.assertTrue(all('ambiguous_transfer_pair' in result[i]['issues'] for i in ('d4', 'd5')))

    def test_legs_in_different_currencies_are_not_paired(self):
        credit = inner_credit('c6', 100, CARD, CREDIT, seconds=1)
        credit['accountAmount'] = {'value': 100, 'currency': {'code': 840, 'name': 'USD', 'strCode': '840'}}
        result = read_history(snapshot([inner_debit('d6', 100, CARD, CREDIT), credit]))
        self.assertNotIn('resolution', by_id(result)['c6'])
        self.assertIn('transfer_pair_not_found', by_id(result)['d6']['issues'])

    def test_kubyshka_loan_and_repayment_are_transfers_with_the_card(self):
        loan = operation('k1', 3000, 'Credit', CARD, description='Перевод средств из Кубышки')
        repay = operation('k2', 3000, 'Debit', CARD, seconds=60, description='Пополнение Кубышки')
        rows = by_id(read_history(snapshot([loan, repay])))
        self.assertEqual((rows['k1']['kind'], rows['k1']['account'], rows['k1']['other_account']),
                         ('transfer', CARD, KUBYSHKA_ACCOUNT))
        self.assertEqual(rows['k1']['amount'], '3000')
        self.assertEqual((rows['k2']['kind'], rows['k2']['other_account']), ('transfer', KUBYSHKA_ACCOUNT))
        self.assertEqual(rows['k2']['amount'], '-3000')


class TbankCoverageTest(unittest.TestCase):
    def test_operation_of_a_foreign_account_is_rejected_with_reason(self):
        result = read_history(snapshot([operation('x1', 10, account='9999999999')]))
        self.assertEqual(result['records'], [])
        self.assertEqual(result['rejected'][0]['locator'], 1)
        self.assertIn('счёт', result['rejected'][0]['reason'])
        self.assertFalse(result['ready'])

    def test_incomplete_or_conflicting_collection_is_not_complete_coverage(self):
        partial = read_history(snapshot([operation('p1', 10)], complete=False))
        self.assertEqual(partial['coverage'], 'unverified')
        excluded = read_history(snapshot([operation('p1', 10)],
                                         excluded=[{'record': {'id': 'z'}, 'reason': 'outside_requested_period'}]))
        self.assertEqual(excluded['coverage'], 'unverified')

    def test_repeated_operation_number_is_flagged(self):
        result = read_history(snapshot([operation('p1', 10), operation('p1', 10)]))
        self.assertTrue(all('duplicate_source_id' in row['issues'] for row in result['records']))

    def test_wrong_snapshot_is_refused(self):
        with self.assertRaises(ValueError):
            read_history({'adapter': 'yandex-bank-graphql', 'schema_version': 1, 'records': []})

    def test_input_count_covers_every_read_operation(self):
        result = read_history(snapshot([operation('p1', 10), operation('x1', 10, account='9999999999')]))
        self.assertEqual(result['input_count'], len(result['records']) + len(result['rejected']))


if __name__ == '__main__':
    unittest.main()
