"""Настройки переноса из коллекций Craft: строгие типы, без догадок."""

import unittest

from adapters.run_config import fetch_collections, parse_config

COLLECTIONS = {'accounts': 'Счета источников', 'control_points': 'Контрольные точки',
               'rules': 'Правила категорий', 'decisions': 'Решения по операциям'}


def account(title, source, history, app, *, balance='', unit='RUB', transfer=True):
    return {'id': title, 'account': title, 'properties': {
        'source': source, 'historyid': history, 'balanceid': balance, 'unit': unit,
        'appaccount': app, 'transfer': transfer}}


def collections(**changes):
    value = {
        'Счета источников': [
            account('Карта Пэй', 'Яндекс Банк', 'Карта Пэй', 'Карта Пэй', balance='CARD'),
            account('Основной', 'Т-Банк', '5448395953', 'Карта Тинь', balance='5448395953'),
            account('Сберегательный', 'Сбер', 'account:1', '', transfer=False),
            account('—', 'Нет источника', '', 'Наличные (RUB)')],
        'Контрольные точки': [{'id': 'c1', 'account': 'Карта Пэй', 'properties': {
            'date': '2026-03-02', 'bankbalance': 4959.89, 'appbalance': 4959.89}}],
        'Правила категорий': [{'id': 'r1', 'payee': 'Пятёрочка', 'properties': {
            'source': 'Яндекс Банк', 'category': 'Еда / Продукты', 'tag': '', 'approved': '2026-10-29'}}],
        'Решения по операциям': [{'id': 'd1', 'sourceid': 'op-1', 'properties': {
            'source': 'Т-Банк', 'action': 'record', 'category': 'Подарки', 'tag': 'Свадьба', 'note': ''}}]}
    value.update(changes)
    return value


class ParseConfigTest(unittest.TestCase):
    def test_accounts_points_rules_and_decisions_are_read_by_source_key(self):
        config = parse_config(collections())
        self.assertEqual(config['accounts']['yabank']['Карта Пэй'],
                         {'account': 'Карта Пэй', 'unit': 'RUB', 'balance_id': 'CARD'})
        self.assertEqual(config['accounts']['tbank']['5448395953']['account'], 'Карта Тинь')
        self.assertEqual(config['not_transferred'], {('sber', 'account:1')})
        self.assertEqual(config['control_points']['Карта Пэй'],
                         {'date': '2026-03-02', 'bank_balance': '4959.89', 'app_balance': '4959.89'})
        self.assertEqual(config['rules'][('yabank', 'пятёрочка')],
                         {'category_path': ['Еда', 'Продукты'], 'tag': None})
        self.assertEqual(config['decisions'][('tbank', 'op-1')],
                         {'action': 'record', 'category_path': ['Подарки'], 'tag': 'Свадьба'})

    def test_rows_without_a_bank_source_are_not_mapped(self):
        config = parse_config(collections())
        self.assertEqual(set(config['accounts']), {'yabank', 'tbank'})

    def test_rule_without_approval_date_is_not_used(self):
        rule = {'id': 'r2', 'payee': 'Лента', 'properties': {
            'source': 'Сбер', 'category': 'Еда', 'tag': '', 'approved': ''}}
        config = parse_config(collections(**{'Правила категорий': [rule]}))
        self.assertEqual(config['rules'], {})

    def test_repeated_account_id_or_rule_is_refused(self):
        twice = [account('A', 'Т-Банк', '1', 'Карта Тинь'), account('B', 'Т-Банк', '1', 'Тинь (кред)')]
        with self.assertRaisesRegex(ValueError, 'Повтор'):
            parse_config(collections(**{'Счета источников': twice}))
        rules = collections()['Правила категорий'] * 2
        with self.assertRaisesRegex(ValueError, 'Повтор'):
            parse_config(collections(**{'Правила категорий': rules}))

    def test_unknown_values_are_refused(self):
        bad = [
            {'Счета источников': [account('A', 'Т-Банк', '1', '')]},
            {'Решения по операциям': [{'id': 'd', 'sourceid': 'x', 'properties': {
                'source': 'Сбер', 'action': 'maybe', 'category': '', 'tag': '', 'note': ''}}]},
            {'Контрольные точки': [{'id': 'c', 'account': 'A', 'properties': {
                'date': '02.03.2026', 'bankbalance': 1, 'appbalance': 1}}]},
            {'Решения по операциям': [{'id': 'd', 'sourceid': 'x', 'properties': {
                'source': 'Сбер', 'action': 'record', 'category': '', 'tag': '', 'note': ''}}]}]
        for change in bad:
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                parse_config(collections(**change))

    def test_missing_collection_is_refused(self):
        value = collections()
        del value['Контрольные точки']
        with self.assertRaisesRegex(ValueError, 'Контрольные точки'):
            parse_config(value)


class FetchCollectionsTest(unittest.TestCase):
    def test_collections_are_found_by_name_and_read_with_a_browser_agent(self):
        calls = []

        def opener(url, headers):
            calls.append((url, headers))
            if url.endswith('/collections'):
                return {'items': [{'id': 'c-' + str(i), 'name': name}
                                  for i, name in enumerate(COLLECTIONS.values())]}
            return {'items': [{'id': url}]}

        result = fetch_collections('https://connect.example/api/v1/', opener)
        self.assertEqual(set(result), set(COLLECTIONS.values()))
        self.assertEqual(calls[0][0], 'https://connect.example/api/v1/collections')
        self.assertTrue(all('Mozilla' in headers['User-Agent'] for _, headers in calls))
        self.assertEqual(result['Счета источников'], [{'id': 'https://connect.example/api/v1/collections/c-0/items'}])

    def test_two_collections_with_one_name_are_refused(self):
        def opener(url, headers):
            return {'items': [{'id': 'a', 'name': 'Счета источников'}, {'id': 'b', 'name': 'Счета источников'}]}

        with self.assertRaisesRegex(ValueError, 'Счета источников'):
            fetch_collections('https://connect.example/api/v1', opener)


if __name__ == '__main__':
    unittest.main()
