"""Сверка содержимого восстановленной базы без привязки к внутренним ID."""

import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path

from adapters.restore_verification import verify_restoration
from test_backup import SCHEMA
from test_import_verification import leg, account


def snapshots():
    records = [leg('old-out', 'Баллы', 'Карта', '-10', 'out', 'old-in'),
               leg('old-in', 'Карта', 'Баллы', '10', 'in', 'old-out')]
    for i, row in enumerate(records):
        row.update(locator=i + 1, linked_locator=2 - i, reconciled=0, upcoming=False,
                   category_path=[], splits=[])
    accounts = [account('Баллы', '90'), account('Карта', '30')]
    for i, row in enumerate(accounts):
        row.update(pk=i + 1, hidden=0, deleted_locally=None, last_reconciled_balance='0')
    first = {'adapter': 'debit-credit-backup', 'coverage': 'backup_snapshot',
             'input_count': 2, 'records': records, 'accounts': accounts, 'rejected': [], 'issues': []}
    last = copy.deepcopy(first)
    for i, row in enumerate(last['records']):
        row.update(source_id=None, linked_id=None, locator=101 + i,
                   linked_locator=102 - i)
    for i, row in enumerate(last['accounts']):
        row.update(id=None, pk=101 + i)
    return first, last


class RestoreVerificationTest(unittest.TestCase):
    def test_all_fields_and_transfer_links_pass_with_new_local_ids_and_no_cloud_ids(self):
        first, last = snapshots()
        before = copy.deepcopy((first, last))
        result = verify_restoration(first, last)
        self.assertTrue(result['ready'])
        self.assertEqual(result['missing_record_count'], 0)
        self.assertEqual(result['unexpected_record_count'], 0)
        self.assertEqual((first, last), before)

    def test_changed_notes_category_and_time_are_reported_as_missing_and_unexpected_content(self):
        first, last = snapshots()
        last['records'][0].update(notes='changed', category='Wrong',
                                  timestamp='2026-07-08T09:09:12+03:00')
        result = verify_restoration(first, last)
        self.assertFalse(result['ready'])
        self.assertGreater(result['missing_record_count'], 0)
        self.assertGreater(result['unexpected_record_count'], 0)
        self.assertTrue(any(r['actual_count'] > r['expected_count'] for r in result['records']))

    def test_equal_rows_keep_their_multiplicity_instead_of_being_collapsed(self):
        first, last = snapshots()
        row = {**first['records'][0], 'kind': 'expense', 'other_account': None,
               'linked_id': None, 'linked_locator': None, 'notes': '', 'markers': []}
        first['records'] = [{**row, 'locator': i} for i in (1, 2)]
        last['records'] = [{**row, 'locator': 100}]
        first['input_count'], last['input_count'] = 2, 1
        result = verify_restoration(first, last)
        self.assertFalse(result['ready'])
        self.assertEqual(result['missing_record_count'], 1)
        self.assertEqual(result['unexpected_record_count'], 0)

    def test_crossed_links_between_same_amount_transfers_with_different_notes_fail(self):
        first, last = snapshots()
        for data, offset in [(first, 10), (last, 200)]:
            more = copy.deepcopy(data['records'])
            for row in more:
                row['locator'] += offset
                row['linked_locator'] += offset
                row['notes'] += ' other conversion'
            data['records'].extend(more)
            data['input_count'] = 4
        a, b, c, d = last['records']
        a['linked_locator'], d['linked_locator'] = d['locator'], a['locator']
        b['linked_locator'], c['linked_locator'] = c['locator'], b['locator']
        self.assertFalse(verify_restoration(first, last)['ready'])

    def test_missing_or_nonreciprocal_transfer_link_blocks_success(self):
        first, last = snapshots()
        last['records'][0]['linked_locator'] = 999
        result = verify_restoration(first, last)
        self.assertFalse(result['ready'])
        self.assertIn('invalid_transfer_link', {i['code'] for i in result['issues']})

    def test_balance_or_account_visibility_change_blocks_success(self):
        first, last = snapshots()
        last['accounts'][0].update(balance='100', hidden=1)
        self.assertFalse(verify_restoration(first, last)['ready'])

    def test_ambiguous_account_names_and_reader_errors_block_success(self):
        first, last = snapshots()
        last['accounts'].append(copy.deepcopy(last['accounts'][0]))
        last['issues'] = [{'code': 'reader_warning'}]
        result = verify_restoration(first, last)
        self.assertFalse(result['ready'])
        self.assertIn('ambiguous_account_identity', {i['code'] for i in result['issues']})
        self.assertIn('snapshot_reader_errors', {i['code'] for i in result['issues']})

    def test_unaccounted_or_filtered_input_cannot_prove_full_restoration(self):
        first, last = snapshots()
        last['input_count'] = 3
        with self.assertRaises(ValueError):
            verify_restoration(first, last)
        last['input_count'] = 2
        last['coverage'] = 'unverified'
        with self.assertRaises(ValueError):
            verify_restoration(first, last)

    def test_cli_reads_full_backups_and_keeps_private_notes_out_of_stdout(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as databases:
            root = Path(temp)
            paths = [root / 'expected.dcbackup', root / 'actual.dcbackup']
            for i, path in enumerate(paths):
                db = databases.enter_context(closing(sqlite3.connect(path)))
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('PRAGMA wal_autocheckpoint=0')
                with db:
                    db.executescript(SCHEMA)
                    db.execute('INSERT INTO ZCURRENCY VALUES (1,?)', ('RUB',))
                    db.execute('INSERT INTO ZACCOUNT VALUES (1,?,?,?,?,?,?,?,?)',
                               ('Карта', 'old-account' if i == 0 else None, 1, -10, -10, 0, 0, None))
                    db.execute('INSERT INTO ZTRANSACTION VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               (1, 'old-transaction' if i == 0 else None, 0, 1, -10, 800000000,
                                'private restoration note', 0, 0, None, None, None, None, None, None, None))
            target = root / 'verified.json'
            cli = Path(__file__).resolve().parents[1] / 'lib/cli.py'
            result = subprocess.run([sys.executable, str(cli), 'verify-restore', *map(str, paths),
                                     '--timezone', 'Europe/Moscow', '--output', str(target)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(target.read_text())
            self.assertTrue(report['ready'])
            self.assertEqual(len(report['inputs_sha256']['expected']), 64)
            self.assertEqual(len(report['inputs_sha256']['expected_wal']), 64)
            self.assertEqual(len(report['inputs_sha256']['actual_wal']), 64)
            self.assertNotIn('private restoration note', result.stdout + result.stderr)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
