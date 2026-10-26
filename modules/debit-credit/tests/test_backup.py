"""Чтение копии базы, включая WAL, без изменения исходных файлов."""

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from adapters.debit_credit_backup import read_backup

SCHEMA = '''
CREATE TABLE ZACCOUNT (Z_PK INTEGER PRIMARY KEY, ZNAME TEXT, ZRECORDNAME TEXT,
 ZCURRENCY INTEGER, ZCURRENT_BALANCE DECIMAL, ZCURRENT_UNCLEARED_BALANCE DECIMAL,
 ZLAST_RECONCILED_BALANCE DECIMAL, ZHIDDEN INTEGER, ZDELETED_LOCALLY INTEGER);
CREATE TABLE ZCURRENCY (Z_PK INTEGER PRIMARY KEY, ZCODE TEXT);
CREATE TABLE ZTRANSACTION (Z_PK INTEGER PRIMARY KEY, ZRECORDNAME TEXT, ZTYPE INTEGER,
 ZACCOUNT INTEGER, ZAMOUNT DECIMAL, ZDATE REAL, ZNOTES TEXT, ZPENDING INTEGER,
 ZRECONCILED INTEGER, ZUPCOMING INTEGER, ZDELETEAFTERDATE REAL,
 ZREFERENCED_TRANSACTION INTEGER, ZCATEGORY INTEGER, ZTRANSACTION_DESCRIPTION INTEGER,
 ZTRANSACTION_TAG INTEGER, ZTRANSACTION_PAYEE INTEGER);
CREATE TABLE ZTRANSACTION_CATEGORY (Z_PK INTEGER PRIMARY KEY, ZNAME TEXT, ZPARENT INTEGER);
CREATE TABLE ZTRANSACTION_DESCRIPTION (Z_PK INTEGER PRIMARY KEY, ZNAME TEXT);
CREATE TABLE ZTRANSACTION_TAG (Z_PK INTEGER PRIMARY KEY, ZNAME TEXT);
CREATE TABLE ZTRANSACTION_PAYEE (Z_PK INTEGER PRIMARY KEY, ZNAME TEXT);
CREATE TABLE ZSPLIT (Z_PK INTEGER PRIMARY KEY, ZTRANSACTION INTEGER, ZCATEGORY INTEGER,
 ZAMOUNT DECIMAL);
'''


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'data.dcbackup'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA)
        self.db.executemany('INSERT INTO ZCURRENCY VALUES (?,?)', [(1, 'RUB'), (2, 'EUR')])
        self.db.executemany('INSERT INTO ZACCOUNT VALUES (?,?,?,?,?,?,?,?,?)', [
            (1, 'Карта', 'account-rub', 1, '-10.25', '-10.25', '0', 0, None),
            (2, 'Наличные', 'account-eur', 2, '0', '0', '0', 0, None)])
        self.db.executemany('INSERT INTO ZTRANSACTION_CATEGORY VALUES (?,?,?)', [
            (1, 'Еда', None), (2, 'Продукты', 1), (3, 'Кофейни', 1)])
        self.db.execute('INSERT INTO ZTRANSACTION_DESCRIPTION VALUES (1,?)', ('Покупка',))
        self.db.execute('INSERT INTO ZTRANSACTION_TAG VALUES (1,?)', ('Поездка',))
        self.db.commit()

    def insert(self, key=1, *, amount='-10.25', account=1, kind=0, pending=0,
               linked=None, date='2026-07-04T10:00:28+03:00', category=2, upcoming=None):
        timestamp = (datetime.fromisoformat(date) - datetime(2001, 1, 1, tzinfo=timezone.utc)).total_seconds()
        self.db.execute('INSERT INTO ZTRANSACTION VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (key, f'transaction-{key}', kind, account, amount, timestamp,
                         'Заметка', pending, 0, upcoming, None, linked, category, 1, 1, None))
        self.db.commit()

    def test_stable_id_currency_status_and_category_path_are_read(self):
        self.insert(pending=1)

        result = read_backup(self.path, 'Europe/Moscow')

        row = result['records'][0]
        self.assertEqual(row['source_id'], 'transaction-1')
        self.assertEqual(row['amount'], '-10.25')
        self.assertEqual(row['currency'], 'RUB')
        self.assertFalse(row['cleared'])
        self.assertEqual(row['timestamp'], '2026-07-04T10:00:28+03:00')
        self.assertEqual(row['category_path'], ['Еда', 'Продукты'])
        self.assertEqual(row['tag'], 'Поездка')
        self.assertEqual(result['accounts'][0]['balance'], '-10.25')

    def test_recurring_template_link_is_visible_in_review_without_assigning_a_provider(self):
        from adapters.matching import compare_records
        from adapters.review import build_review
        self.insert()
        self.insert(2)
        self.db.execute('ALTER TABLE ZTRANSACTION ADD ZRECURRING_TRANSACTION INTEGER')
        self.db.execute('UPDATE ZTRANSACTION SET ZRECURRING_TRANSACTION=95 WHERE Z_PK=1')
        self.db.commit()
        result = read_backup(self.path, 'Europe/Moscow')
        self.assertTrue(result['recurring_links_available'])
        self.assertEqual([r['recurring_template_locator'] for r in result['records']], [95, None])
        review = build_review(compare_records([], result['records'], {}, window_seconds=120))
        self.assertEqual(review['unreferenced_ledger'][0]['recurring_template_locator'], 95)
        self.assertEqual(result['records'][0]['account'], 'Карта')
        self.assertTrue(result['ready'])

    def test_legacy_backup_does_not_claim_absence_of_a_recurring_template(self):
        self.insert()
        result = read_backup(self.path, 'Europe/Moscow')
        self.assertFalse(result['recurring_links_available'])
        self.assertNotIn('recurring_template_locator', result['records'][0])
        self.assertTrue(result['ready'])

    def test_unrecognized_recurring_link_keeps_the_financial_record_for_review(self):
        self.insert()
        self.db.execute('ALTER TABLE ZTRANSACTION ADD ZRECURRING_TRANSACTION INTEGER')
        self.db.execute("UPDATE ZTRANSACTION SET ZRECURRING_TRANSACTION='unrecognized'")
        self.db.commit()
        result = read_backup(self.path, 'Europe/Moscow')
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['rejected'], [])
        self.assertEqual(result['records'][0]['amount'], '-10.25')
        self.assertIn('recurring_link_requires_review', result['records'][0]['issues'])
        self.assertFalse(result['ready'])

    def test_empty_backup_still_reports_available_recurring_link_metadata(self):
        self.db.execute('ALTER TABLE ZTRANSACTION ADD ZRECURRING_TRANSACTION INTEGER')
        self.db.commit()
        result = read_backup(self.path, 'Europe/Moscow')
        self.assertTrue(result['recurring_links_available'])
        self.assertEqual(result['records'], [])

    def test_foreign_transfer_halves_keep_their_ids_and_other_accounts(self):
        self.insert(kind=2, amount='-1000', linked=2, category=None)
        self.insert(2, kind=2, amount='10', account=2, linked=1, category=None)

        rows = read_backup(self.path, 'Europe/Moscow')['records']

        self.assertEqual([r['other_account'] for r in rows], ['Наличные', 'Карта'])
        self.assertEqual([r['linked_id'] for r in rows], ['transaction-2', 'transaction-1'])
        self.assertEqual([r['currency'] for r in rows], ['RUB', 'EUR'])

    def test_split_categories_are_preserved_individually(self):
        self.insert(category=None)
        self.db.executemany('INSERT INTO ZSPLIT VALUES (?,?,?,?)',
                            [(1, 1, 2, '-8'), (2, 1, 3, '-2.25')])
        self.db.commit()

        row = read_backup(self.path, 'Europe/Moscow')['records'][0]

        self.assertEqual([s['category_path'] for s in row['splits']],
                         [['Еда', 'Продукты'], ['Еда', 'Кофейни']])
        self.assertEqual([s['amount'] for s in row['splits']], ['-8', '-2.25'])

    def test_period_is_inclusive_in_the_display_timezone(self):
        self.insert(date='2026-07-04T00:00:00+03:00')
        self.insert(2, date='2026-07-05T23:59:59+03:00')
        self.insert(3, date='2026-07-06T00:00:00+03:00')

        result = read_backup(self.path, 'Europe/Moscow', '2026-07-04', '2026-07-05')

        self.assertEqual(len(result['records']), 2)
        self.assertEqual(result['date_filter'], {'from_date': '2026-07-04', 'to_date': '2026-07-05'})

    def test_wal_is_included_without_changing_original_database_or_wal(self):
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA wal_autocheckpoint=0')
        self.insert()
        files = [self.path, Path(str(self.path) + '-wal')]
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]

        result = read_backup(self.path, 'Europe/Moscow')

        self.assertEqual(len(result['records']), 1)
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in files])

    def test_missing_transfer_peer_is_reported_without_discarding_other_rows(self):
        self.insert(kind=2, linked=999)
        self.insert(2)

        result = read_backup(self.path, 'Europe/Moscow')

        self.assertEqual(len(result['records']), 1)
        self.assertEqual(len(result['rejected']), 1)
        self.assertEqual(result['input_count'], 2)

    def test_upcoming_transactions_are_not_silently_mixed_with_posted_records(self):
        self.insert(upcoming=1)

        row = read_backup(self.path, 'Europe/Moscow')['records'][0]

        self.assertTrue(row['upcoming'])
        self.assertFalse(row['eligible_for_matching'])

    def test_sqlite_binary_noise_is_removed_but_original_amount_is_preserved(self):
        self.insert(amount='-936.81000000000006')

        row = read_backup(self.path, 'Europe/Moscow')['records'][0]

        self.assertEqual(row['amount'], '-936.81')
        # Текст дробного числа зависит от версии SQLite: шум виден не везде.
        stored = self.db.execute('SELECT CAST(ZAMOUNT AS TEXT) FROM ZTRANSACTION').fetchone()[0]
        self.assertEqual(row['raw']['amount'], stored)

    def test_real_subcent_amount_is_preserved_and_requires_review(self):
        self.insert(amount='-1.005')

        result = read_backup(self.path, 'Europe/Moscow')

        self.assertEqual(result['records'][0]['amount'], '-1.005')
        self.assertFalse(result['ready'])
        self.assertIn('amount_precision_requires_review', {i['code'] for i in result['issues']})

    def test_unknown_pending_flag_remains_unknown_and_blocks_matching(self):
        self.insert(pending=None)

        result = read_backup(self.path, 'Europe/Moscow')

        self.assertIsNone(result['records'][0]['cleared'])
        self.assertFalse(result['ready'])

    def test_restored_records_without_cloud_ids_are_preserved_without_inventing_ids(self):
        self.insert()
        self.insert(2)
        self.db.execute('UPDATE ZACCOUNT SET ZRECORDNAME=NULL')
        self.db.execute('UPDATE ZTRANSACTION SET ZRECORDNAME=NULL')
        self.db.commit()

        result = read_backup(self.path, 'Europe/Moscow')

        self.assertTrue(result['ready'])
        self.assertEqual(result['input_count'], 2)
        self.assertEqual(len(result['records']), 2)
        self.assertTrue(all(r['source_id'] is None for r in result['records']))
        self.assertTrue(all(a['id'] is None for a in result['accounts']))
        self.assertEqual({r['locator'] for r in result['records']}, {1, 2})
        self.assertEqual(result['cloud_identity'], {
            'transactions_without_id': 2, 'accounts_without_id': 2,
            'local_locators_are_snapshot_only': True})

    def test_restored_transfer_links_use_local_locators_when_cloud_ids_are_empty(self):
        self.insert(kind=2, amount='-1000', linked=2, category=None)
        self.insert(2, kind=2, amount='10', account=2, linked=1, category=None)
        self.db.execute('UPDATE ZTRANSACTION SET ZRECORDNAME=NULL')
        self.db.commit()

        result = read_backup(self.path, 'Europe/Moscow')

        self.assertTrue(result['ready'])
        self.assertEqual([r['linked_locator'] for r in result['records']], [2, 1])
        self.assertEqual([r['linked_id'] for r in result['records']], [None, None])
        self.assertEqual([r['other_account'] for r in result['records']], ['Наличные', 'Карта'])

    def test_duplicate_present_cloud_ids_still_require_review(self):
        self.insert()
        self.insert(2)
        self.db.execute('UPDATE ZTRANSACTION SET ZRECORDNAME=?', ('same-id',))
        self.db.commit()

        result = read_backup(self.path, 'Europe/Moscow')

        self.assertFalse(result['ready'])
        self.assertEqual(len(result['records']), 2)
        self.assertIn('duplicate_source_id', {i['code'] for i in result['issues']})


if __name__ == '__main__':
    unittest.main()
