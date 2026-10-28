"""История Сбера: требования раздела «Сбер» без потери прочитанных операций."""

import unittest
from datetime import datetime

from adapters.matching import validate_records
from adapters.sber_api import BONUS_ACCOUNT, read_history

CARD = {'id': 'card:1000000001', 'displayedValue': 'MIR Classic •• 0001'}
CREDIT = {'id': 'card:1000000002', 'displayedValue': 'Кредитная СберКарта •• 0002'}
OUTSIDE = {'id': 'account:2000000001', 'displayedValue': 'Счёт •• 0001'}


def money(value, currency='RUB'):
    return {'amount': value, 'currencyCode': currency}


def operation(identifier, value, *, source=None, target=None, form='ExtCardPayment',
              state=('FINANCIAL', 'executed'), **extra):
    row = {'uohId': identifier, 'date': '10.02.2026T12:00:00', 'form': form,
           'state': {'name': state[0], 'category': state[1]}, 'description': 'Оплата товаров и услуг',
           'correspondent': 'Самокат', 'isFinancial': True, 'type': 'payment',
           'operationAmount': money(value), 'nationalAmount': money(value)}
    if source:
        row['fromResource'] = source
    if target:
        row['toResource'] = target
    row.update(extra)
    return row


def snapshot(records, **extra):
    result = {'schema_version': 1, 'adapter': 'sber-operations', 'complete': True,
              'period': {'fromInclusive': '2026-02-01T00:00:00+03:00',
                         'toExclusive': '2026-03-01T00:00:00+03:00'},
              'pages': [{'offset': 0, 'count': len(records)}], 'records': records,
              'observations': list(records), 'excluded': [], 'conflicts': [], 'repeatedObservations': 0}
    result.update(extra)
    return result


def by_id(result):
    return {row['source_id']: row for row in result['records']}


class SberRecordTest(unittest.TestCase):
    def test_purchase_keeps_operation_number_account_amount_and_moscow_time(self):
        result = read_history(snapshot([operation('p1', -350.5, source=CARD)]))
        row = by_id(result)['p1']
        self.assertEqual(row['source'], 'sber')
        self.assertEqual(row['account'], CARD['id'])
        self.assertEqual(row['account_name'], CARD['displayedValue'])
        self.assertEqual(row['amount'], '-350.5')
        self.assertEqual(row['currency'], 'RUB')
        self.assertEqual(datetime.fromisoformat(row['timestamp']).isoformat(), '2026-02-10T12:00:00+03:00')
        self.assertEqual(row['timestamp_precision'], 'second')
        self.assertEqual((row['status'], row['cleared']), ('completed', True))
        self.assertEqual(row['description'], 'Самокат')
        validate_records(result['records'])
        self.assertTrue(result['ready'])
        self.assertEqual(result['coverage'], 'snapshot_complete')
        self.assertEqual(result['source'], 'sber')

    def test_operation_left_on_authorization_is_recorded_as_a_purchase(self):
        row = by_id(read_history(snapshot([operation('a1', -300, source=CREDIT,
                                                     state=('AUTHORIZATION', 'executed'))])))['a1']
        self.assertEqual((row['status'], row['cleared']), ('pending', False))
        self.assertNotIn('not_recorded_reason', row)

    def test_declined_operation_is_kept_and_marked_not_recorded(self):
        row = by_id(read_history(snapshot([operation('f1', -399, source=CARD,
                                                     state=('UNAUTHORIZATION', 'cancel'))])))['f1']
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['not_recorded_reason'], 'failed_operation')

    def test_application_without_money_is_kept_and_marked_not_recorded(self):
        statement = operation('s1', 0, form='UfsRetailStatement', isFinancial=False)
        for key in ('operationAmount', 'nationalAmount'):
            statement.pop(key)
        row = by_id(read_history(snapshot([statement])))['s1']
        self.assertEqual(row['amount'], '0')
        self.assertEqual(row['not_recorded_reason'], 'non_financial')

    def test_draft_or_waiting_application_is_not_an_issue(self):
        rows = []
        for identifier, state in (('d1', ('INITIAL', 'draft')), ('d2', ('RECEIVED', 'waiting'))):
            row = operation(identifier, 0, form='UfsLoanClaim', isFinancial=False, state=state)
            row.pop('operationAmount')
            row.pop('nationalAmount')
            rows.append(row)
        result = read_history(snapshot(rows))
        self.assertEqual(result['issues'], [])
        self.assertTrue(all(row['not_recorded_reason'] == 'non_financial' for row in result['records']))

    def test_incoming_money_lands_on_the_receiving_card(self):
        row = by_id(read_history(snapshot([operation('i1', 99, source=OUTSIDE, target=CARD,
                                                     form='P2PSBPInTransfer')])))['i1']
        self.assertEqual((row['account'], row['amount'], row['kind']), (CARD['id'], '99', None))
        self.assertEqual(row['counterparty_resource'], OUTSIDE['id'])

    def test_old_ruble_code_is_read_as_rubles(self):
        row = operation('r1', -10, source=CARD)
        row['operationAmount'] = money(-10, 'RUR')
        self.assertEqual(by_id(read_history(snapshot([row])))['r1']['currency'], 'RUB')

    def test_sber_spasibo_points_go_to_their_own_account(self):
        bonus = operation('b1', 0, form='UfsExtAccrualBonus', isFinancial=False,
                          bonuses=[{'currency': 'SBER_SPASIBO', 'income': 500}])
        spend = operation('b2', 0, form='UfsExtWritingOffBonus', isFinancial=False,
                          bonuses=[{'currency': 'SBER_SPASIBO', 'outcome': 200}])
        for row in (bonus, spend):
            row.pop('operationAmount')
            row.pop('nationalAmount')
        rows = by_id(read_history(snapshot([bonus, spend])))
        self.assertEqual((rows['b1']['account'], rows['b1']['amount'], rows['b1']['currency']),
                         (BONUS_ACCOUNT, '500', 'SBER_SPASIBO'))
        self.assertEqual(rows['b2']['amount'], '-200')
        self.assertNotIn('not_recorded_reason', rows['b1'])


