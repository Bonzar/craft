"""Файловые границы CLI без записи в приложения."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_adapters import card, export_csv

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


if __name__ == '__main__':
    unittest.main()
