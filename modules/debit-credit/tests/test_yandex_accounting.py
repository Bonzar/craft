"""Ожидаемые записи строятся по правилам, связи округлений остаются предложениями."""

import copy
import unittest

from adapters.yandex_accounting import expand_accounting
import adapters.yandex_accounting as accounting
from adapters.matching import compare_records
from test_matching import entry, marker
from test_matching import operation
from test_source_resolutions import snapshot


def purchase(identifier='buy', **changes):
    return operation(identifier, **{'account': 'Карта Пэй', 'description': 'Магазин',
                                   'kind': None, 'cashback_hint': '20', **changes})


def rounding(identifier, account, other, amount, **changes):
    return operation(identifier, account=account, other_account=other, amount=amount,
                     kind='transfer_leg', bank_fields=[['Округление', 'С карты Пэй']],
                     timestamp='2026-07-04T10:00:05+03:00', **changes)


class YandexAccountingTest(unittest.TestCase):
    def test_cashback_does_not_claim_the_purchase_comment_as_its_own(self):
        original = snapshot(purchase(bank_comment='Комментарий покупки'))
        result = expand_accounting(original, parent_window_seconds=120)
        self.assertEqual(result['records'][0]['bank_comment'], 'Комментарий покупки')
        self.assertIsNone(result['records'][1].get('bank_comment'))

    def cashback_comparison(self, preliminary, amount='18'):
        source = expand_accounting(snapshot(purchase(cashback_cleared=not preliminary,
                                                     cashback_hint=amount)), parent_window_seconds=120)
        ledger = entry(account='ЯПлюс Баллы', amount='20', kind='income', cleared=False,
                       timestamp='2026-07-04T10:02:00+03:00',
                       markers=[marker('buy', role='cashback')], splits=[], eligible_for_matching=True)
        mapping = {'yabank': {'ЯПлюс Баллы': {'account': 'ЯПлюс Баллы',
                                            'source_currency': 'YPLUS_POINTS', 'currency': 'RUB'}}}
        return compare_records(source['records'][1:], [ledger], mapping, window_seconds=120)

    def test_preliminary_cashback_does_not_propose_existing_amount_changes(self):
        result = accounting.plan_cashback_confirmations(self.cashback_comparison(True))
        self.assertEqual(result['updates'], [])
        self.assertEqual(result['decisions'][0]['action'], 'keep_preliminary')

    def test_confirmed_cashback_proposes_amount_and_completion_on_marked_record(self):
        result = accounting.plan_cashback_confirmations(self.cashback_comparison(False))
        self.assertEqual(result['updates'], [{'locator': 1, 'set': {'amount': '18', 'cleared': True}}])
        self.assertEqual(result['decisions'][0]['action'], 'confirm_existing')

    def test_ambiguous_cashback_marker_does_not_propose_changes(self):
        comparison = self.cashback_comparison(False)
        comparison['records'][0]['match'] = 'marker_conflict'
        result = accounting.plan_cashback_confirmations(comparison)
        self.assertEqual(result['updates'], [])
        self.assertEqual(result['decisions'][0]['action'], 'requires_review')

    def test_preliminary_cashback_is_pending_even_when_purchase_is_completed(self):
        result = expand_accounting(snapshot(purchase(cashback_cleared=False)), parent_window_seconds=120)
        credit = result['records'][1]
        self.assertEqual((credit['status'], credit['cleared']), ('pending', False))
        self.assertEqual(credit['issues'], [])

    def test_confirmed_cashback_keeps_identity_and_uses_final_amount(self):
        pending = expand_accounting(snapshot(purchase(cashback_cleared=False, cashback_hint='20')),
                                    parent_window_seconds=120)['records'][1]
        final = expand_accounting(snapshot(purchase(cashback_cleared=True, cashback_hint='18')),
                                  parent_window_seconds=120)['records'][1]
        self.assertEqual((pending['source_id'], pending['role']), (final['source_id'], final['role']))
        self.assertEqual((final['amount'], final['status'], final['cleared']), ('18', 'completed', True))

    def test_cashback_has_purchase_identity_with_own_role_and_two_minute_shift(self):
        source = snapshot(purchase())
        original = copy.deepcopy(source)
        result = expand_accounting(source, parent_window_seconds=120)
        cashback = result['records'][1]
        self.assertEqual((cashback['source_id'], cashback['role']), ('buy', 'cashback'))
        self.assertEqual(cashback['timestamp'], '2026-07-04T10:02:00+03:00')
        self.assertEqual((cashback['account'], cashback['currency'], cashback['amount']),
                         ('ЯПлюс Баллы', 'YPLUS_POINTS', '20'))
        self.assertEqual(cashback['category_path'], ['Баллы'])
        self.assertEqual(cashback['kind'], 'income')
        self.assertEqual(result['source_input_count'], 1)
        self.assertEqual(result['input_count'], 2)
        self.assertEqual(result['origins'][0]['record_indices'], [0, 1])
        self.assertEqual(source, original)

    def test_standalone_points_are_not_merged_with_derived_cashback(self):
        points = operation('points', account='ЯПлюс Баллы', amount='20', currency='YPLUS_POINTS')
        result = expand_accounting(snapshot(purchase(), points), parent_window_seconds=120)
        self.assertEqual(len(result['records']), 3)
        self.assertEqual(result['records'][1], points)

    def test_failed_canceled_alias_and_uncertain_cashback_do_not_generate_income(self):
        for changes in ({'status': 'failed'}, {'status': 'canceled'},
                        {'resolution': {'same_operation_as': {'source': 'yabank', 'id': 'main'}}},
                        {'issues': ['cashback_status_requires_review']}):
            with self.subTest(changes=changes):
                result = expand_accounting(snapshot(purchase(**changes)), parent_window_seconds=120)
                self.assertEqual(len(result['records']), 1)
                self.assertEqual(result['derived_count'], 0)

    def test_pending_cashback_is_present_with_explicit_unknown_completion(self):
        result = expand_accounting(snapshot(purchase(status='pending', cleared=False)),
                                   parent_window_seconds=120)
        self.assertIsNone(result['records'][1]['cleared'])
        self.assertIn('cashback_completion_requires_review', result['records'][1]['issues'])

    def test_rounding_uses_own_ids_and_proposes_parent_time_without_guessing_category(self):
        expense = rounding('round-out', 'Карта Пэй', 'Округление', '-7')
        income = rounding('round-in', 'Округление', 'Карта Пэй', '7')
        buy = purchase(cashback_hint=None)
        result = expand_accounting(snapshot(buy, expense, income), parent_window_seconds=120)
        out, incoming = result['records'][1:]
        self.assertEqual([out['source_id'], incoming['source_id']], ['round-out', 'round-in'])
        self.assertEqual([out['kind'], incoming['kind']], ['expense', 'income'])
        self.assertIsNone(out['other_account'])
        self.assertIsNone(out.get('category'))
        self.assertEqual(incoming['category_path'], ['Яндекс Пэй', 'Округление'])
        self.assertEqual(out['timestamp'], expense['timestamp'])
        candidate = out['accounting']['parent_candidates'][0]
        self.assertEqual(candidate['source_id'], 'buy')
        self.assertEqual(candidate['proposed_timestamp'], '2026-07-04T10:01:00+03:00')

    def test_same_time_purchases_are_preserved_as_multiple_parent_candidates(self):
        source = snapshot(purchase('one', cashback_hint=None), purchase('two', cashback_hint=None),
                          rounding('round', 'Карта Пэй', 'Округление', '-7'))
        result = expand_accounting(source, parent_window_seconds=120)
        self.assertEqual(len(result['records'][2]['accounting']['parent_candidates']), 2)
        self.assertIn('rounding_parent_requires_review', result['records'][2]['issues'])

    def test_ordinary_transfer_to_rounding_is_not_reclassified_without_bank_field(self):
        row = rounding('transfer', 'Карта Пэй', 'Округление', '-100')
        row['bank_fields'] = [['Перевод', 'С карты Пэй']]
        result = expand_accounting(snapshot(row), parent_window_seconds=120)
        self.assertEqual(result['records'][0], row)

    def test_invalid_hint_or_repeated_expansion_is_rejected(self):
        with self.assertRaises(ValueError):
            expand_accounting(snapshot(purchase(cashback_hint='NaN')), parent_window_seconds=120)
        expanded = expand_accounting(snapshot(purchase()), parent_window_seconds=120)
        with self.assertRaises(ValueError):
            expand_accounting(expanded, parent_window_seconds=120)

    def test_derived_credit_does_not_claim_purchase_account_evidence_or_confirmation(self):
        row = purchase(bank_account_ids=['card-agreement'], bank_fields=[['Способ оплаты', 'Карта Пэй']],
                       resolution={'set': {'account': 'Карта Пэй'}, 'reason': 'Only purchase'},
                       account_evidence={'kind': 'receipt_agreement'})
        derived = expand_accounting(snapshot(row), parent_window_seconds=120)['records'][1]
        self.assertEqual(derived['bank_account_ids'], [])
        self.assertEqual(derived['bank_fields'], [])
        self.assertNotIn('resolution', derived)
        self.assertNotIn('account_evidence', derived)
