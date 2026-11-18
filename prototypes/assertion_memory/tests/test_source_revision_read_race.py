"""A standalone current read cannot mix two committed interpretation sets."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.query_snapshot import capture
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory import source_revision


class SourceRevisionReadRaceTest(unittest.TestCase):
    def fixture(self, folder):
        reader=MemoryStore(Path(folder)/'memory.sqlite3')
        self.addCleanup(reader.close)
        queue=CaptureQueue(reader)
        event=queue.capture_event('fiction','one','The fictional lamp is green.',actor='Noor',
                                  source='fixture://lamp',scope='garden',recorded_at='2026-01-01')
        def add(store,key):
            store.append_assertion(assertion_id=key,text='Fictional lamp '+key,actor='Noor',
                source='fixture://lamp',episode='fiction',scope='garden',recorded_at='2026-01-01')
        add(reader,'old');add(reader,'direct')
        reader.append_edge(edge_id='old-edge',from_id='old',to_id='direct',kind='depends_on',recorded_at='2026-01-01')
        with reader.transaction(write=True) as connection:
            connection.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                (event.id,'legacy','2026-01-02T00:00:00.000000+00:00',json.dumps({'assertion_ids':['old']})))
        writer=MemoryStore(reader.path)
        self.addCleanup(writer.close)
        def publish():
            with writer.transaction(write=True) as connection:
                add(writer,'new')
                writer.append_edge(edge_id='new-edge',from_id='new',to_id='direct',kind='depends_on',recorded_at='2026-01-01')
                manifest={'schema':source_revision.SCHEMA,'event_id':event.id,'protocol':'fixture-v2',
                    'input_fingerprint':'same-frozen-source','status':'accepted_complete','assertion_ids':['new'],
                    'reconciliation':[{'assertion_id':key,'decision':decision,'verification':{
                        'status':'accepted','verifier':'independent-fixture','reason':'Exact invented source verified.',
                        'evidence_refs':[{'event_id':event.id,'start':0,'end':len(event.prompt)}]}}
                        for key,decision in (('old','withdrawn'),('new','introduced'))]}
                connection.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                    (event.id,'revision','2026-01-03T00:00:00.000000+00:00',json.dumps({'source_revision':manifest})))
                source_revision.mark_revision_changed(connection,event.id,'2026-01-03')
        return reader,publish

    def run_race(self,read,before,after):
        with tempfile.TemporaryDirectory() as folder:
            reader,publish=self.fixture(folder)
            original=source_revision.selection
            published=[]
            def select_then_publish(*args,**kwargs):
                result=original(*args,**kwargs)
                if not published:
                    published.append(True)
                    publish()  # WAL writer must finish while the reader holds its boundary.
                return result
            with patch.object(source_revision,'selection',side_effect=select_then_publish):
                value=read(reader)
            self.assertEqual(published,[True])
            self.assertIn(value,(before,after),'current read mixed old membership with newly published rows')
            self.assertEqual(read(reader),after)

    def test_list_assertions_keeps_one_committed_membership(self):
        self.run_race(lambda store:{row.assertion_id for row in store.list_assertions()},
                      {'old','direct'},{'new','direct'})

    def test_fts_before_limit_keeps_one_committed_membership(self):
        self.run_race(lambda store:{hit.assertion.assertion_id for hit in store.search('lamp',limit=10)},
                      {'old','direct'},{'new','direct'})

    def test_edges_keep_one_committed_endpoint_membership(self):
        self.run_race(lambda store:{row.edge_id for row in store.list_edges()},
                      {'old-edge'},{'new-edge'})

    def test_coverage_count_keeps_one_committed_membership(self):
        self.run_race(lambda store:store.scope_version('garden',follow_kinds='depends_on'),3,3)

    def test_nested_engine_snapshot_keeps_original_watermarks(self):
        self.run_race(lambda store:set(capture(MemoryEngine(store)).assertions),
                      {'old','direct'},{'new','direct'})


if __name__=='__main__':
    unittest.main()
