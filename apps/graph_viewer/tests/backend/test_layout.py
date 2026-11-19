"""Geometry is presentation math; dense source memberships need actual usable extent."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import numpy as np
from test_service import fixture,EARLY
from apps.graph_viewer.server.projection import prepare
from apps.graph_viewer.server.providers.assertion_memory import AssertionMemorySource


class LayoutContract(unittest.TestCase):
    def test_dense_members_have_volumetric_extent_and_manifest_bounds_match_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.sqlite3';fixture(path)
            with sqlite3.connect(path) as c:
                c.execute('DELETE FROM memory_edges');c.execute('DELETE FROM memory_assertions')
                rows=[(f'n{i}',f'source {i}','a','s','session',EARLY,EARLY,'s','{}') for i in range(1000)]
                c.executemany('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',rows)
                members=[{'id':row[0]} for row in rows[:-1]]
                c.execute('UPDATE memory_view_versions SET snapshot=?',(json.dumps({'created_at':EARLY,'evidence':members}),))
            source=AssertionMemorySource(path)
            graph=prepare(source,Path(tmp)/'cache',{'mode':'current','at':None,'history':False,'undated':'separate'})
            points=np.fromfile(graph.directory/'nodes.bin',dtype=np.dtype([('xyz','<f4',(3,)),('flags','<u4')]))['xyz']
            self.assertTrue((points[:-1].std(axis=0)>180).all(),points.std(axis=0))
            span=np.ptp(points,axis=0)
            declared=np.asarray(graph.manifest['bounds']['max'])-np.asarray(graph.manifest['bounds']['min'])
            self.assertTrue((declared<=span*1.03+.01).all(),(declared,span))
            distances=np.linalg.norm(points[:-1]-points[:-1].mean(axis=0),axis=1)
            self.assertGreater(np.std(distances),40,'Members must fill a volume instead of a constant-radius shell')
            source.close()
