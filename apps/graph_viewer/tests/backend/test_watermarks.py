"""Row watermark checks must preserve consistency without a per-row VM loop."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from test_service import fixture,EARLY
from apps.graph_viewer.server.incremental import watermarks
from apps.graph_viewer.server.providers.assertion_memory import AssertionMemorySource


class WatermarkCostContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'source.sqlite3';fixture(self.path)
    def tearDown(self):
        self.tmp.cleanup()
    def read_with_vm_budget(self):
        source=AssertionMemorySource(self.path);steps=[0]
        try:
            with source.connect() as c:
                c.execute('BEGIN');c.execute('SELECT version FROM memory_state LIMIT 1').fetchone()
                def limit():
                    steps[0]+=1000
                    return int(steps[0]>10000)
                c.set_progress_handler(limit,1000)
                try:
                    result=watermarks(c)
                except sqlite3.OperationalError as error:
                    self.fail('Watermark inspection exceeded 10000 VM steps; per-row aggregate loop: '+str(error))
                finally:
                    c.set_progress_handler(None,0)
                self.assertTrue(c.in_transaction,'All scalar checks must remain inside the caller-pinned read transaction')
                c.commit()
                return result
        finally:source.close()
    def test_edge_max_and_count_keep_fast_paths_and_detect_rowid_gaps(self):
        with sqlite3.connect(self.path) as c:
            c.executemany('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',((f'bulk-{i}','old','unicode','supports',EARLY,'{}') for i in range(50000)))
            c.execute("DELETE FROM memory_edges WHERE edge_id IN ('bulk-4','bulk-49999')")
        marks=self.read_with_vm_budget()
        self.assertEqual(marks['memory_edges'],(50002,50001))
        self.assertEqual(marks['memory_assertions'],(6,6))
    def test_large_view_version_watermarks_keep_fast_paths_too(self):
        with sqlite3.connect(self.path) as c:
            c.executemany('INSERT INTO memory_view_versions VALUES(?,?,?,?)',((i,'parent','{}',EARLY) for i in range(3,50003)))
            c.execute('DELETE FROM memory_view_versions WHERE id=5')
        marks=self.read_with_vm_budget()
        versions=next(item[1] for item in json.loads(marks['topics']) if item[0]=='memory_view_versions')
        self.assertEqual(versions,[50002,50001])

    def test_separate_scalars_still_share_the_same_pinned_wal_snapshot(self):
        with sqlite3.connect(self.path) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
        source=AssertionMemorySource(self.path);published=[]
        try:
            with source.connect() as c:
                c.execute('BEGIN');c.execute('SELECT version FROM memory_state LIMIT 1').fetchone()
                def concurrent_write(sql):
                    if sql=='SELECT count(*) FROM memory_edges' and not published:
                        with sqlite3.connect(self.path) as writer:
                            writer.execute('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',('concurrent','old','unicode','supports',EARLY,'{}'))
                            writer.execute('UPDATE memory_state SET version=version+1')
                        published.append(True)
                c.set_trace_callback(concurrent_write)
                marks=watermarks(c);c.set_trace_callback(None)
                self.assertTrue(published,'Concurrent writer must commit between the two scalar checks')
                self.assertEqual(marks['memory_edges'],(3,3))
                self.assertEqual(marks['state'],1)
                c.commit()
            with source.connect() as current:
                self.assertEqual(current.execute('SELECT count(*) FROM memory_edges').fetchone()[0],4)
        finally:source.close()
