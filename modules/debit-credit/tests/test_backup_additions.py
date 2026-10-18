"""Добавление явных операций в отдельную копию с точными остатками и связями."""

import copy
import unittest

from adapters.backup_edit import backup_fingerprint, prepare_backup
from adapters.debit_credit_backup import read_backup
import test_backup_edit


def addition(key='buy', **changes):
    return {'key': key, 'peer_key': None, 'kind': 'expense', 'account': 'Карта',
            'currency': 'RUB', 'amount': '-2.50', 'timestamp': '2026-07-04T10:00:00+03:00',
            'cleared': True, 'description': 'Покупка', 'category_path': ['Еда', 'Продукты'],
            'tag': 'Поездка', 'payee': None,
            'notes': '[dc-sync:v1 source=yabank id=' + key + ']', **changes}


class BackupAdditionsTest(unittest.TestCase):
    def setUp(self):
        fixture = test_backup_edit.BackupEditTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.source, self.target, self.db = fixture.source, fixture.target, fixture.db
        self.db.executescript('''
          ALTER TABLE ZTRANSACTION ADD Z_ENT INTEGER DEFAULT 18;
          ALTER TABLE ZTRANSACTION ADD ZCHANGETRIGGER INTEGER;
          ALTER TABLE ZTRANSACTION ADD ZORIGINATINGSOURCE INTEGER;
          ALTER TABLE ZTRANSACTION ADD ZPENDING_UPDATE INTEGER;
          ALTER TABLE ZTRANSACTION ADD ZTAX DECIMAL;
          ALTER TABLE ZACCOUNT ADD Z_OPT INTEGER DEFAULT 1;
          INSERT INTO ZACCOUNT (Z_PK,ZNAME,ZRECORDNAME,ZCURRENCY,ZCURRENT_BALANCE,
            ZCURRENT_UNCLEARED_BALANCE,ZLAST_RECONCILED_BALANCE,ZHIDDEN)
            VALUES (3,'Запас','reserve',1,0,0,0,0);
        ''')
        self.db.commit()

    def plan(self, rows):
        return {'schema_version': 1, 'source_sha256': backup_fingerprint(self.source),
                'updates': [], 'additions': rows}

    def balances(self, snapshot, name):
        row = next(a for a in snapshot['accounts'] if a['name'] == name)
        return row['balance'], row['uncleared_balance']

    def test_expense_keeps_category_tag_and_source_marker_and_updates_balances(self):
        before = backup_fingerprint(self.source)
        report = prepare_backup(self.source, self.target, self.plan([addition()]), 'Europe/Moscow')
        snapshot = read_backup(self.target, 'Europe/Moscow')
        row = snapshot['records'][-1]
        self.assertTrue(report['ready'])
        self.assertEqual(snapshot['input_count'], 2)
        self.assertEqual(row['category_path'], ['Еда', 'Продукты'])
        self.assertEqual(row['tag'], 'Поездка')
        self.assertEqual(row['markers'][0]['id'], 'buy')
        self.assertEqual(self.balances(snapshot, 'Карта'), ('-12.75', '-12.75'))
        self.assertEqual(backup_fingerprint(self.source), before)

    def test_pending_income_changes_only_balance_including_pending(self):
        row = addition(kind='income', amount='15.00', cleared=False, category_path=[], tag=None)
        prepare_backup(self.source, self.target, self.plan([row]), 'Europe/Moscow')
        snapshot = read_backup(self.target, 'Europe/Moscow')
        self.assertEqual(self.balances(snapshot, 'Карта'), ('-10.25', '4.75'))
        self.assertFalse(snapshot['records'][-1]['cleared'])

    def test_transfer_creates_two_reciprocal_legs_and_preserves_individual_times(self):
        out = addition('out', kind='transfer_leg', peer_key='in', amount='-20', category_path=[], tag=None)
        into = addition('in', kind='transfer_leg', peer_key='out', amount='20', account='Запас',
                        timestamp='2026-07-04T10:00:06.725+03:00', category_path=[], tag=None)
        plan = self.plan([out, into])
        original_plan = copy.deepcopy(plan)
        prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
        snapshot = read_backup(self.target, 'Europe/Moscow')
        legs = [r for r in snapshot['records'] if r['kind'] == 'transfer_leg']
        self.assertEqual(len(legs), 2)
        self.assertEqual(legs[0]['linked_locator'], legs[1]['locator'])
        self.assertEqual(legs[1]['linked_locator'], legs[0]['locator'])
        self.assertEqual(legs[1]['timestamp'], '2026-07-04T10:00:06.725000+03:00')
        self.assertEqual(self.balances(snapshot, 'Карта'), ('-30.25', '-30.25'))
        self.assertEqual(self.balances(snapshot, 'Запас'), ('20', '20'))
        self.assertEqual(plan, original_plan)

    def test_two_identical_purchases_with_different_source_ids_remain_two_records(self):
        prepare_backup(self.source, self.target, self.plan([addition('one'), addition('two')]), 'Europe/Moscow')
        snapshot = read_backup(self.target, 'Europe/Moscow')
        self.assertEqual(snapshot['input_count'], 3)
        self.assertEqual(self.balances(snapshot, 'Карта'), ('-15.25', '-15.25'))

    def test_existing_or_repeated_source_marker_blocks_duplicate_addition(self):
        self.db.execute('UPDATE ZTRANSACTION SET ZNOTES=?', (addition()['notes'],))
        self.db.commit()
        with self.assertRaises(ValueError):
            prepare_backup(self.source, self.target, self.plan([addition()]), 'Europe/Moscow')
        self.assertFalse(self.target.exists())

    def test_unknown_fields_accounts_categories_and_incomplete_transfers_are_not_guessed(self):
        cases = [addition(account='unknown'), addition(category_path=['unknown']),
                 addition(cleared=None), addition(description=''), addition(notes='no source id'),
                 addition(extra='unsupported'), addition(kind='transfer_leg', peer_key='missing'),
                 addition(amount='2.50'), addition(amount='-1.005')]
        for row in cases:
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    prepare_backup(self.source, self.target, self.plan([row]), 'Europe/Moscow')
                self.assertFalse(self.target.exists())

    def test_unbalanced_same_currency_transfer_does_not_publish_a_candidate(self):
        rows = [addition('out', kind='transfer_leg', peer_key='in', amount='-20'),
                addition('in', kind='transfer_leg', peer_key='out', amount='19', account='Запас')]
        with self.assertRaises(ValueError):
            prepare_backup(self.source, self.target, self.plan(rows), 'Europe/Moscow')
        self.assertFalse(self.target.exists())

    def test_insert_trigger_changing_old_operation_is_detected(self):
        self.db.execute('''CREATE TRIGGER unwanted AFTER INSERT ON ZTRANSACTION
          BEGIN UPDATE ZTRANSACTION SET ZNOTES='unexpected' WHERE Z_PK=1; END''')
        self.db.commit()
        with self.assertRaises(ValueError):
            prepare_backup(self.source, self.target, self.plan([addition()]), 'Europe/Moscow')
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
