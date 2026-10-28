"""Выписка Сбера по карте: операции из PDF и только те списания, которых нет в истории."""

import unittest
from datetime import datetime

from adapters.sber_api import read_history
from adapters.sber_statement import read_statement_pdf
from sber_statement_pdf import build_pdf, statement_lines
from test_sber_api import CARD, CREDIT, operation, snapshot


class StatementPdfTest(unittest.TestCase):
    def test_operations_are_read_with_time_code_amount_and_full_description(self):
        statement = read_statement_pdf(build_pdf(statement_lines()))
        self.assertEqual(statement['card_suffix'], '0002')
        rows = statement['operations']
        self.assertEqual([row['amount'] for row in rows], ['10.00', '-296.16', '-9600.00'])
        self.assertEqual(datetime.fromisoformat(rows[1]['timestamp']).isoformat(), '2025-02-14T01:19:00+03:00')
        self.assertEqual(rows[1]['authorization_code'], '831544')
        self.assertEqual(rows[1]['category'], 'Прочие операции')
        self.assertEqual(rows[1]['description'], 'SBER STRAHOVANIE ZAEMSHCHIKA Списание платы за '
                                                 'страхование с карты Банка. Операция по карте ****0002')
        self.assertEqual(rows[1]['balance'], '136827.42')

    def test_not_a_statement_is_refused(self):
        with self.assertRaises(ValueError):
            read_statement_pdf(b'not a pdf')
        with self.assertRaises(ValueError):
            read_statement_pdf(build_pdf([(48, 700, 'Что-то другое')]))


class StatementMergeTest(unittest.TestCase):
    def history(self):
        transfer = operation('t1', -10, source=CARD, target=CREDIT, form='UfsTransferSelf',
                             state=('EXECUTED', 'executed'), date='18.02.2025T19:34:10')
        purchase = operation('p1', -9600, source=CREDIT, date='11.02.2025T02:41:30')
        return snapshot([transfer, purchase])

    def test_only_charges_missing_from_history_are_added(self):
        statement = read_statement_pdf(build_pdf(statement_lines()))
        result = read_history(self.history(), statements=[statement])
        added = [row for row in result['records'] if row.get('statement_only')]
        self.assertEqual(len(added), 1)
        row = added[0]
        self.assertEqual((row['account'], row['amount'], row['source']), (CREDIT['id'], '-296.16', 'sber'))
        self.assertEqual(row['description'].split(' Списание')[0], 'SBER STRAHOVANIE ZAEMSHCHIKA')
        self.assertEqual((row['status'], row['cleared']), ('completed', True))
        self.assertTrue(row['source_id'].startswith('statement:'))
        self.assertEqual(result['input_count'], len(result['records']) + len(result['rejected']))
        self.assertEqual(result['statement_checks'][0]['added'], 1)
        self.assertEqual(result['statement_checks'][0]['matched'], 2)
        self.assertTrue(result['ready'])

    def test_same_statement_read_twice_gives_the_same_operation_number(self):
        statement = read_statement_pdf(build_pdf(statement_lines()))
        first = [r for r in read_history(self.history(), statements=[statement])['records'] if r.get('statement_only')]
        second = [r for r in read_history(self.history(), statements=[statement])['records'] if r.get('statement_only')]
        self.assertEqual(first[0]['source_id'], second[0]['source_id'])

    def test_statement_of_an_unknown_card_is_not_merged(self):
        statement = read_statement_pdf(build_pdf(statement_lines('Кредитная СберКарта •• 9999')))
        result = read_history(self.history(), statements=[statement])
        self.assertFalse(any(row.get('statement_only') for row in result['records']))
        self.assertIn({'code': 'statement_card_not_in_history', 'card_suffix': '9999'}, result['issues'])
        self.assertFalse(result['ready'])


if __name__ == '__main__':
    unittest.main()
