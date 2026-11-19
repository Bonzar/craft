import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

SCRIPTS=Path(__file__).resolve().parents[2]/'scripts'
def load(name):
    spec=importlib.util.spec_from_file_location(name,SCRIPTS/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
fixture=load('fixture');live=load('live_fixture')


class LiveFixtureTests(unittest.TestCase):
    def test_probe_commits_real_source_rows_and_cleanup_restores_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest=fixture.generate_fixture(Path(directory)/'synthetic',nodes=80,edges=120)
            manifest['writable_live_probes']=True
            probe=live.publish(manifest,supersede=True)
            with sqlite3.connect(manifest['database']) as db:
                self.assertEqual(db.execute('select count(*) from memory_assertions').fetchone()[0],81)
                self.assertEqual(db.execute('select kind from memory_edges where edge_id=?',(probe['edge_id'],)).fetchone()[0],'supersedes')
            self.assertGreater(probe['source_ready_epoch_ms'],0)
            live.cleanup(manifest,probe)
            with sqlite3.connect(manifest['database']) as db:
                self.assertEqual(db.execute('select count(*) from memory_assertions').fetchone()[0],80)
                self.assertEqual(db.execute('select count(*) from memory_edges').fetchone()[0],120)
                self.assertEqual(fixture.table_digest(db,'memory_assertions','*','assertion_id'),manifest['digests']['assertions'])
                self.assertEqual(fixture.table_digest(db,'memory_edges','*','edge_id'),manifest['digests']['edges'])
                self.assertEqual(db.execute('pragma foreign_key_check').fetchall(),[])

    def test_probe_refuses_a_baseline_fixture_without_explicit_write_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest=fixture.generate_fixture(Path(directory)/'synthetic',nodes=80,edges=120)
            with self.assertRaises(ValueError):
                live.publish(manifest)

    def test_probe_refuses_a_non_fixture_source(self):
        with self.assertRaises(ValueError):
            live.connect({'database':'/private/tmp/not-a-fixture.sqlite3','synthetic':False})


if __name__=='__main__':unittest.main()
