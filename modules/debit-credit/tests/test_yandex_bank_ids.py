"""ID ленты Яндекс Банка меняется между чтениями, номер операции — нет."""

import base64
import json
import unittest

from adapters.markers import extract_markers, Marker
from adapters.matching import compare_records
from adapters.yandex_bank_api import read_history
from adapters.yandex_bank_ids import operation_id


def encode(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def feed_id(operation: str, versions=(0,), kind='UNQ_YABANK') -> str:
    chain = [{'type': 'DEPOT', 'operationId': operation, 'chainId': operation,
              'operationTimestamp': '2026-10-19T13:36:47.400Z', 'chainVersionIds': list(versions)}]
    return f'FEED_VIEW::{kind}::::::{encode(operation)}::::false::::V4::{encode(json.dumps(chain))}::true'


def points_feed_id(payment: str) -> str:
    chain = [{'type': 'OHIO_LINKED_TXNS', 'operationId': payment,
              'operationTimestamp': '2026-10-07T05:09:25Z', 'trustPaymentId': payment[6:]}]
    return f'FEED_VIEW::UNQ_OHIO::::::::::false::::V4::{encode(json.dumps(chain))}::true'


def snapshot(identifier: str, status='CLEAR') -> dict:
    detail = {'requestedId': identifier, 'id': identifier, 'date': '2026-10-19T13:36:47.400Z',
              'direction': 'DEBIT', 'statusCode': status,
              'title': {'plain': 'Самокаты', 'compound': None},
              'amount': {'money': {'amount': '88.00', 'currency': 'RUB'}, 'plus': None},
              'additionalFieldsList': [{'additionalFields': [
                  {'name': 'Способ оплаты', 'value': 'Карта Пэй'}]}], 'cashback': None}
    row = {'id': identifier, 'date': detail['date'], 'amount': detail['amount'],
           'direction': 'DEBIT', 'statusCode': status}
    return {'schema_version': 1, 'adapter': 'yandex-bank-graphql', 'records': [row],
            'details': [detail], 'pages': [{'items': [row], 'pendingItems': [], 'cursor': None}],
            'cursorExhausted': True, 'complete': True, 'conflicts': [], 'detailErrors': [],
            'unreadIds': [], 'period': {'fromInclusive': '2026-10-18T00:00:00+03:00',
                                        'toExclusive': '2026-10-20T00:00:00+03:00'}}


MAPPING = {'yabank': {'Карта Пэй': {'account': 'Карта Пэй', 'source_currency': 'RUB',
                                    'currency': 'RUB'}}}


def ledger_row(identifier: str, timestamp='2026-10-19T16:36:00+03:00') -> dict:
    return {'source': 'debit-credit', 'source_id': 'app-1', 'locator': 1, 'timestamp': timestamp,
            'timestamp_precision': 'minute', 'amount': '-88', 'currency': 'RUB',
            'account': 'Карта Пэй', 'cleared': True, 'status': 'completed',
            'markers': [{'source': 'yabank', 'id': identifier, 'role': None}]}


class OperationIdTest(unittest.TestCase):
    def test_card_operation_number_is_read_from_feed_id(self):
        self.assertEqual(operation_id(feed_id('54186314773346304')), '54186314773346304')

    def test_chain_versions_do_not_change_operation_number(self):
        self.assertEqual(operation_id(feed_id('54186314773346304', (0,))),
                         operation_id(feed_id('54186314773346304', (0, 4, 5))))

    def test_points_operation_uses_service_payment_id(self):
        self.assertEqual(operation_id(points_feed_id('trust_6ac5d40558de748983351b61')),
                         'trust_6ac5d40558de748983351b61')

    def test_unknown_format_has_no_operation_number(self):
        for value in ('feed-a', '54186314773346304', 'FEED_VIEW::UNQ_YABANK::broken'):
            self.assertIsNone(operation_id(value))


class SourceIdentityTest(unittest.TestCase):
    def test_bank_record_is_identified_by_operation_number_and_keeps_feed_id(self):
        pending = read_history(snapshot(feed_id('541', (0,)), 'HOLD'))['records'][0]
        completed = read_history(snapshot(feed_id('541', (0, 4, 5))))['records'][0]
        self.assertEqual(pending['source_id'], '541')
        self.assertEqual(completed['source_id'], '541')
        self.assertEqual(completed['feed_id'], feed_id('541', (0, 4, 5)))

    def test_unknown_feed_id_is_kept_and_flagged(self):
        result = read_history(snapshot('feed-a'))
        self.assertEqual(result['records'][0]['source_id'], 'feed-a')
        self.assertIn('unstable_feed_identity', result['records'][0]['issues'])
        self.assertFalse(result['ready'])

    def test_legacy_feed_id_marker_is_read_as_operation_number(self):
        notes = 'Ужин ' + Marker('yabank', feed_id('541', (0,)), 'cashback').render()
        self.assertEqual(extract_markers(notes), [Marker('yabank', '541', 'cashback')])

    def test_marker_of_another_source_is_not_rewritten(self):
        identifier = feed_id('541')
        self.assertEqual(extract_markers(Marker('tbank', identifier).render())[0].id, identifier)


class MissingFromSourceTest(unittest.TestCase):
    def compare(self, sources, ledger, period):
        return compare_records(sources, ledger, MAPPING, window_seconds=120, source_period=period)

    def test_marked_record_absent_from_read_period_is_reported_not_removed(self):
        source = read_history(snapshot(feed_id('541')))
        ledger = [ledger_row('541'), ledger_row('999')]
        result = self.compare(source['records'], ledger, source['period'])
        self.assertEqual([row['ledger_index'] for row in result['missing_from_source']], [1])
        self.assertEqual(result['missing_from_source'][0]['reason'],
                         'marked_operation_absent_from_source_period')
        self.assertEqual(len(result['ledger_records']), 2)

    def test_record_outside_read_period_is_not_missing(self):
        source = read_history(snapshot(feed_id('541')))
        ledger = [ledger_row('999', '2026-10-21T10:00:00+03:00')]
        self.assertEqual(self.compare(source['records'], ledger, source['period'])['missing_from_source'], [])

    def test_without_period_missing_operations_are_not_claimed(self):
        source = read_history(snapshot(feed_id('541')))
        result = self.compare(source['records'], [ledger_row('999')], None)
        self.assertEqual(result['missing_from_source'], [])
        self.assertIn('missing_from_source_not_checked', result['limitations'])


if __name__ == '__main__':
    unittest.main()
