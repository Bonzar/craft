"""Файловые границы CLI без записи в приложения."""

import json
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from test_adapters import card, export_csv
from test_backup import SCHEMA
from test_matching import MAPPING, entry, operation

CLI = Path(__file__).resolve().parents[1] / 'lib' / 'cli.py'


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)],
                              capture_output=True, text=True, timeout=10)

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
        source, target = self.directory / 'cards.json', self.directory / 'result.json'
        source.write_text(json.dumps([card()]), encoding='utf-8')
        target.write_text('keep', encoding='utf-8')

        result = self.run_cli('yandex-bank', source, '--output', target)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(target.read_text(encoding='utf-8'), 'keep')

    def test_unknown_timezone_has_a_safe_error_without_traceback(self):
        source = self.directory / 'source.csv'
        source.write_text(export_csv({}), encoding='utf-8')

        result = self.run_cli('debit-credit', source, '--timezone', 'does-not-exist')

        self.assertEqual(result.returncode, 2)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('error', json.loads(result.stderr))

    def test_one_rejected_card_stays_in_output_and_sets_failure_exit_code(self):
        source, target = self.directory / 'cards.json', self.directory / 'result.json'
        source.write_text(json.dumps([card(), card('b', amount='bad')]), encoding='utf-8')

        result = self.run_cli('yandex-bank', source, '--output', target)

        self.assertEqual(result.returncode, 1)
        report = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(report['input_count'], 2)
        self.assertEqual(len(report['records']), 1)
        self.assertEqual(report['rejected'][0]['raw']['amount'], 'bad')

    def test_output_with_personal_data_is_only_readable_by_its_owner(self):
        source, target = self.directory / 'cards.json', self.directory / 'private.json'
        source.write_text(json.dumps([card()]), encoding='utf-8')

        result = self.run_cli('yandex-bank', source, '--output', target)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_backup_command_dispatches_to_sqlite_reader(self):
        source, target = self.directory / 'copy.dcbackup', self.directory / 'copy.json'
        with sqlite3.connect(source) as db:
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


if __name__ == '__main__':
    unittest.main()
