"""Append-only bounds use exact cached extrema without reading old fat rows."""
from copy import deepcopy
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from apps.graph_viewer.server.incremental import finish_counts


def expected_bounds(points):
    if not points:return {'min':[-1]*3,'max':[1]*3}
    low=[min(p[axis] for p in points) for axis in range(3)];high=[max(p[axis] for p in points) for axis in range(3)]
    pad=[max(.001,(high[axis]-low[axis])*.005) for axis in range(3)]
    return {'min':[low[i]-pad[i] for i in range(3)],'max':[high[i]+pad[i] for i in range(3)]}


class CachedBoundsContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.c=sqlite3.connect(Path(self.tmp.name)/'projection.sqlite3');self.c.row_factory=sqlite3.Row
        self.c.executescript('CREATE TABLE nodes(ordinal INTEGER PRIMARY KEY,body TEXT,x REAL,y REAL,z REAL,valid_at TEXT); CREATE TABLE edges(f INTEGER,t INTEGER,kind TEXT,metadata TEXT); CREATE INDEX node_valid_julian ON nodes(julianday(valid_at));')
        self.points=[(-10.,0.,-2.),(4.,9.,1.),(0.,-3.,5.)]+[(0.,0.,0.)]*20000
        self.c.executemany('INSERT INTO nodes VALUES(?,?,?,?,?,?)',((i,'x'*1024,*xyz,'2030-01-01T00:00:00Z' if i==0 else '2025-01-01T00:00:00Z') for i,xyz in enumerate(self.points)))
        self.old=SimpleNamespace(manifest={'counts':{'nodes':len(self.points),'edges':0,'visible_nodes':len(self.points)-3,'visible_edges':0,'eligible_before_status_filter':len(self.points),'future_valid_known_count':1,'undated_known_count':0,'excluded_unknown_valid_count':0,'topics':1},'bounds':expected_bounds(self.points),'layout':{'raw_bounds':{'min':[-10.,-3.,-2.],'max':[4.,9.,5.]}}})
        self.selection={'mode':'current','at':None,'history':False,'undated':'separate'}
    def tearDown(self):
        self.c.close();self.tmp.cleanup()
    def finish(self,added,captured='2026-01-01T00:00:00Z',budget=True):
        steps=[0]
        if budget:
            def limit():
                steps[0]+=1000;return int(steps[0]>10000)
            self.c.set_progress_handler(limit,1000)
        try:
            return finish_counts(self.c,self.old,self.selection,captured,{},added,[],(1,0),(1,0),0)
        except sqlite3.OperationalError as error:
            self.fail('Cached append bounds must not scan old projection rows: '+str(error))
        finally:self.c.set_progress_handler(None,0)
    def full_sql_bounds(self):
        row=self.c.execute('SELECT min(x),min(y),min(z),max(x),max(y),max(z) FROM nodes').fetchone()
        return expected_bounds([] if row[0] is None else [row[:3],row[3:]])
    def test_append_inside_and_outside_exact_cached_bounds_never_reads_old_rows(self):
        previous=deepcopy(self.old.manifest)
        for point in [(1.,1.,1.),(20.,-12.,7.)]:
            ordinal=len(self.points);self.c.execute('INSERT INTO nodes VALUES(?,?,?,?,?,?)',(ordinal,'new',*point,'2025-01-01T00:00:00Z'));self.points.append(point)
            result=self.finish([ordinal])
            self.assertEqual(result[1],expected_bounds(self.points));self.assertEqual(result[1],self.full_sql_bounds());self.assertEqual(result[0]['nodes'],self.old.manifest['counts']['nodes']+1)
            self.old.manifest['counts']=result[0];self.old.manifest['bounds']=result[1];self.old.manifest['layout']['raw_bounds']=result[3]
        self.assertEqual(previous['bounds'],expected_bounds(self.points[:-2]))
    def test_clock_only_keeps_bounds_identical_and_updates_future_count(self):
        old=deepcopy(self.old.manifest)
        result=self.finish([],captured='2031-01-01T00:00:00Z')
        self.assertEqual(result[1],old['bounds']);self.assertEqual(result[0]['nodes'],old['counts']['nodes']);self.assertEqual(result[0]['future_valid_known_count'],0)
        self.assertEqual(self.old.manifest,old)
    def test_repeated_inside_appends_do_not_accumulate_padding(self):
        original=deepcopy(self.old.manifest['bounds'])
        for _ in range(20):
            ordinal=len(self.points);point=(0.,0.,0.);self.c.execute('INSERT INTO nodes VALUES(?,?,?,?,?,?)',(ordinal,'new',*point,None));self.points.append(point)
            result=self.finish([ordinal]);self.assertEqual(result[1],original)
            self.old.manifest['counts']=result[0];self.old.manifest['bounds']=result[1];self.old.manifest['layout']['raw_bounds']=result[3]
    def test_legacy_cache_recomputes_exact_extrema_once_then_carries_them(self):
        del self.old.manifest['layout']['raw_bounds']
        ordinal=len(self.points);point=(11.,0.,0.);self.c.execute('INSERT INTO nodes VALUES(?,?,?,?,?,?)',(ordinal,'new',*point,None));self.points.append(point)
        result=self.finish([ordinal],budget=False)
        self.assertEqual(result[1],expected_bounds(self.points));self.assertEqual(result[3],{'min':[-10.,-3.,-2.],'max':[11.,9.,5.]})
    def test_empty_projection_first_append_does_not_union_placeholder_box(self):
        self.c.execute('DELETE FROM nodes');self.points=[(8.,-7.,6.)]
        self.old.manifest['counts'].update(nodes=0,visible_nodes=0,eligible_before_status_filter=0,future_valid_known_count=0)
        self.old.manifest['bounds']=expected_bounds([]);self.old.manifest['layout']['raw_bounds']=None
        self.c.execute('INSERT INTO nodes VALUES(?,?,?,?,?,?)',(0,'new',*self.points[0],None))
        result=self.finish([0]);self.assertEqual(result[1],expected_bounds(self.points))

    def test_hidden_rows_and_history_keep_all_physical_slot_extrema(self):
        self.c.execute('ALTER TABLE nodes ADD COLUMN flags INTEGER DEFAULT 0')
        self.c.execute('UPDATE nodes SET flags=1 WHERE ordinal=0')
        for history in [False,True]:
            self.selection['history']=history
            result=self.finish([])
            self.assertEqual(result[1],self.full_sql_bounds())
            self.assertLess(result[1]['min'][0],-10.,'A hidden physical slot still determines transport bounds')
    def test_degenerate_extrema_keep_minimum_padding_exact(self):
        self.c.execute('DELETE FROM nodes');self.points=[(8.,8.,8.)]
        self.c.execute('INSERT INTO nodes VALUES(?,?,?,?,?,?)',(0,'old',*self.points[0],None))
        self.old.manifest['counts'].update(nodes=1,visible_nodes=1,eligible_before_status_filter=1,future_valid_known_count=0)
        self.old.manifest['bounds']=expected_bounds(self.points);self.old.manifest['layout']['raw_bounds']={'min':[8.]*3,'max':[8.]*3}
        point=(8.0001,8.,8.);self.c.execute('INSERT INTO nodes VALUES(?,?,?,?,?,?)',(1,'new',*point,None));self.points.append(point)
        result=self.finish([1]);self.assertEqual(result[1],self.full_sql_bounds());self.assertEqual(result[1],expected_bounds(self.points))
