"""Регистрация подготовленной копии без изменения остальных настроек приложения."""

import copy
import plistlib
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from adapters.backup_edit import backup_fingerprint, prepare_backup
from adapters.backup_registry import register_backup
import test_backup_edit


class BackupRegistryTest(unittest.TestCase):
    def setUp(self):
        fixture = test_backup_edit.BackupEditTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        base = fixture.source.parent / 'Data'
        backups = base / 'Documents/backups'
        backups.mkdir(parents=True)
        self.preferences = base / 'Library/Preferences/com.mr-brightside.debitandcreditMac.plist'
        self.preferences.parent.mkdir(parents=True)
        self.source = backups / '2026-10-18T18:00:00+0300.dcbackup'
        shutil.copyfile(fixture.source, self.source)
        self.candidate = fixture.source.parent / 'candidate.dcbackup'
        plan = {'schema_version': 1, 'source_sha256': backup_fingerprint(self.source),
                'updates': [{'locator': 1, 'set': {'notes': 'corrected'}}]}
        self.report = prepare_backup(self.source, self.candidate, plan, 'Europe/Moscow')
        archive = backups.parent / 'backups-attachments'
        archive.mkdir()
        (archive / 'receipt.jpg').write_bytes(b'receipt bytes')
        self.entry = {'fileName': self.source.name, 'path': str(self.source), 'number': 1,
                      'date': datetime(2026, 10, 18, 15, 0, 0, 123456),
                      'attachments': [{'fileName': 'receipt.jpg', 'name': 'receipt',
                                       'path': '/unchanged/user/attachment.jpg', 'transaction_year': 2026}]}
        self.original = {'BackupList': [self.entry], 'other_setting': {'nested': 'keep'}}
        self.preferences.write_bytes(plistlib.dumps(self.original, fmt=plistlib.FMT_BINARY))
        self.archive = fixture.source.parent / 'original-registry.plist'
        self.stamp = datetime.fromisoformat('2026-10-18T19:00:00+03:00')
        self.running = patch('adapters.backup_registry.app_is_running', return_value=False)
        self.running.start()
        self.addCleanup(self.running.stop)

    def register(self):
        return register_backup(self.candidate, self.report, self.preferences,
                               self.source.name, self.stamp, self.archive)

    def read(self):
        return plistlib.loads(self.preferences.read_bytes())

    @unittest.skipUnless(sys.platform == 'darwin', 'Requires the macOS preferences service')
    def test_only_one_registry_entry_is_appended_with_correct_date_count_and_attachments(self):
        source_hash = backup_fingerprint(self.source)
        report = self.register()
        self.assertTrue(report['ready'])
        actual = self.read()
        self.assertEqual(actual['other_setting'], self.original['other_setting'])
        self.assertEqual(actual['BackupList'][0], self.entry)
        added = actual['BackupList'][1]
        self.assertEqual(added['date'], self.stamp.astimezone(timezone.utc).replace(tzinfo=None))
        self.assertEqual(added['number'], 1)
        self.assertEqual(added['attachments'], self.entry['attachments'])
        self.assertEqual(Path(added['path']).read_bytes(), self.candidate.read_bytes())
        self.assertEqual(backup_fingerprint(self.source), source_hash)
        self.assertEqual(plistlib.loads(self.archive.read_bytes()), {'BackupList': [self.entry]})
        self.assertEqual(self.archive.stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(added['path']).stat().st_mode & 0o777, 0o600)

    def test_running_app_blocks_registration_and_file_publication(self):
        with patch('adapters.backup_registry.app_is_running', return_value=True):
            with self.assertRaises(ValueError):
                self.register()
        self.assertEqual(self.read(), self.original)
        self.assertFalse(self.archive.exists())

    def test_changed_candidate_or_source_fingerprint_is_rejected(self):
        self.report['candidate_sha256']['database'] = '0' * 64
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(self.read(), self.original)
        self.assertFalse(self.archive.exists())

    def test_old_source_entry_is_not_used_after_a_newer_backup_has_appeared(self):
        current = copy.deepcopy(self.original)
        newer = {**self.entry, 'fileName': 'newer.dcbackup', 'date': datetime(2026, 10, 18, 15, 30)}
        current['BackupList'].append(newer)
        self.preferences.write_bytes(plistlib.dumps(current, fmt=plistlib.FMT_BINARY))
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(self.read(), current)
        self.assertFalse(self.archive.exists())

    def test_missing_archived_attachment_prevents_registration(self):
        (self.source.parent.parent / 'backups-attachments/receipt.jpg').unlink()
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(self.read(), self.original)

    def test_existing_destination_is_not_overwritten(self):
        name = self.stamp.strftime('%Y-%m-%dT%H:%M:%S%z.dcbackup')
        destination = self.source.parent / name
        destination.write_bytes(b'keep existing')
        with self.assertRaises(FileExistsError):
            self.register()
        self.assertEqual(destination.read_bytes(), b'keep existing')
        self.assertEqual(self.read(), self.original)

    def test_failed_defaults_call_is_reported_without_automatic_retry_or_secret_output(self):
        with patch('adapters.backup_registry.subprocess.run',
                   return_value=subprocess.CompletedProcess([], 1, stdout=b'', stderr=b'private error')) as run:
            report = self.register()
        self.assertFalse(report['ready'])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.read(), self.original)
        self.assertNotIn('private error', str(report))
        self.assertIn('registry_write_not_confirmed', {i['code'] for i in report['issues']})


if __name__ == '__main__':
    unittest.main()
