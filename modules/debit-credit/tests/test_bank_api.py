"""JSON API сохраняет самостоятельные операции, статусы и единицы суммы."""

import copy
import unittest

from adapters.yandex_bank_api import read_history
from test_yandex_bank_ids import feed_id


def detail(identifier='feed-a', **changes):
    """identifier — номер операции, ID ленты собирается в настоящем формате."""
    return {'requestedId': feed_id(identifier), 'id': 'native-a', 'date': '2026-07-04T07:00:26.388Z',
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
    def test_bank_comment_reaches_review_without_assigning_app_metadata(self):
        from adapters.matching import compare_records
        from adapters.review import build_review
        value = snapshot(detail(comment='Предоплата за кейтеринг свадьбы'))
        saved = copy.deepcopy(value)
        parsed = read_history(value)
        mapping = {'yabank': {'Карта Пэй': {'account': 'Карта Пэй',
                   'source_currency': 'RUB', 'currency': 'RUB'}}}
        case = build_review(compare_records(parsed['records'], [], mapping,
                                           window_seconds=120))['records'][0]
        self.assertEqual(case['source']['bank_comment'], 'Предоплата за кейтеринг свадьбы')
        self.assertNotIn('raw', case['source'])
        self.assertEqual(case['source']['notes'], '')
        self.assertIsNone(case['source']['category'])
        self.assertIsNone(case['source']['tag'])
        self.assertEqual(value, saved)

    def test_nontext_comment_is_flagged_without_rejecting_the_operation(self):
        value = snapshot(detail(comment={'future': 'structured comment'}))
        result = read_history(value)
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['rejected'], [])
        self.assertIsNone(result['records'][0]['bank_comment'])
        self.assertIn('bank_comment_requires_review', result['records'][0]['issues'])
        self.assertEqual(result['records'][0]['raw']['detail']['comment'],
                         value['details'][0]['comment'])
        self.assertFalse(result['ready'])

    def test_filter_accounts_and_displayed_balances_are_kept_separately_from_operations(self):
        value = snapshot(detail())
        value['pages'][0]['sourceFilter'] = {'options': [
            {'filterId': '<SOURCE>::WALLET_pa_card', 'title': 'Карта Пэй', 'subtitle': None},
            {'filterId': '<SOURCE>::SAVINGS_ACCOUNT_sa_save', 'title': 'Сейв',
             'subtitle': '1\u00a0234,56\u00a0₽'},
            {'filterId': '<SOURCE>::SAVINGS_COMBINED', 'title': 'Сейвы', 'subtitle': 'Все счета'}]}
        value['balanceObservations'] = [{'phase': 'before', 'balance': {'amount': '900'}}]
        saved = copy.deepcopy(value)
        result = read_history(value)
        observations = result['source_account_observations']
        self.assertEqual(observations['input_count'], 3)
        self.assertEqual(len(observations['records']), 2)
        self.assertEqual(len(observations['excluded']), 1)
        self.assertEqual(observations['rejected'], [])
        save = observations['records'][1]
        self.assertEqual((save['bank_account_id'], save['name'], save['balance'], save['currency']),
                         ('sa_save', 'Сейв', '1234.56', 'RUB'))
        self.assertIsNone(observations['records'][0]['balance'])
        self.assertEqual(result['balance_observations'], value['balanceObservations'])
        self.assertEqual(result['input_count'], 1)
        self.assertEqual(value, saved)
        self.assertFalse(observations['reconciled'])

    def test_empty_last_page_filter_groups_are_excluded_without_parse_errors(self):
        value = snapshot(detail())
        value['pages'].append({'items': [], 'pendingItems': [], 'cursor': None,
                              'sourceFilter': {'options': [
            {'filterId': '<SOURCE>::SAVINGS_COMBINED', 'title': 'Сейвы'},
            {'filterId': '<SOURCE>::EXTERNAL_BANKS', 'title': 'Другие банки'}]}})
        result = read_history(value)
        observations = result['source_account_observations']
        self.assertEqual(observations['input_count'], 2)
        self.assertEqual(len(observations['excluded']), 2)
        self.assertEqual(observations['rejected'], [])
        self.assertTrue(result['ready'])

    def test_changed_filter_names_and_balances_remain_separate_observations(self):
        value = snapshot(detail())
        value['pages'][0]['sourceFilter'] = {'options': [
            {'filterId': '<SOURCE>::SAVINGS_ACCOUNT_sa_same', 'title': 'Первое имя', 'subtitle': '10 ₽'}]}
        value['pages'].append({'items': [], 'pendingItems': [], 'cursor': None,
                              'sourceFilter': {'options': [
            {'filterId': '<SOURCE>::SAVINGS_ACCOUNT_sa_same', 'title': 'Другое имя', 'subtitle': '11 ₽'}]}})
        result = read_history(value)['source_account_observations']
        self.assertEqual([r['name'] for r in result['records']], ['Первое имя', 'Другое имя'])
        self.assertEqual([r['balance'] for r in result['records']], ['10', '11'])
        self.assertEqual([r['page_index'] for r in result['records']], [0, 1])

    def test_filter_evidence_does_not_silently_replace_historical_account_attribution(self):
        row = detail(additionalFieldsList=[], amount={'money': {
            'amount': '20', 'currency': 'RUB', 'agreementId': 'sa_same'}, 'plus': None})
        value = snapshot(row)
        value['pages'][0]['sourceFilter'] = {'options': [
            {'filterId': '<SOURCE>::SAVINGS_ACCOUNT_sa_same', 'title': 'Округление', 'subtitle': '10 ₽'}]}
        result = read_history(value)
        self.assertIsNone(result['records'][0]['account'])
        self.assertEqual(result['source_account_observations']['records'][0]['name'], 'Округление')

    def test_invalid_filter_values_are_visible_without_losing_read_operations(self):
        value = snapshot(detail())
        value['pages'][0]['sourceFilter'] = {'options': [
            {'filterId': '<SOURCE>::SAVINGS_ACCOUNT_sa_save', 'title': 'Сейв', 'subtitle': 'unknown'}]}
        result = read_history(value)
        observations = result['source_account_observations']
        self.assertEqual(observations['input_count'], 1)
        self.assertEqual(len(observations['rejected']), 1)
        self.assertEqual(len(result['records']), 1)
        self.assertFalse(result['ready'])

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
            {'name': 'Пополнение', 'value': 'На На подарок'}]}], direction='CREDIT')
        self.assertEqual(read_history(snapshot(r))['records'][0]['account'], 'На подарок')

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
