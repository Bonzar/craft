"""Source topic materializations are distinct from valid-time row filtering."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from test_service import fixture,EARLY
from apps.graph_viewer.server.projection import prepare
from apps.graph_viewer.server.providers.assertion_memory import AssertionMemorySource


class TopicContract(unittest.TestCase):
    def test_matching_historical_materialization_and_current_freshness_are_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.sqlite3';fixture(path)
            with sqlite3.connect(path) as c:
                c.execute('CREATE TABLE memory_historical_checks(view_id TEXT,valid_cutoff TEXT,recorded_cutoff TEXT,pipeline_key TEXT,version_id INTEGER,source_version INTEGER)')
                c.execute('INSERT INTO memory_view_versions VALUES(?,?,?,?)',(3,'parent',json.dumps({'created_at':EARLY,'evidence':[{'id':'unicode'}]}),EARLY))
                c.execute('INSERT INTO memory_historical_checks VALUES(?,?,?,?,?,?)',('parent','2025-02-01T00:00:00.000000+00:00','','test',3,1))
            source=AssertionMemorySource(path)
            current=prepare(source,Path(tmp)/'cache',{'mode':'current','at':None,'history':True,'undated':'separate'})
            parent=next(t for t in current.topics()['topics'] if t['id']=='topic:parent')
            self.assertEqual(parent['freshness'],'current')
            historical=prepare(source,Path(tmp)/'cache',{'mode':'known_now','at':'2025-02-01T00:00:00.000000+00:00','history':True,'undated':'separate'})
            topics=historical.topics(); parent=next(t for t in topics['topics'] if t['id']=='topic:parent')
            self.assertEqual(parent['source_ref']['version_id'],3)
            membership=next(m for m in topics['memberships'] if m['topic_index']==parent['index'])
            with historical.connect() as c:
                ids=[c.execute('SELECT id FROM nodes WHERE ordinal=?',(n,)).fetchone()[0] for n in membership['node_indices']]
            self.assertEqual(ids,['unicode'])
            source.close()
