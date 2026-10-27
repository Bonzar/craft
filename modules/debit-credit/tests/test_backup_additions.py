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

    def preliminary_cashback(self):
        self.db.execute("UPDATE ZACCOUNT SET ZNAME='ЯПлюс Баллы', ZCURRENT_BALANCE=7, ZCURRENT_UNCLEARED_BALANCE=27 WHERE Z_PK=1")
        self.db.execute("UPDATE ZTRANSACTION SET ZTYPE=1,ZAMOUNT=20,ZPENDING=1,ZNOTES=? WHERE Z_PK=1",
                        ('[dc-sync:v1 source=yabank id=buy role=cashback]',))
        self.db.commit()

    def test_cashback_confirmation_updates_same_record_and_both_balance_caches(self):
        self.preliminary_cashback()
        original = backup_fingerprint(self.source)
        plan = self.plan([])
        plan['updates'] = [{'locator': 1, 'set': {'amount': '18.00', 'cleared': True}}]
        result = prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
        snapshot = read_backup(self.target, 'Europe/Moscow')
        self.assertTrue(result['ready'])
        self.assertEqual(snapshot['input_count'], 1)
        self.assertEqual(snapshot['records'][0]['source_id'], 'old')
        self.assertEqual(snapshot['records'][0]['amount'], '18')
        self.assertTrue(snapshot['records'][0]['cleared'])
        self.assertEqual(self.balances(snapshot, 'ЯПлюс Баллы'), ('25', '25'))
        self.assertEqual(backup_fingerprint(self.source), original)

    def test_marked_pending_expense_confirms_without_changing_its_metadata(self):
        self.db.execute("UPDATE ZTRANSACTION SET ZPENDING=1,ZNOTES=? WHERE Z_PK=1",
                        ('human note\n[dc-sync:v1 source=other-bank id=payment]',))
        self.db.execute('UPDATE ZACCOUNT SET ZCURRENT_BALANCE=0 WHERE Z_PK=1')
        self.db.commit()
        before = read_backup(self.source, 'Europe/Moscow')
        plan = self.plan([])
        plan['updates'] = [{'locator': 1, 'set': {'amount': '-9.75', 'cleared': True}}]
        result = prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
        after = read_backup(self.target, 'Europe/Moscow')
        self.assertTrue(result['ready'])
        self.assertEqual(after['input_count'], 1)
        self.assertEqual(self.balances(after, 'Карта'), ('-9.75', '-9.75'))
        for field in ('source_id', 'category_path', 'description', 'tag', 'notes', 'timestamp'):
            self.assertEqual(before['records'][0][field], after['records'][0][field])

    def test_pending_confirmation_cannot_reverse_sign_or_mix_metadata_edits(self):
        self.db.execute("UPDATE ZTRANSACTION SET ZPENDING=1,ZNOTES=? WHERE Z_PK=1",
                        ('[dc-sync:v1 source=other-bank id=payment]',))
        self.db.commit()
        for changes in ({'amount': '9', 'cleared': True},
                        {'amount': '0', 'cleared': True},
                        {'amount': '-9', 'cleared': True, 'notes': 'replace marker'}):
            plan = self.plan([])
            plan['updates'] = [{'locator': 1, 'set': changes}]
            with self.assertRaises(ValueError):
                prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
            self.assertFalse(self.target.exists())

    def test_pending_transfer_confirmation_updates_both_linked_legs_and_balances(self):
        outgoing = addition('out', peer_key='in', kind='transfer_leg', amount='-20',
                            category_path=[], tag=None, cleared=False)
        incoming = addition('in', peer_key='out', kind='transfer_leg', account='Запас',
                            amount='20', category_path=[], tag=None, cleared=False)
        prepared = prepare_backup(self.source, self.target, self.plan([outgoing, incoming]), 'Europe/Moscow')
        keys = prepared['new_record_locators']
        plan = {'schema_version': 1, 'source_sha256': backup_fingerprint(self.target),
                'updates': [{'locator': keys['out'], 'set': {'amount': '-18', 'cleared': True}},
                            {'locator': keys['in'], 'set': {'amount': '18', 'cleared': True}}]}
        target = self.target.with_name('confirmed.dcbackup')
        for invalid in (plan['updates'][:1],
                        [plan['updates'][0], {'locator': keys['in'], 'set': {'amount': '19', 'cleared': True}}]):
            with self.assertRaises(ValueError):
                prepare_backup(self.target, target, {**plan, 'updates': invalid}, 'Europe/Moscow')
            self.assertFalse(target.exists())
        prepare_backup(self.target, target, plan, 'Europe/Moscow')
        snapshot = read_backup(target, 'Europe/Moscow')
        self.assertEqual(snapshot['input_count'], 3)
        self.assertEqual(self.balances(snapshot, 'Карта'), ('-28.25', '-28.25'))
        self.assertEqual(self.balances(snapshot, 'Запас'), ('18', '18'))
        legs = {r['locator']: r for r in snapshot['records']}
        self.assertEqual(legs[keys['out']]['linked_locator'], keys['in'])
        self.assertEqual(legs[keys['in']]['linked_locator'], keys['out'])

    def test_pending_amount_changes_are_rejected_until_cashback_confirmation(self):
        self.preliminary_cashback()
        for changes in ({'amount': '18'}, {'amount': '18', 'cleared': False}):
            plan = self.plan([])
            plan['updates'] = [{'locator': 1, 'set': changes}]
            with self.assertRaises(ValueError):
                prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
            self.assertFalse(self.target.exists())

    def test_cashback_confirmation_and_new_pending_income_share_correct_balance_caches(self):
        self.preliminary_cashback()
        extra = addition('extra', kind='income', account='ЯПлюс Баллы', amount='5', cleared=False,
                         category_path=[], tag=None)
        plan = self.plan([extra])
        plan['updates'] = [{'locator': 1, 'set': {'amount': '18', 'cleared': True}}]
        prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
        snapshot = read_backup(self.target, 'Europe/Moscow')
        self.assertEqual(snapshot['input_count'], 2)
        self.assertEqual(self.balances(snapshot, 'ЯПлюс Баллы'), ('25', '30'))

    def test_already_completed_cashback_cannot_be_changed_by_confirmation(self):
        self.preliminary_cashback()
        self.db.execute('UPDATE ZTRANSACTION SET ZPENDING=0 WHERE Z_PK=1')
        self.db.commit()
        plan = self.plan([])
        plan['updates'] = [{'locator': 1, 'set': {'amount': '18', 'cleared': True}}]
        with self.assertRaises(ValueError):
            prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
        self.assertFalse(self.target.exists())

    def test_financial_edits_without_cashback_marker_are_rejected(self):
        self.preliminary_cashback()
        self.db.execute("UPDATE ZTRANSACTION SET ZNOTES='human note' WHERE Z_PK=1")
        self.db.commit()
        plan = self.plan([])
        plan['updates'] = [{'locator': 1, 'set': {'amount': '18', 'cleared': True}}]
        with self.assertRaises(ValueError):
            prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
        self.assertFalse(self.target.exists())

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

    def test_marker_introduced_by_an_update_cannot_also_create_a_new_operation(self):
        plan = self.plan([addition()])
        plan['updates'] = [{'locator': 1, 'set': {'notes': addition()['notes']}}]
        with self.assertRaises(ValueError):
            prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
        self.assertFalse(self.target.exists())

    def test_foreign_currency_transfer_keeps_explicit_amounts_without_currency_guessing(self):
        rows = [addition('out', kind='transfer_leg', peer_key='in', amount='-100', category_path=[], tag=None),
                addition('in', kind='transfer_leg', peer_key='out', amount='1.25', account='Наличные',
                         currency='EUR', category_path=[], tag=None)]
        prepare_backup(self.source, self.target, self.plan(rows), 'Europe/Moscow')
        result = read_backup(self.target, 'Europe/Moscow')
        self.assertEqual(self.balances(result, 'Карта'), ('-110.25', '-110.25'))
        self.assertEqual(self.balances(result, 'Наличные'), ('1.25', '1.25'))


if __name__ == '__main__':
    unittest.main()
