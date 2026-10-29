"""Команда transfer-run: снимки банков и копия на входе, отдельная копия и отчёт на выходе."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import test_backup_additions
from adapters.backup_edit import backup_fingerprint
from adapters.debit_credit_backup import read_backup
from test_tbank_api import CARD, operation, snapshot

CLI = Path(__file__).resolve().parents[1] / 'lib' / 'cli.py'


def collections(rules):
    return {
        'Счета источников': [{'id': 'a', 'account': 'Основной', 'properties': {
            'source': 'Т-Банк', 'historyid': CARD, 'balanceid': CARD, 'unit': 'RUB',
            'appaccount': 'Карта'}}],
        'Контрольные точки': [],
        'Правила категорий и тегов': rules}


def rule(match, category):
    return {'id': match, 'match': match, 'properties': {
        'source': 'Т-Банк', 'by': 'продавец', 'category': category, 'tag': '', 'action': None,
        'approved': '2026-10-29'}}


class TransferRunCommandTest(unittest.TestCase):
    def setUp(self):
        fixture = test_backup_additions.BackupAdditionsTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.backup, self.directory = fixture.source, fixture.source.parent
        self.source = self.directory / 'tbank.json'
        self.source.write_text(json.dumps(snapshot([operation('p1', 120.5, description='Самокат')])))
        self.config = self.directory / 'config.json'

    def run_cli(self, backup, target, report, rules):
        self.config.write_text(json.dumps(collections(rules)))
        return subprocess.run([sys.executable, str(CLI), 'transfer-run', '--source', str(self.source),
                               '--backup', str(backup), '--target', str(target), '--config', str(self.config),
                               '--from-date', '2026-02-01', '--to-date', '2026-02-28',
                               '--timezone', 'Europe/Moscow', '--output', str(report)],
                              capture_output=True, text=True, timeout=30)

    def test_rule_purchase_lands_in_a_new_copy_and_a_repeat_adds_nothing(self):
        target, report = self.directory / 'prepared.dcbackup', self.directory / 'run.json'
        before = backup_fingerprint(self.backup)
        result = self.run_cli(self.backup, target, report, [rule('Самокат', 'Еда / Продукты')])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(report.read_text())
        self.assertEqual(data['added'], 1)
        self.assertEqual(data['review'], [])
        rows = read_backup(target, 'Europe/Moscow')['records']
        self.assertEqual(sum('id=p1]' in row['notes'] for row in rows), 1)
        self.assertEqual(backup_fingerprint(self.backup), before)
        again_target, again = self.directory / 'again.dcbackup', self.directory / 'again.json'
        result = self.run_cli(target, again_target, again, [rule('Самокат', 'Еда / Продукты')])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(again.read_text())['added'], 0)
        self.assertFalse(again_target.exists())

    def test_purchase_without_a_rule_is_reported_and_no_copy_is_made(self):
        target, report = self.directory / 'prepared.dcbackup', self.directory / 'run.json'
        result = self.run_cli(self.backup, target, report, [])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        data = json.loads(report.read_text())
        self.assertEqual([row['reason'] for row in data['review']], ['needs_category'])
        self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