class SberTransferTest(unittest.TestCase):
    def test_own_transfer_is_one_operation_with_both_sides(self):
        row = by_id(read_history(snapshot([operation('t1', -99, source=CARD, target=CREDIT,
                                                     form='UfsTransferSelf',
                                                     state=('EXECUTED', 'executed'))])))['t1']
        self.assertEqual((row['kind'], row['account'], row['other_account'], row['amount']),
                         ('transfer', CARD['id'], CREDIT['id'], '-99'))


    def test_own_transfer_without_other_side_stays_a_transfer_and_is_flagged(self):
        result = read_history(snapshot([operation('t2', -99, source=CARD, form='UfsTransferSelf',
                                                   state=('EXECUTED', 'executed'))]))
        row = by_id(result)['t2']
        self.assertEqual((row['kind'], row['other_account']), ('transfer', None))
        self.assertIn('transfer_counterpart_unknown', row['issues'])
        self.assertFalse(result['ready'])


class SberCoverageTest(unittest.TestCase):
    def test_financial_operation_without_own_side_is_rejected_with_reason(self):
        result = read_history(snapshot([operation('x1', -10)]))
        self.assertEqual(result['records'], [])
        self.assertIn('счёт', result['rejected'][0]['reason'])
        self.assertFalse(result['ready'])

    def test_incomplete_collection_is_not_complete_coverage(self):
        self.assertEqual(read_history(snapshot([operation('p1', -1, source=CARD)], complete=False))['coverage'],
                         'unverified')

    def test_repeated_operation_number_is_flagged(self):
        result = read_history(snapshot([operation('p1', -1, source=CARD), operation('p1', -1, source=CARD)]))
        self.assertTrue(all('duplicate_source_id' in row['issues'] for row in result['records']))

    def test_wrong_snapshot_is_refused(self):
        with self.assertRaises(ValueError):
            read_history({'adapter': 'tbank-timeline', 'schema_version': 1, 'records': []})


if __name__ == '__main__':
    unittest.main()
