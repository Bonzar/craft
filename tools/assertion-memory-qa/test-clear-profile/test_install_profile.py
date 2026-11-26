import json
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import nullcontext
from hashlib import sha256
from types import SimpleNamespace
import sqlite3
import unittest
from unittest.mock import patch, sentinel

import install_profile as m
from test_profile_plan import EVIDENCE, ARCHIVE, REV, TAG, SCOPE, MANIFEST
from profile_plan import compact, plan

class InstallTest(unittest.TestCase):
    def test_exact_reread_open_database_is_readonly_and_fences_changed_epoch(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            path=root/'canonical.sqlite3'
            with sqlite3.connect(path) as db: db.execute('CREATE TABLE fictional(id INTEGER)')
            with patch.object(m,'TEST_DB',str(path)):
                before=m.scope()
                with sqlite3.connect(path) as db:
                    db.execute('CREATE TABLE memory_database_clear_state(singleton INTEGER,revision TEXT)')
                    db.execute('INSERT INTO memory_database_clear_state VALUES(1,?)',('a'*32,))
                self.assertNotEqual(before,m.scope())
            alias=root/'no.sqlite3'; alias.symlink_to(path)
            with patch.object(m,'TEST_DB',str(alias)), self.assertRaises(ValueError):m.scope()

    def test_commit_marker_only_after_expected_physical_test_and_graph(self):
        evidence=dict(EVIDENCE)
        seal=sha256(compact(evidence)).hexdigest()
        inputs=dict(revision=REV,archive=ARCHIVE,tag=TAG,expected_evidence_sha256=seal,expected_manifest_sha256=MANIFEST)
        pin=plan(evidence,**inputs)['marker_sha256']
        target=Path(evidence['current'])
        code={'REVISION':REV.encode(),'BASE_RELEASE_TAG':TAG.encode()}
        with patch.object(m.os,'getuid',return_value=0), patch.object(m.os,'geteuid',return_value=0), \
             patch.object(m.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=evidence['configured_uid'])), \
             patch.object(m,'activation_lock',return_value=nullcontext()) as locked, \
             patch.object(m,'JOURNAL') as journal, \
             patch.object(m.Path,'resolve',return_value=target), patch.object(m,'scope',return_value=SCOPE) as check, \
             patch.object(m,'verify_installed_sources',return_value=code), \
             patch.object(m,'verify_graph',return_value=sentinel.graph) as graph, \
             patch.object(m,'atomic_absent') as write:
            for wrong in ('f'*64,None):
                with self.assertRaises(ValueError):m.install(evidence,**inputs,expected_marker_sha256=wrong)
            write.assert_not_called()
            with self.assertRaisesRegex(ValueError,'journal'):
                m.install(evidence,**inputs,expected_marker_sha256=pin)
            write.assert_not_called()
            journal.lstat.side_effect=FileNotFoundError
            check.return_value='d'*64
            with self.assertRaises(ValueError):m.install(evidence,**inputs,expected_marker_sha256=pin)
            write.assert_not_called()
            check.return_value=SCOPE
            graph.side_effect=ValueError('Unexpected TEST Graph authority arguments')
            with self.assertRaises(ValueError):m.install(evidence,**inputs,expected_marker_sha256=pin)
            write.assert_not_called()
            graph.side_effect=None
            result=m.install(evidence,**inputs,expected_marker_sha256=pin)
            write.assert_called_once_with(Path(m.TEST_MARKER),plan(evidence,**inputs)['profile'])
            self.assertTrue(result['runtime_activation_pending'])
            self.assertFalse(result['deletion_performed'])

    def test_non_root_never_reaches_lock(self):
        with patch.object(m.os,'getuid',return_value=1), patch.object(m,'activation_lock') as lock:
            with self.assertRaises(ValueError):
                m.install({},revision=REV,archive=ARCHIVE,tag=TAG,expected_evidence_sha256='f'*64,expected_manifest_sha256=MANIFEST,
                          expected_marker_sha256='c'*64)
            lock.assert_not_called()
