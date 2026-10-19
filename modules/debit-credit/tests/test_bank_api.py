"""JSON API сохраняет самостоятельные операции, статусы и единицы суммы."""

import copy
import unittest

from adapters.yandex_bank_api import read_history


def detail(identifier='feed-a', **changes):
    return {'requestedId': identifier, 'id': 'native-a', 'date': '2026-07-04T07:00:26.388Z',
            'direction': 'DEBIT', 'statusCode': 'CLEAR',
            'title': {'plain': 'Магазин', 'compound': None},
            'amount': {'money': {'amount': '120.50', 'currency': 'RUB'}, 'plus': None},
            'additionalFieldsList': [{'additionalFields': [
                {'name': 'Способ оплаты', 'value': 'Карта Пэй'},
                {'name': 'MCC', 'value': '5411'}]}],
            'cashback': None, **changes}


def snapshot(*details):
    rows = [{'id': r['requestedId'], 'date': r['date'], 'amount': r['amount'],
             'direction': r['direction'], 'statusCode': r['statusCode']} for r in details]
    return {'schema_version': 1, 'adapter': 'yandex-bank-graphql', 'records': rows,
            'details': list(details), 'pages': [{'items': copy.deepcopy(rows),
                'pendingItems': [], 'cursor': None}], 'cursorExhausted': True,
            'complete': True, 'conflicts': [], 'detailErrors': [], 'unreadIds': []}


