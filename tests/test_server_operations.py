import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.server_operations import backup, restore, server_manifest, backup_manifest, write_manifest

class ServerOperationsTest(unittest.TestCase):
    def test_live_wal_backup_separate_ledger_and_restore_new_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'memory.sqlite3';ledger=root/'usage.sqlite3'
            with MemoryStore(str(source)) as store:
                store._connection.execute('PRAGMA journal_mode=WAL')
                store.append_assertion(assertion_id='synthetic',text='Synthetic fixture',actor='fixture',source='fixture://local',episode='s',recorded_at='2026-01-01')
                with sqlite3.connect(ledger) as connection:
                    connection.execute('CREATE TABLE attempts(id TEXT)');connection.execute("INSERT INTO attempts VALUES('synthetic')")
                before=hashlib.sha256(source.read_bytes()).hexdigest()
                result=backup(source,root/'copy.sqlite3',ledger_source=ledger)
                self.assertFalse(result['globally_consistent'])
                self.assertEqual(before,hashlib.sha256(source.read_bytes()).hexdigest())
                target=root/'restored.sqlite3';restore(root/'copy.sqlite3',target)
                with sqlite3.connect(target) as connection:
                    self.assertEqual(connection.execute('SELECT assertion_id FROM memory_assertions').fetchone()[0],'synthetic')
                    self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                with sqlite3.connect(result['ledger']) as connection:self.assertEqual(connection.execute('SELECT count(*) FROM attempts').fetchone()[0],1)
                self.assertEqual((root/'copy.sqlite3').stat().st_mode&0o777,0o600)
                with self.assertRaises(FileExistsError):restore(root/'copy.sqlite3',source)
                with self.assertRaises(FileExistsError):backup(source,root/'copy.sqlite3')
    def test_invalid_source_never_publishes_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'not-memory.sqlite3'
            with sqlite3.connect(source) as connection:connection.execute('CREATE TABLE unrelated(x)')
            with self.assertRaises(ValueError):backup(source,root/'copy.sqlite3')
            self.assertFalse((root/'copy.sqlite3').exists())
    def test_ledger_collision_does_not_start_knowledge_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'memory.sqlite3';ledger=root/'usage.sqlite3';target=root/'backup.sqlite3'
            with MemoryStore(str(source)),sqlite3.connect(ledger):pass
            Path(str(target)+'.model-usage.sqlite3').write_text('existing')
            with self.assertRaises(FileExistsError):backup(source,target,ledger_source=ledger)
            self.assertFalse(target.exists())
    def test_daily_backup_manifest_has_no_restarting_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'memory.sqlite3';source.touch()
            manifest=backup_manifest(source,root/'backups',python='/usr/bin/python3',repository=root,log_dir=root/'logs')
            self.assertEqual(manifest['StartCalendarInterval'],{'Hour':3,'Minute':15})
            self.assertFalse(manifest['RunAtLoad']);self.assertNotIn('KeepAlive',manifest)
            self.assertIn('.backup.',manifest['Label'])
    def test_log_symlink_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'http.json';config.write_text('{}')
            manifest=server_manifest(config,python='/usr/bin/python3',repository=root,log_dir=root/'logs')
            (root/'logs').mkdir(mode=0o700);target=root/'untouched';target.write_text('sentinel')
            Path(manifest['StandardOutPath']).symlink_to(target)
            with self.assertRaises(OSError):write_manifest(manifest,root/'manifests')
            self.assertEqual(target.read_text(),'sentinel')
    def test_manifest_is_scoped_and_logs_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'http.json';config.write_text('{}')
            manifest=server_manifest(config,python='/usr/bin/python3',repository=root,log_dir=root/'logs')
            self.assertTrue(manifest['KeepAlive']);self.assertTrue(manifest['RunAtLoad'])
            self.assertEqual(manifest['WorkingDirectory'],str(root.resolve()))
            self.assertEqual(manifest['EnvironmentVariables']['PYTHONPATH'],str(root.resolve()))
            self.assertEqual(manifest['ProgramArguments'][1:4],['-m','prototypes.assertion_memory.http_server','--config'])
            path=write_manifest(manifest,root/'manifests')
            self.assertTrue(path.exists());self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(Path(manifest['StandardOutPath']).stat().st_mode&0o777,0o600)
            self.assertEqual(write_manifest(manifest,root/'manifests'),path)
            manifest['KeepAlive']=False
            with self.assertRaises(FileExistsError):write_manifest(manifest,root/'manifests')

if __name__=='__main__':unittest.main()
