"""Изолированная правка копии: исходник и несогласованные поля сохраняются."""

import copy
import sqlite3
import unittest
from pathlib import Path

from adapters.backup_edit import backup_fingerprint, prepare_backup
from adapters.debit_credit_backup import read_backup
import test_backup


class BackupEditTest(unittest.TestCase):
    def setUp(self):
        fixture = test_backup.BackupTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.source, self.db = fixture.path, fixture.db
        self.db.executescript('''
          ALTER TABLE ZTRANSACTION ADD Z_OPT INTEGER DEFAULT 1;
          ALTER TABLE ZTRANSACTION ADD ZDAY REAL;
          ALTER TABLE ZTRANSACTION_DESCRIPTION ADD Z_ENT INTEGER DEFAULT 20;
          ALTER TABLE ZTRANSACTION_DESCRIPTION ADD Z_OPT INTEGER DEFAULT 1;
          ALTER TABLE ZTRANSACTION_DESCRIPTION ADD ZPENDING_UPDATE INTEGER DEFAULT 0;
          ALTER TABLE ZTRANSACTION_DESCRIPTION ADD ZTYPE INTEGER DEFAULT 0;
          ALTER TABLE ZTRANSACTION_DESCRIPTION ADD ZUSAGERATING INTEGER DEFAULT 0;
          ALTER TABLE ZTRANSACTION_DESCRIPTION ADD ZRECORDNAME TEXT;
          CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER PRIMARY KEY,Z_NAME TEXT,Z_SUPER INTEGER,Z_MAX INTEGER);
          INSERT INTO Z_PRIMARYKEY VALUES (18,'Transaction',0,1),(20,'Transaction_description',0,1);
        ''')
        # The fixture reader does not depend on the extra Core Data bookkeeping columns.
        self.db.execute('''INSERT INTO ZTRANSACTION
          (Z_PK,ZRECORDNAME,ZTYPE,ZACCOUNT,ZAMOUNT,ZDATE,ZNOTES,ZPENDING,ZRECONCILED,
           ZUPCOMING,ZDELETEAFTERDATE,ZREFERENCED_TRANSACTION,ZCATEGORY,
           ZTRANSACTION_DESCRIPTION,ZTRANSACTION_TAG,ZTRANSACTION_PAYEE)
          VALUES (1,'old',0,1,-10.25,800000000,'old note',0,0,NULL,NULL,NULL,2,1,1,NULL)''')
        self.db.commit()
        self.target = self.source.parent / 'prepared.dcbackup'

    def plan(self, **changes):
        return {'schema_version': 1, 'source_sha256': backup_fingerprint(self.source),
                'updates': [{'locator': 1, 'set': changes or {
                    'description': 'Исправленное описание',
                    'timestamp': '2026-07-04T10:00:28.725+03:00',
                    'notes': 'human note\n[dc-sync:v1 source=yabank id=real-source]'}}]}

    def test_isolated_copy_keeps_original_and_untouched_fields_and_uses_exact_changes(self):
        plan = self.plan()
        original_plan = copy.deepcopy(plan)
        original_hash = backup_fingerprint(self.source)
        before = read_backup(self.source, 'Europe/Moscow')['records'][0]
        report = prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
        after = read_backup(self.target, 'Europe/Moscow')['records'][0]
        self.assertTrue(report['ready'])
        self.assertFalse(report['app_restore_verified'])
        self.assertEqual(backup_fingerprint(self.source), original_hash)
        self.assertEqual(plan, original_plan)
        self.assertEqual(after['description'], 'Исправленное описание')
        self.assertEqual(after['timestamp'], '2026-07-04T10:00:28.725000+03:00')
        self.assertEqual(after['notes'], plan['updates'][0]['set']['notes'])
        for key in ('source_id', 'amount', 'currency', 'account', 'category', 'tag', 'cleared'):
            self.assertEqual(before[key], after[key])
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.assertFalse(Path(str(self.target) + '-wal').exists())

    def test_stale_plan_fails_before_publishing_output(self):
        plan = self.plan()
        self.db.execute('UPDATE ZTRANSACTION SET ZNOTES=?', ('changed outside plan',))
        self.db.commit()
        with self.assertRaises(ValueError):
            prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
        self.assertFalse(self.target.exists())

    def test_existing_output_and_original_path_are_never_overwritten(self):
        self.target.write_text('keep')
        with self.assertRaises((ValueError, FileExistsError)):
            prepare_backup(self.source, self.target, self.plan(), 'Europe/Moscow')
        self.assertEqual(self.target.read_text(), 'keep')
        with self.assertRaises((ValueError, FileExistsError)):
            prepare_backup(self.source, self.source, self.plan(), 'Europe/Moscow')

    def test_unsupported_financial_change_and_duplicate_target_are_rejected(self):
        for plan in [self.plan(amount='-50'), self.plan()]:
            if 'amount' not in plan['updates'][0]['set']:
                plan['updates'].append(copy.deepcopy(plan['updates'][0]))
            with self.assertRaises(ValueError):
                prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
            self.assertFalse(self.target.exists())

    def test_unknown_target_and_naive_time_are_rejected(self):
        for plan in [self.plan(), self.plan(timestamp='2026-07-04T10:00:00')]:
            if 'description' in plan['updates'][0]['set']:
                plan['updates'][0]['locator'] = 999
            with self.assertRaises(ValueError):
                prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
            self.assertFalse(self.target.exists())

    def test_new_description_is_parameterized_and_existing_metadata_is_not_renamed(self):
        name = "caption'); DELETE FROM ZACCOUNT; --"
        prepare_backup(self.source, self.target, self.plan(description=name), 'Europe/Moscow')
        result = read_backup(self.target, 'Europe/Moscow')
        self.assertEqual(len(result['accounts']), 2)
        self.assertEqual(result['records'][0]['description'], name)
        with sqlite3.connect(self.target) as db:
            self.assertEqual(db.execute('SELECT ZNAME FROM ZTRANSACTION_DESCRIPTION WHERE Z_PK=1').fetchone()[0], 'Покупка')
            self.assertEqual(db.execute('SELECT Z_MAX FROM Z_PRIMARYKEY WHERE Z_ENT=20').fetchone()[0], 2)

    def test_trigger_changing_an_unapproved_table_prevents_publication(self):
        self.db.execute('''CREATE TRIGGER unwanted AFTER UPDATE ON ZTRANSACTION
                           BEGIN UPDATE ZACCOUNT SET ZNAME='unexpected'; END''')
        self.db.commit()
        with self.assertRaises(ValueError):
            prepare_backup(self.source, self.target, self.plan(notes='new note'), 'Europe/Moscow')
        self.assertFalse(self.target.exists())

    def test_wal_content_is_included_and_source_wal_is_not_modified(self):
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA wal_autocheckpoint=0')
        self.db.execute('UPDATE ZTRANSACTION SET ZNOTES=?', ('from WAL',))
        self.db.commit()
        plan = self.plan(description='Read from WAL')
        before = backup_fingerprint(self.source)
        prepare_backup(self.source, self.target, plan, 'Europe/Moscow')
        self.assertEqual(read_backup(self.target, 'Europe/Moscow')['records'][0]['notes'], 'from WAL')
        self.assertEqual(backup_fingerprint(self.source), before)


if __name__ == '__main__':
    unittest.main()
