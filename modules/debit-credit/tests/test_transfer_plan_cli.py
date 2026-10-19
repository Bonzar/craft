"""План из полной копии напрямую передаётся редактору, устаревший план не применяется."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import test_backup_additions
from test_source_resolutions import snapshot
from test_transfer_plan import leg
from adapters.debit_credit_backup import read_backup
from adapters.backup_edit import backup_fingerprint

CLI = Path(__file__).resolve().parents[1] / 'lib' / 'cli.py'


class TransferPlanCommandTest(unittest.TestCase):
    def setUp(self):
        fixture = test_backup_additions.BackupAdditionsTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.backup, self.db = fixture.source, fixture.db
        self.directory = self.backup.parent
        self.source, self.mapping = self.directory / 'source.json', self.directory / 'map.json'
        rows = [leg('out', account='Карта', other_account='Запас'),
                leg('in', False, account='Запас', other_account='Карта')]
        self.source.write_text(json.dumps({**snapshot(*rows), 'coverage': 'snapshot_complete'}))
        self.mapping.write_text(json.dumps({'yabank': {
            name: {'account': name, 'currency': 'RUB', 'source_currency': 'RUB'}
            for name in ('Карта', 'Запас')}}))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)],
                              capture_output=True, text=True, timeout=15)

    def proposal(self, backup, path):
        return self.run_cli('plan-transfers', self.source, '--backup', backup,
                            '--accounts', self.mapping, '--from-date', '2026-07-01',
                            '--to-date', '2026-07-31', '--timezone', 'Europe/Moscow',
                            '--pair-window-seconds', '300', '--ledger-window-seconds', '129600',
                            '--output', path)

    def test_plan_report_is_directly_consumed_and_repeated_source_produces_no_second_import(self):
        report, target = self.directory / 'proposal.json', self.directory / 'prepared.dcbackup'
        before = backup_fingerprint(self.backup)
        result = self.proposal(self.backup, report)
        self.assertEqual(result.returncode, 1, result.stderr)
        data = json.loads(report.read_text())
        self.assertEqual(data['backup_plan']['source_sha256'], before)
        result = self.run_cli('prepare-backup', self.backup, '--plan', report,
                              '--target', target, '--timezone', 'Europe/Moscow')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_backup(target, 'Europe/Moscow')['input_count'], 3)
        again = self.directory / 'again.json'
        self.assertEqual(self.proposal(target, again).returncode, 1)
        self.assertIsNone(json.loads(again.read_text())['backup_plan'])
        self.assertEqual(backup_fingerprint(self.backup), before)

    def test_error_is_reported_if_backup_changes_after_planning(self):
        report, target = self.directory / 'proposal.json', self.directory / 'prepared.dcbackup'
        self.assertEqual(self.proposal(self.backup, report).returncode, 1)
        self.db.execute("UPDATE ZTRANSACTION SET ZNOTES='Changed after planning'")
        self.db.commit()
        result = self.run_cli('prepare-backup', self.backup, '--plan', report,
                              '--target', target, '--timezone', 'Europe/Moscow')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(target.exists())