class BankApiTest(unittest.TestCase):
    def test_native_id_is_kept_without_replacing_feed_identity(self):
        result = read_history(snapshot(detail()))
        row = result['records'][0]
        self.assertEqual(row['source_id'], 'feed-a')
        self.assertEqual(row['native_id'], 'native-a')
        self.assertEqual(row['timestamp'], '2026-07-04T07:00:26.388000+00:00')
        self.assertEqual(row['timestamp_precision'], 'millisecond')
        self.assertEqual(row['amount'], '-120.50')
        self.assertEqual(row['account'], 'Карта Пэй')
        self.assertEqual(row['mcc'], '5411')
        self.assertTrue(result['ready'])

    def test_failed_canceled_and_pending_statuses_are_distinct(self):
        rows = [detail(str(i), statusCode=status) for i, status in enumerate(['FAIL', 'CANCEL', 'HOLD'])]
        result = read_history(snapshot(*rows))
        self.assertEqual([r['status'] for r in result['records']], ['failed', 'canceled', 'pending'])
        self.assertEqual([r['cleared'] for r in result['records']], [None, None, False])

    def test_standalone_points_and_foreign_currency_are_preserved(self):
        points = detail('p', amount={'money': None, 'plus': '400.0'}, direction='CREDIT',
                        additionalFieldsList=[])
        foreign = detail('f', amount={'money': {'amount': '64.2', 'currency': 'BYN'}, 'plus': None})
        rows = read_history(snapshot(points, foreign))['records']
        self.assertEqual((rows[0]['currency'], rows[0]['account'], rows[0]['amount']),
                         ('YPLUS_POINTS', 'ЯПлюс Баллы', '400.0'))
        self.assertEqual(rows[1]['currency'], 'BYN')

    def test_transfer_titles_determine_two_sides_without_reusing_purchase_id(self):
        title = {'plain': None, 'compound': {'firstPart': 'Карта Пэй', 'secondPart': 'Округление'}}
        rows = read_history(snapshot(detail('out', title=title),
                                     detail('in', title=title, direction='CREDIT')))['records']
        self.assertEqual([r['account'] for r in rows], ['Карта Пэй', 'Округление'])
        self.assertEqual([r['other_account'] for r in rows], ['Округление', 'Карта Пэй'])
        self.assertEqual([r['source_id'] for r in rows], ['out', 'in'])

    def test_single_preposition_is_removed_from_named_source_account(self):
        r = detail(additionalFieldsList=[{'additionalFields': [
            {'name': 'Пополнение', 'value': 'На На подарок жене'}]}], direction='CREDIT')
        self.assertEqual(read_history(snapshot(r))['records'][0]['account'], 'На подарок жене')

    def test_unknown_account_is_preserved_as_unknown_with_raw_fields(self):
        r = detail(additionalFieldsList=[{'additionalFields': [{'name': 'Новое поле', 'value': 'private'}]}])
        result = read_history(snapshot(r))
        self.assertIsNone(result['records'][0]['account'])
        self.assertFalse(result['ready'])
        self.assertEqual(result['records'][0]['raw']['detail'], r)

    def test_cashback_with_none_status_is_recognized_as_preliminary(self):
        info = {'cashbackStatus': 'NONE', 'direction': 'CREDIT', 'partialValue': None,
                'totalValue': {'money': None, 'plus': '28.00'}}
        r = detail(cashback={'summary': None, 'servicesCashback': [{'cashbackInfo': info}]})
        result = read_history(snapshot(r))
        self.assertEqual(result['records'][0]['cashback_hint'], '28.00')
        self.assertIs(result['records'][0]['cashback_cleared'], False)
        self.assertTrue(result['ready'])

    def test_fully_accrued_cashback_is_independent_of_purchase_completion(self):
        info = {'cashbackStatus': 'FULLY', 'direction': 'CREDIT', 'partialValue': None,
                'totalValue': {'money': None, 'plus': '20.00'}}
        r = detail(statusCode='HOLD', cashback={'summary': None,
                   'servicesCashback': [{'cashbackInfo': info}]})
        row = read_history(snapshot(r))['records'][0]
        self.assertIs(row['cleared'], False)
        self.assertIs(row['cashback_cleared'], True)

    def test_unknown_cashback_status_still_requires_review(self):
        info = {'cashbackStatus': 'NEW_STATUS', 'direction': 'CREDIT', 'partialValue': None,
                'totalValue': {'money': None, 'plus': '20.00'}}
        r = detail(cashback={'summary': None, 'servicesCashback': [{'cashbackInfo': info}]})
        result = read_history(snapshot(r))
        self.assertFalse(result['ready'])
        self.assertIn('cashback_status_requires_review', result['records'][0]['issues'])

    def test_duplicate_native_ids_do_not_merge_distinct_feed_records(self):
        result = read_history(snapshot(detail('a'), detail('b')))
        self.assertEqual(len(result['records']), 2)
        self.assertEqual([r['source_id'] for r in result['records']], ['a', 'b'])

    def test_missing_and_extra_details_remain_visible(self):
        value = snapshot(detail())
        value['details'] = [detail('extra')]
        result = read_history(value)
        self.assertEqual(result['input_count'], 1)
        self.assertEqual(len(result['rejected']), 1)
        self.assertEqual(result['unmatched_details'], [detail('extra')])
        self.assertFalse(result['ready'])

    def test_page_mismatch_does_not_prove_period_coverage(self):
        value = snapshot(detail())
        value['pages'][0]['items'] = []
        result = read_history(value)
        self.assertEqual(result['coverage'], 'unverified')
        self.assertFalse(result['ready'])

    def test_changed_amount_between_feed_and_card_is_reported(self):
        value = snapshot(detail())
        value['details'][0]['amount'] = {'money': {'amount': '130', 'currency': 'RUB'}, 'plus': None}
        result = read_history(value)
        self.assertIn('feed_detail_amount_differs', result['records'][0]['issues'])
        self.assertFalse(result['ready'])

    def test_input_is_not_mutated(self):
        value = snapshot(detail())
        before = copy.deepcopy(value)
        read_history(value)
        self.assertEqual(value, before)

    def test_missing_account_is_resolved_by_shared_receipt_agreement(self):
        action = {'action': 'yandexbank://screen.open/pdf_load?agreement_id=wallet-a&product_type=WALLET'}
        known = detail('known', actions=[action], title={'plain': None, 'compound': {
            'firstPart': 'Карта Пэй', 'secondPart': 'Округление'}})
        unknown = detail('unknown', actions=[action], additionalFieldsList=[])
        result = read_history(snapshot(known, unknown))
        row = result['records'][1]
        self.assertEqual(row['account'], 'Карта Пэй')
        self.assertNotIn('unknown_account', row['issues'])
        self.assertEqual(row['account_evidence']['witness_ids'], ['known'])

    def test_conflicting_names_for_agreement_require_review(self):
        action = {'action': 'yandexbank://screen.open/pdf_load?agreement_id=saver-a'}
        first = detail('one', actions=[action], title={'plain': None, 'compound': {
            'firstPart': 'Сейв один', 'secondPart': 'Карта Пэй'}})
        second = detail('two', actions=[action], title={'plain': None, 'compound': {
            'firstPart': 'Сейв два', 'secondPart': 'Карта Пэй'}})
        unknown = detail('three', actions=[action], additionalFieldsList=[])
        row = read_history(snapshot(first, second, unknown))['records'][2]
        self.assertIsNone(row['account'])
        self.assertIn('ambiguous_bank_account_identity', row['issues'])

    def test_unsigned_points_use_explicit_spending_purpose(self):
        value = detail(direction=None, amount={'money': None, 'plus': '389.0'},
                       additionalFieldsList=[{'additionalFields': [
                           {'name': 'Назначение', 'value': 'Списание баллов от Магазин'}]}])
        result = read_history(snapshot(value))
        self.assertEqual(result['records'][0]['amount'], '-389.0')
        self.assertEqual(result['records'][0]['direction_evidence']['value'],
                         'Списание баллов от Магазин')
        self.assertTrue(result['ready'])

    def test_unsigned_points_without_explicit_purpose_remain_rejected(self):
        value = detail(direction=None, amount={'money': None, 'plus': '389.0'},
                       additionalFieldsList=[])
        result = read_history(snapshot(value))
        self.assertEqual(len(result['rejected']), 1)
        self.assertEqual(len(result['records']), 0)


if __name__ == '__main__':
    unittest.main()
