"""Core receipt parity, closed failure and source-independent JSON membership."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from apps.graph_viewer.server.projection import prepare
from apps.graph_viewer.server.providers.assertion_memory import AssertionMemorySource
from revision_fixture import fixture, publish, ids, CURRENT, BEFORE, SUMMARY, logical_source


class LedgerContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.path=self.root/'source.sqlite3'; fixture(self.path)
        self.source=AssertionMemorySource(self.path)
    def tearDown(self):
        self.source.close(); self.tmp.cleanup()
    def graph(self, selection=CURRENT):
        return prepare(self.source,self.root/'cache',selection)
    def test_verified_partial_failure_empty_and_reintroduction_match_concrete_ids_and_core(self):
        cases=[([],{},'failed',{'round','green','direct'}),
               (['round'],{'round':'retained'},'unresolved',{'round','green','direct'}),
               ([],{'green':'withdrawn'},'failed',{'round','direct'}),
               (['blue'],{'blue':'introduced'},'unresolved',{'blue','round','direct'}),
               ([],{'blue':'withdrawn','round':'withdrawn'},'accepted_complete',{'direct'}),
               (['green'],{'green':'introduced'},'accepted_complete',{'green','direct'})]
        from prototypes.assertion_memory.source_revision import selection
        for n,(members,decisions,status,expected) in enumerate(cases):
            with self.subTest(status=status,decisions=decisions):
                with sqlite3.connect(self.path) as c:
                    publish(c,members,decisions,status,completed=f'2025-04-{n+1:02}T00:00:00+00:00')
                before=logical_source(self.path); graph=self.graph()
                self.assertEqual(ids(graph),expected)
                with self.source.connect() as c:
                    selected=selection(c,graph.manifest['knowledge_cutoff'])
                    all_ids={r[0] for r in c.execute('SELECT assertion_id FROM memory_assertions')}
                self.assertEqual(ids(graph),all_ids-selected.inactive_ids)
                self.assertEqual(before,logical_source(self.path))
    def test_legacy_cumulative_runs_never_replace_members_and_missing_ledger_is_unrestricted(self):
        with sqlite3.connect(self.path) as c:
            c.execute('DELETE FROM memory_extraction_runs')
            for members in [['blue'],['round']]:
                c.execute('INSERT INTO memory_extraction_runs(event_id,result,completed_at) VALUES(?,?,?)',
                          ('event',json.dumps({'assertion_ids':members}),'2025-01-01T00:00:00+00:00'))
        self.assertEqual(ids(self.graph()),{'blue','round','green','direct'})
        with sqlite3.connect(self.path) as c: c.execute('DROP TABLE memory_extraction_runs')
        self.assertEqual(ids(self.graph()),{'blue','round','green','direct'})
    def test_present_malformed_ledger_and_invalid_modern_proof_fail_closed(self):
        with sqlite3.connect(self.path) as c:
            result=json.loads(c.execute('SELECT result FROM memory_extraction_runs WHERE id=2').fetchone()[0])
            result['source_revision']['reconciliation'][0]['verification']['status']='rejected'
            c.execute('UPDATE memory_extraction_runs SET result=? WHERE id=2',(json.dumps(result),))
        with self.assertRaises(ValueError): self.graph()
        with sqlite3.connect(self.path) as c:
            c.execute('DROP TABLE memory_extraction_runs')
            c.execute('CREATE TABLE memory_extraction_runs(id INTEGER)')
        with self.assertRaises(ValueError): self.graph()
    def test_matching_historical_materialization_is_still_stale_if_revision_removes_saved_member(self):
        with sqlite3.connect(self.path) as c:
            c.execute('CREATE TABLE memory_historical_checks(view_id TEXT,version_id INTEGER,source_version INTEGER,valid_cutoff TEXT,recorded_cutoff TEXT)')
            c.execute('INSERT INTO memory_historical_checks VALUES(?,?,?,?,?)',('view',1,1,BEFORE,''))
        graph=self.graph(dict(CURRENT,mode='known_now',at=BEFORE,undated='include'))
        topics=graph.topics()
        self.assertEqual(topics['topics'][0]['freshness'],'stale')
        self.assertEqual(topics['completeness'],'partial')
        self.assertIn('source_topic_membership_filtered_by_revision',topics['unknowns'])
        self.assertNotIn('historical_topic_membership_partial_current_materialization',topics['unknowns'])
        with graph.connect() as c: self.assertEqual(c.execute('SELECT text FROM overviews').fetchone()[0],SUMMARY)
    def test_json_provider_works_when_every_core_import_is_blocked_in_fresh_process(self):
        script='''
import importlib.abc, sys, tempfile
from pathlib import Path
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith(('prototypes.assertion_memory','assertion_memory')):
            raise AssertionError('source engine import forbidden')
sys.meta_path.insert(0,Deny())
from apps.graph_viewer.server.providers.json_snapshot import JsonSnapshotSource
from apps.graph_viewer.server.projection import prepare
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    source=JsonSnapshotSource(Path('apps/graph_viewer/tests/fixtures/independent-source.json'),root/'state')
    graph=prepare(source,root/'cache',{'mode':'current','at':None,'history':True,'undated':'separate'})
    assert graph.manifest['counts']['nodes']==6
    assert graph.watermarks['assertion_membership'][1]==source.revision()
    assert not any(k.startswith(('prototypes.assertion_memory','assertion_memory')) for k in sys.modules)
'''
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__': unittest.main()
