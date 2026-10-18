"""DOM-поля обезличены, структура проверена на открытых карточках банка."""

import unittest

from adapters.yandex_bank import read_cards


def dom_card(operation_id='a', **changes):
    value = {
        'url': 'https://bank.yandex.ru/my/transactions-history/operation?id=' + operation_id,
        'captured_at': '2026-10-18T12:00:00+03:00', 'timezone': 'Europe/Moscow',
        'title': 'Магазин', 'date': '17 октября 2026, 09:33', 'amount': '−300 ₽',
        'pointsAmount': False, 'transferTitles': [],
        'rows': [['Кешбэк'], ['По карте Пэй', '+3'], ['Чат с поддержкой'],
                 ['Получить справку'], ['Способ оплаты', 'Карта Пэй'],
                 ['Статус', 'В обработке'], ['Категория', 'Общественный транспорт'],
                 ['MCC', '4111']],
    }
    return {**value, **changes}


class BankDomTest(unittest.TestCase):
    def test_visible_purchase_fields_are_read_with_cashback_and_pending_status(self):
        result = read_cards([dom_card()])
        self.assertEqual(result['rejected'], [])
        row = result['records'][0]
        self.assertEqual(row['cashback_hint'], '3')
        self.assertFalse(row['cleared'])
        self.assertEqual(row['account'], 'Карта Пэй')
        self.assertEqual(row['mcc'], '4111')
        self.assertIsNone(row['category'])
        self.assertEqual(row['raw']['rows'], dom_card()['rows'])

    def test_rounding_uses_title_accounts_and_keeps_two_source_ids(self):
        rows = [['Чат с поддержкой'], ['Получить квитанцию'],
                ['Округление', 'С карты Пэй'], ['Куда', 'На Округление'],
                ['Статус', 'Выполнено']]
        outgoing = dom_card('out', title='Карта Пэй → Округление', amount='−32 ₽',
                            transferTitles=['Карта Пэй', 'Округление'], rows=rows)
        incoming = {**outgoing, 'url': dom_card('in')['url'], 'amount': '+32 ₽'}
        result = read_cards([outgoing, incoming])
        self.assertEqual(result['rejected'], [])
        self.assertEqual([r['source_id'] for r in result['records']], ['out', 'in'])
        self.assertEqual([r['account'] for r in result['records']], ['Карта Пэй', 'Округление'])

    def test_two_identical_purchases_are_not_merged(self):
        result = read_cards([dom_card('a'), dom_card('b')])
        self.assertEqual(len(result['records']), 2)
        self.assertEqual(result['issues'], [])

    def test_cashback_components_are_added_without_counting_total_twice(self):
        rows = [['Кешбэк', '+8'], ['По карте Пэй', '+3'], ['От сервиса', '+5'],
                ['Чат с поддержкой'], ['Статус', 'Выполнено'], ['Способ оплаты', 'Карта Пэй']]
        result = read_cards([dom_card(rows=rows)])
        self.assertEqual(result['records'][0]['cashback_hint'], '8')

    def test_conflicting_cashback_total_is_rejected_with_original_rows(self):
        rows = [['Кешбэк', '+7'], ['По карте Пэй', '+3'], ['От сервиса', '+5']]
        result = read_cards([dom_card(rows=rows)])
        self.assertEqual(len(result['rejected']), 1)
        self.assertEqual(result['rejected'][0]['raw']['rows'], rows)

    def test_unknown_one_line_detail_is_not_silently_dropped(self):
        result = read_cards([dom_card(rows=[['Неизвестный реквизит']])])
        self.assertEqual(len(result['rejected']), 1)

    def test_points_are_a_separate_account_without_cashback_hint(self):
        result = read_cards([dom_card(amount='+400', pointsAmount=True,
                                      rows=[['Назначение', 'Начисление баллов'],
                                            ['Статус', 'Выполнено']])])
        row = result['records'][0]
        self.assertEqual(row['currency'], 'YPLUS_POINTS')
        self.assertIsNone(row['cashback_hint'])


if __name__ == '__main__':
    unittest.main()
