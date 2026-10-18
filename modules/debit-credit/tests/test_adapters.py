"""Поведение читающих адаптеров на обезличенных примерах."""

import csv
import io
import unittest

from adapters.debit_credit import HEADERS, read_csv
from adapters.markers import Marker, extract_markers
from adapters.yandex_bank import read_cards


def export_csv(*changes):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=HEADERS)
    writer.writeheader()
    for change in changes:
        row = dict.fromkeys(HEADERS, '')
        row.update({'Дата': '4 июля 2026\u202fг. в 17:42',
                    'Счет': 'Карта', 'Сумма': '-532,00'})
        row.update(change)
        writer.writerow(row)
    return stream.getvalue()


def card(operation_id='a', **changes):
    result = {
        'url': 'https://bank.yandex.ru/my/transactions-history/operation?id=' + operation_id,
        'captured_at': '2026-10-18T12:00:00+03:00',
        'timezone': 'Europe/Moscow',
        'title': 'Такси', 'amount': '−306 ₽',
        'date': '5 июля 2026, 16:15', 'is_points': False,
        'details': [['Статус', 'Выполнено'], ['Способ оплаты', 'Карта Пэй'],
                    ['Кешбэк', '+31'], ['MCC', '3990']],
        'transfer_accounts': [],
    }
    return {**result, **changes}


class DebitCreditTest(unittest.TestCase):
    def test_amount_is_exact_and_missing_status_stays_unknown(self):
        result = read_csv(export_csv({'Сумма': '−1\u00a0234,57'}), 'Europe/Moscow')
        row = result['records'][0]
        self.assertEqual(row['amount'], '-1234.57')
        self.assertEqual(row['timestamp'], '2026-07-04T17:42:00+03:00')
        self.assertIsNone(row['cleared'])
        self.assertIsNone(row['currency'])
        self.assertIsNone(row['source_id'])

    def test_identical_purchases_are_kept_twice(self):
        result = read_csv(export_csv({}, {}), 'Europe/Moscow')
        self.assertEqual(len(result['records']), 2)
        self.assertNotEqual(result['records'][0]['locator'], result['records'][1]['locator'])

    def test_transfer_halves_keep_different_currency_amounts(self):
        data = export_csv({'Счет': 'EUR', 'Счет-получатель перевода': 'RUB', 'Сумма': '-1500'},
                          {'Счет': 'RUB', 'Счет-получатель перевода': 'EUR', 'Сумма': '136500'})
        result = read_csv(data, 'Europe/Moscow', {'EUR': 'EUR', 'RUB': 'RUB'})
        self.assertEqual([r['kind'] for r in result['records']], ['transfer_leg'] * 2)
        self.assertEqual([r['currency'] for r in result['records']], ['EUR', 'RUB'])
        self.assertEqual([r['amount'] for r in result['records']], ['-1500', '136500'])

    def test_invalid_row_is_reported_and_other_rows_survive(self):
        result = read_csv(export_csv({}, {'Сумма': 'NaN'}, {}), 'Europe/Moscow')
        self.assertEqual(result['input_count'], 3)
        self.assertEqual(len(result['records']), 2)
        self.assertEqual(len(result['rejected']), 1)
        self.assertEqual(result['rejected'][0]['raw']['Сумма'], 'NaN')
        self.assertFalse(result['ready'])

    def test_wrong_columns_raise_an_error(self):
        with self.assertRaises(ValueError):
            read_csv('Date,Amount\n2026-01-01,-1\n', 'Europe/Moscow')

    def test_empty_list_is_not_accepted_as_account_currency_mapping(self):
        with self.assertRaises(ValueError):
            read_csv(export_csv({}), 'Europe/Moscow', [])

    def test_iso_date_and_split_category_are_preserved(self):
        category = 'Продукты (-15,00), Кофейни (-5,00)'
        result = read_csv(export_csv({'Дата': '2026-04-27 12:00:00',
                                     'Категория': category}), 'Europe/Moscow')
        self.assertEqual(result['records'][0]['category'], category)

    def test_service_marker_does_not_replace_user_notes(self):
        notes = 'Ужин\n[dc-sync:v1 source=yabank id=abc role=cashback]'
        row = read_csv(export_csv({'Заметки': notes}), 'Europe/Moscow')['records'][0]
        self.assertEqual(row['notes'], notes)
        self.assertEqual(row['markers'][0]['role'], 'cashback')


