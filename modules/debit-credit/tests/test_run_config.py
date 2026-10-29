"""Настройки переноса из коллекций Craft: строгие типы, без догадок."""

import unittest

from adapters.run_config import fetch_collections, parse_config

COLLECTIONS = {'accounts': 'Счета источников', 'control_points': 'Контрольные точки',
               'rules': 'Правила категорий и тегов'}
RULES = 'Правила категорий и тегов'


def rule(match, source, *, by='продавец', category='', tag='', action=None, approved='2026-10-29'):
    return {'id': match, 'match': match, 'properties': {
        'source': source, 'by': by, 'category': category, 'tag': tag, 'action': action,
        'approved': approved}}


def account(title, source, history, app, *, balance='', unit='RUB'):
    return {'id': title, 'account': title, 'properties': {
        'source': source, 'historyid': history, 'balanceid': balance, 'unit': unit,
        'appaccount': app}}


def collections(**changes):
    value = {
        'Счета источников': [
            account('Карта Пэй', 'Яндекс Банк', 'Карта Пэй', 'Карта Пэй', balance='CARD'),
            account('Основной', 'Т-Банк', '5448395953', 'Карта Тинь', balance='5448395953'),
            account('Сберегательный', 'Сбер', 'account:1', ''),
            account('—', 'Нет источника', '', 'Наличные (RUB)')],
        'Контрольные точки': [{'id': 'c1', 'account': 'Карта Пэй', 'properties': {
            'date': '2026-03-02', 'bankbalance': 4959.89, 'appbalance': 4959.89}}],
        RULES: [rule('Пятёрочка', 'Яндекс Банк', category='Еда / Продукты'),
                rule('op-1', 'Т-Банк', by='операция', category='Подарки', tag='Свадьба'),
                rule('account:1', 'Сбер', by='счёт', action='не записывать')]}
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

    def test_rule_may_set_only_a_tag_or_only_a_category(self):
        rules = [rule('Аэрофлот', 'Т-Банк', tag='Путешествие'), rule('Лента', 'Сбер', category='Еда')]
        config = parse_config(collections(**{RULES: rules}))
        self.assertEqual(config['rules'][('tbank', 'аэрофлот')], {'category_path': [], 'tag': 'Путешествие'})
        self.assertEqual(config['rules'][('sber', 'лента')], {'category_path': ['Еда'], 'tag': None})

    def test_decision_marks_mean_skip_or_already_recorded(self):
        decisions = [rule('a', 'Сбер', by='операция', action='не записывать'),
                     rule('b', 'Сбер', by='операция', action='уже в приложении'),
                     rule('c', 'Сбер', by='операция', tag='Свадьба')]
        config = parse_config(collections(**{RULES: decisions}))
        self.assertEqual([config['decisions'][('sber', k)]['action'] for k in 'abc'], ['skip', 'same', 'record'])
        self.assertEqual(config['decisions'][('sber', 'c')]['tag'], 'Свадьба')

    def test_rows_without_a_bank_source_are_not_mapped(self):
        config = parse_config(collections())
        self.assertEqual(set(config['accounts']), {'yabank', 'tbank'})

    def test_rule_without_approval_date_is_not_used(self):
        rows = [rule('Лента', 'Сбер', category='Еда', approved=''),
                rule('op', 'Сбер', by='операция', category='Еда', approved='')]
        config = parse_config(collections(**{RULES: rows}))
        self.assertEqual((config['rules'], config['decisions']), ({}, {}))

    def test_repeated_account_id_or_rule_is_refused(self):
        twice = [account('A', 'Т-Банк', '1', 'Карта Тинь'), account('B', 'Т-Банк', '1', 'Тинь (кред)')]
        with self.assertRaisesRegex(ValueError, 'Повтор'):
            parse_config(collections(**{'Счета источников': twice}))
        rules = collections()[RULES] * 2
        with self.assertRaisesRegex(ValueError, 'Повтор'):
            parse_config(collections(**{RULES: rules}))

    def test_unknown_values_are_refused(self):
        bad = [
            {'Счета источников': [account('A', 'Т-Банк', '1', 'Карта Тинь', unit='')]},
            {RULES: [rule('account:1', 'Сбер', by='счёт', category='Еда')]},
            {RULES: [rule('x', 'Сбер', by='операция', action='maybe')]},
            {'Контрольные точки': [{'id': 'c', 'account': 'A', 'properties': {
                'date': '02.03.2026', 'bankbalance': 1, 'appbalance': 1}}]},
            {RULES: [rule('x', 'Сбер', by='операция')]},
            {RULES: [rule('x', 'Сбер', by='всегда', category='Еда')]},
            {RULES: [rule('Лента', 'Сбер', action='не записывать', category='Еда')]}]
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
