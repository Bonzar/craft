"""Файловые границы CLI без записи в приложения."""

import json
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path

from test_adapters import export_csv
from test_backup import SCHEMA
from test_matching import MAPPING, entry, operation
from test_bank_api import snapshot, detail
from adapters.matching import compare_records

CLI = Path(__file__).resolve().parents[1] / 'lib' / 'cli.py'


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)],
                              capture_output=True, text=True, timeout=10)

    def test_confirmation_command_proposes_existing_updates_without_private_stdout(self):
        from test_completion import comparison
        source, output = self.directory / 'comparison.json', self.directory / 'confirmations.json'
        value = comparison()
        value['records'][0]['record']['description'] = 'private merchant'
        source.write_text(json.dumps(value))
        result = self.run_cli('plan-confirmations', source, '--output', output)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(output.read_text())
        self.assertEqual(report['updates'], [{'locator': 1, 'set': {'amount': '-18', 'cleared': True}}])
        self.assertEqual(len(report['comparison_sha256']), 64)
        self.assertFalse(report['ready'])
        self.assertNotIn('private merchant', result.stdout + result.stderr)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_single_transfer_command_hashes_inputs_without_exposing_account_names(self):
        from test_transfer_accounting import source, transfer
        from test_transfer_plan import MAPPING as TRANSFER_MAPPING
        input_path, accounts = self.directory / 'source.json', self.directory / 'accounts.json'
        output = self.directory / 'expected.json'
        input_path.write_text(json.dumps(source(transfer(description='private description'))))
        accounts.write_text(json.dumps(TRANSFER_MAPPING))
        result = self.run_cli('transfer-accounting', input_path, '--accounts', accounts,
                              '--output', output)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(output.read_text())
        self.assertEqual(len(report['records']), 2)
        self.assertFalse(report['ready'])
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(report['transfer_inputs_sha256']), {'source', 'accounts'})
        self.assertTrue(all(len(h) == 64 for h in report['transfer_inputs_sha256'].values()))
        self.assertNotIn('private description', result.stdout + result.stderr)

    def test_review_command_keeps_context_private_and_does_not_approve_writes(self):
        source, target = self.directory / 'comparison.json', self.directory / 'review.json'
        comparison = compare_records([operation(description='private merchant')], [entry()],
                                     MAPPING, window_seconds=120)
        source.write_text(json.dumps(comparison))
        result = self.run_cli('review', source, '--output', target)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(target.read_text())
        self.assertEqual(report['groups'][0]['description'], 'private merchant')
        self.assertFalse(report['ready'])
        self.assertEqual(len(report['comparison_sha256']), 64)
        self.assertNotIn('private merchant', result.stdout + result.stderr)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_resolution_command_scopes_confirmation_and_keeps_hashes(self):
        from test_source_resolutions import snapshot, resolution
        source, specs = self.directory / 'source.json', self.directory / 'specs.json'
        target = self.directory / 'resolved.json'
        row = operation(account=None, issues=['unknown_account'])
        source.write_text(json.dumps(snapshot(row)))
        specs.write_text(json.dumps([resolution(row, set={'account': 'Private Card'})]))
        result = self.run_cli('resolve-source', source, '--resolutions', specs, '--output', target)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(target.read_text())
        self.assertEqual(report['records'][0]['account'], 'Private Card')
        self.assertEqual(len(report['resolution_inputs_sha256']['source']), 64)
        self.assertEqual(len(report['resolution_inputs_sha256']['resolutions']), 64)
        self.assertNotIn('Private Card', result.stdout + result.stderr)

    def test_accounting_command_keeps_source_count_and_derived_count_separate(self):
        from test_yandex_accounting import purchase
        from test_source_resolutions import snapshot
        source, target = self.directory / 'source.json', self.directory / 'expected.json'
        source.write_text(json.dumps(snapshot(purchase())))
        result = self.run_cli('yandex-accounting', source, '--parent-window-seconds', '120',
                              '--output', target)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(target.read_text())
        self.assertEqual(report['source_input_count'], 1)
        self.assertEqual(report['derived_count'], 1)
        self.assertEqual(len(report['records']), 2)
        self.assertEqual(len(report['accounting_input_sha256']), 64)

    def test_classification_command_outputs_evidence_without_approving_operations(self):
        from adapters.review import build_review
        source, target = self.directory / 'review.json', self.directory / 'evidence.json'
        comparison = compare_records([operation()], [entry(category_path=['Продукты'])],
                                     MAPPING, window_seconds=120)
        source.write_text(json.dumps(build_review(comparison)))
        result = self.run_cli('classification-evidence', source, '--output', target)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(target.read_text())
        self.assertEqual(report['classification_profiles'][0]['category_options'][0]['value'],
                         ['Продукты'])
        self.assertFalse(report['records'][0]['classification']['approved'])
        self.assertEqual(len(report['classification_input_sha256']), 64)

    def test_movement_command_uses_explicit_period_and_does_not_confirm_balances(self):
        from test_source_resolutions import snapshot
        source, ledger = self.directory / 'source.json', self.directory / 'ledger.json'
        mapping, target = self.directory / 'map.json', self.directory / 'movement.json'
        source.write_text(json.dumps(snapshot(operation())))
        ledger.write_text(json.dumps(snapshot(entry())))
        mapping.write_text(json.dumps(MAPPING))
        result = self.run_cli('movement-control', source, '--ledger', ledger, '--accounts', mapping,
                              '--from-date', '2026-07-01', '--to-date', '2026-07-31',
                              '--timezone', 'Europe/Moscow', '--output', target)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(target.read_text())
        self.assertEqual(report['period']['end'], '2026-08-01T00:00:00+03:00')
        self.assertEqual(report['records'][0]['completed_difference'], '0')
        self.assertFalse(report['ready'])
        self.assertEqual(len(report['movement_inputs_sha256']['source']), 64)

    def test_private_csv_rows_are_written_only_to_explicit_output(self):
        source, target = self.directory / 'source.csv', self.directory / 'result.json'
        source.write_text(export_csv({'Заметки': 'private note'}), encoding='utf-8-sig')

        result = self.run_cli('debit-credit', source, '--timezone', 'Europe/Moscow', '--output', target)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('private note', result.stdout)
        report = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(report['records'][0]['notes'], 'private note')
        self.assertEqual(len(report['input_sha256']), 64)
        self.assertEqual(report['coverage'], 'unverified')

    def test_existing_output_is_not_overwritten(self):
        source, target = self.directory / 'api.json', self.directory / 'result.json'
        source.write_text(json.dumps(snapshot(detail())), encoding='utf-8')
        target.write_text('keep', encoding='utf-8')

        result = self.run_cli('yandex-bank-api', source, '--output', target)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(target.read_text(encoding='utf-8'), 'keep')

    def test_unknown_timezone_has_a_safe_error_without_traceback(self):
        source = self.directory / 'source.csv'
        source.write_text(export_csv({}), encoding='utf-8')

        result = self.run_cli('debit-credit', source, '--timezone', 'does-not-exist')

        self.assertEqual(result.returncode, 2)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('error', json.loads(result.stderr))

    def test_one_rejected_operation_stays_in_output_and_sets_failure_exit_code(self):
        source, target = self.directory / 'api.json', self.directory / 'result.json'
        bad = detail('b', amount={'money': {'amount': 'bad', 'currency': 'RUB'}, 'plus': None})
        source.write_text(json.dumps(snapshot(detail(), bad)), encoding='utf-8')

        result = self.run_cli('yandex-bank-api', source, '--output', target)

        self.assertEqual(result.returncode, 1)
        report = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(report['input_count'], 2)
        self.assertEqual(len(report['records']), 1)
        self.assertEqual(len(report['rejected']), 1)

    def test_output_with_personal_data_is_only_readable_by_its_owner(self):
        source, target = self.directory / 'api.json', self.directory / 'private.json'
        source.write_text(json.dumps(snapshot(detail())), encoding='utf-8')

        result = self.run_cli('yandex-bank-api', source, '--output', target)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_backup_command_dispatches_to_sqlite_reader(self):
        source, target = self.directory / 'copy.dcbackup', self.directory / 'copy.json'
        with closing(sqlite3.connect(source)) as db, db:
            db.executescript(SCHEMA)

        result = self.run_cli('debit-credit-backup', source, '--timezone', 'Europe/Moscow',
                              '--from-date', '2026-07-04', '--to-date', '2026-07-05', '--output', target)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(target.read_text())['adapter'], 'debit-credit-backup')

    def test_comparison_keeps_parser_rejections_and_requires_review(self):
        source, ledger = self.directory / 'source.json', self.directory / 'ledger.json'
        mapping, target = self.directory / 'map.json', self.directory / 'comparison.json'
        rejected = [{'locator': 2, 'reason': 'bad amount', 'raw': {'amount': 'private'}}]
        source.write_text(json.dumps({'records': [operation()], 'rejected': rejected,
                                     'input_count': 2, 'issues': [], 'coverage': 'unverified'}))
        ledger.write_text(json.dumps({'records': [entry()], 'rejected': [], 'input_count': 1,
                                     'issues': [], 'coverage': 'backup_snapshot'}))
        mapping.write_text(json.dumps(MAPPING))

        result = self.run_cli('compare', source, ledger, '--accounts', mapping,
                              '--window-seconds', '120', '--output', target)

        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(target.read_text())
        self.assertEqual(report['input_count'], 2)
        self.assertEqual(report['rejected'], rejected)
        self.assertEqual(report['records'][0]['match'], 'candidate')
        self.assertFalse(report['ready'])
        self.assertNotIn('private', result.stdout + result.stderr)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_comparison_rejects_snapshot_with_unaccounted_input_rows(self):
        source, mapping = self.directory / 'bad.json', self.directory / 'map.json'
        source.write_text(json.dumps({'records': [], 'rejected': [], 'input_count': 1}))
        mapping.write_text(json.dumps(MAPPING))
        result = self.run_cli('compare', source, source, '--accounts', mapping,
                              '--window-seconds', '120')
        self.assertEqual(result.returncode, 2)

    def test_bank_api_command_reads_completed_collector_file(self):
        source, target = self.directory / 'api.json', self.directory / 'parsed.json'
        source.write_text(json.dumps(snapshot(detail())))
        result = self.run_cli('yandex-bank-api', source, '--output', target)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(target.read_text())
        self.assertEqual(report['records'][0]['source_id'], 'feed-a')
        self.assertEqual(len(report['input_sha256']), 64)

    def test_import_verification_command_reads_full_backup_pair(self):
        before, after = self.directory / 'before.dcbackup', self.directory / 'after.dcbackup'
        expected, target = self.directory / 'expected.json', self.directory / 'verified.json'
        stamp = '2026-07-08T09:08:18+03:00'
        epoch = (datetime.fromisoformat(stamp) - datetime(2001, 1, 1, tzinfo=timezone.utc)).total_seconds()
        note = '[dc-sync:v1 source=yabank id=new]'
        for path, balance in ((before, 0), (after, 10)):
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(SCHEMA)
                db.execute('INSERT INTO ZCURRENCY VALUES (1,?)', ('RUB',))
                db.execute('INSERT INTO ZACCOUNT VALUES (1,?,?,1,?,?,0,0,NULL)',
                           ('Карта', 'account-id', balance, balance))
                if path == after:
                    db.execute('INSERT INTO ZTRANSACTION VALUES (1,?,1,1,10,?,?,0,0,0,NULL,NULL,NULL,NULL,NULL,NULL)',
                               ('tx-new', epoch, note))
        expected.write_text(json.dumps([{'timestamp': stamp, 'timestamp_precision': 'second',
            'amount': '10', 'currency': 'RUB', 'account': 'Карта', 'other_account': None,
            'kind': 'income', 'cleared': True, 'description': '', 'category': None, 'tag': None,
            'payee': '', 'notes': note, 'markers': [{'source': 'yabank', 'id': 'new', 'role': None}]}]))
        result = self.run_cli('verify-import', before, after, '--expected', expected,
                              '--timezone', 'Europe/Moscow', '--output', target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(target.read_text())['ready'])


if __name__ == '__main__':
    unittest.main()