class YandexBankTest(unittest.TestCase):
    def test_purchase_keeps_opaque_id_status_and_cashback_hint(self):
        row = read_cards([card('opaque%2F123')])['records'][0]
        self.assertEqual(row['source_id'], 'opaque/123')
        self.assertEqual(row['amount'], '-306')
        self.assertEqual(row['currency'], 'RUB')
        self.assertTrue(row['cleared'])
        self.assertEqual(row['cashback_hint'], '31')
        self.assertIsNone(row['category'])

    def test_equal_purchases_with_different_ids_are_kept(self):
        rows = read_cards([card('a'), card('b')])['records']
        self.assertEqual([r['source_id'] for r in rows], ['a', 'b'])

    def test_repeated_capture_is_reported_not_silently_removed(self):
        result = read_cards([card(), card()])
        self.assertEqual(len(result['records']), 2)
        self.assertFalse(result['ready'])
        self.assertIn('duplicate_source_id', {i['code'] for i in result['issues']})

    def test_pending_and_canceled_keep_distinct_statuses(self):
        pending = card(details=[['Статус', 'В обработке'], ['Способ оплаты', 'Карта Пэй']])
        canceled = card('b', details=[['Статус', 'Не выполнено / Отмена']])
        rows = read_cards([pending, canceled])['records']
        self.assertEqual([r['status'] for r in rows], ['pending', 'canceled'])
        self.assertEqual([r['cleared'] for r in rows], [False, None])

    def test_points_income_does_not_require_a_purchase(self):
        source = card(amount='+400', is_points=True,
                      details=[['Статус', 'Выполнено'], ['Назначение', 'Начисление баллов']])
        row = read_cards([source])['records'][0]
        self.assertEqual(row['currency'], 'YPLUS_POINTS')
        self.assertEqual(row['account'], 'ЯПлюс Баллы')
        self.assertEqual(row['amount'], '400')
        self.assertNotIn('purchase_id', row)

    def test_rounding_legs_retain_their_own_ids(self):
        first = card('out', title='Округление', amount='−94 ₽',
                     transfer_accounts=['Карта Пэй', 'Округление'])
        second = card('in', title='Округление', amount='+94 ₽',
                      transfer_accounts=['Карта Пэй', 'Округление'])
        rows = read_cards([first, second])['records']
        self.assertEqual([r['source_id'] for r in rows], ['out', 'in'])
        self.assertEqual([r['account'] for r in rows], ['Карта Пэй', 'Округление'])

    def test_login_and_transfer_pages_are_rejected(self):
        for url in ('https://bank.yandex.ru/_pay/login',
                    'https://bank.yandex.ru/my/transfers?id=a',
                    'https://evil.example/my/transactions-history/operation?id=a'):
            with self.subTest(url=url):
                result = read_cards([card(url=url)])
                self.assertEqual(len(result['rejected']), 1)
                self.assertEqual(result['records'], [])

    def test_missing_id_is_not_replaced_by_a_purchase_fingerprint(self):
        result = read_cards([card(url='https://bank.yandex.ru/my/transactions-history/operation')])
        self.assertEqual(len(result['rejected']), 1)

    def test_duplicate_id_parameter_is_rejected_even_when_one_value_is_empty(self):
        result = read_cards([card(url=card()['url'] + '&id=')])
        self.assertEqual(len(result['rejected']), 1)

    def test_missing_sign_or_unknown_status_blocks_automatic_use(self):
        for change in ({'amount': '306 ₽'}, {'details': [['Статус', 'Новый статус']]}):
            with self.subTest(change=change):
                result = read_cards([card(**change)])
                self.assertFalse(result['ready'])

    def test_amount_with_space_after_sign_is_preserved(self):
        row = read_cards([card(amount='− 1\u202f234,57 ₽')])['records'][0]
        self.assertEqual(row['amount'], '-1234.57')

    def test_display_timezone_is_explicit_and_not_taken_from_the_laptop(self):
        row = read_cards([card(timezone='Europe/Istanbul')])['records'][0]
        self.assertEqual(row['timestamp'], '2026-07-05T16:15:00+03:00')
        result = read_cards([card(timezone='')])
        self.assertEqual(len(result['rejected']), 1)


class MarkerTest(unittest.TestCase):
    def test_opaque_id_round_trips_without_breaking_marker(self):
        marker = Marker('yabank', 'a b]&=/%Ж', 'cashback')
        self.assertEqual(extract_markers(marker.render()), [marker])

    def test_optional_role_and_two_source_aliases_are_preserved(self):
        note = '[dc-sync:v1 source=yabank id=a role=cashback]\n[dc-sync:v1 source=yabank-points id=b]'
        self.assertEqual(extract_markers(note),
                         [Marker('yabank', 'a', 'cashback'), Marker('yabank-points', 'b')])

    def test_malformed_marker_is_not_silently_ignored(self):
        with self.assertRaises(ValueError):
            extract_markers('[dc-sync:v1 source=yabank role=cashback]')


if __name__ == '__main__':
    unittest.main()
