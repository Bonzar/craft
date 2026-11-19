"""Synthetic scale invariants; no private database or content is opened."""
import math
import sqlite3
import unittest
from apps.graph_viewer.server.layout import component_sets,pack_components,append_bins


class ComponentScale(unittest.TestCase):
    def test_connectivity_consumes_a_million_directed_arcs_in_one_stream(self):
        class Arcs:
            iterations=0
            def __iter__(self):
                self.iterations+=1
                if self.iterations>1:raise AssertionError('Connectivity must not rescan arcs per component')
                for repeat in range(10):
                    for node in range(100000):
                        base=0 if node<50000 else 50000
                        yield node,base+(node-base+1+repeat)%50000
        arcs=Arcs();groups=component_sets(range(100000),arcs)
        self.assertEqual({frozenset(group) for group in groups},{frozenset(range(50000)),frozenset(range(50000,100000))})
        self.assertEqual(arcs.iterations,1)

    def test_hundred_thousand_isolated_stars_pack_compactly_and_append_without_old_scan(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.executescript('CREATE TABLE nodes(ordinal INTEGER PRIMARY KEY,id TEXT UNIQUE,x REAL,y REAL,z REAL); CREATE TABLE edges(ordinal INTEGER PRIMARY KEY,f INTEGER,t INTEGER);')
        count=100000
        c.executemany('INSERT INTO nodes VALUES(?,?,0,0,0)',((i,'star-'+str(i)) for i in range(count)))
        pack_components(c)
        self.assertEqual(c.execute('SELECT count(*) FROM layout_bins').fetchone()[0],count)
        self.assertEqual(c.execute('SELECT count(*) FROM layout_bin_nodes').fetchone()[0],count)
        low,high=c.execute('SELECT min(x0),max(x1) FROM layout_bins').fetchone()
        spans=[c.execute(f'SELECT max({axis}1)-min({axis}0) FROM layout_bins').fetchone()[0] for axis in 'xyz']
        self.assertTrue(all(math.isfinite(span) and span<240*math.ceil(count**(1/3)) for span in spans),spans)
        self.assertGreater(high,low)
        old=tuple(c.execute('SELECT x,y,z FROM nodes WHERE ordinal=0').fetchone())
        c.execute('INSERT INTO nodes VALUES(100000,\'appended\',NULL,NULL,NULL)')
        c.execute('INSERT INTO edges VALUES(0,100000,0)')
        steps=[0]
        def bounded():
            steps[0]+=1000
            return int(steps[0]>10000)
        c.set_progress_handler(bounded,1000)
        try:
            self.assertTrue(append_bins(c,[100000],[0]))
        except sqlite3.OperationalError as error:
            self.fail('A one-star append scanned old projection rows: '+str(error))
        finally:c.set_progress_handler(None,0)
        self.assertEqual(old,tuple(c.execute('SELECT x,y,z FROM nodes WHERE ordinal=0').fetchone()))
        row=c.execute('SELECT n.x,n.y,n.z,b.x0,b.y0,b.z0,b.x1,b.y1,b.z1 FROM nodes n JOIN layout_bin_nodes m ON m.node=n.ordinal JOIN layout_bins b ON b.id=m.bin WHERE n.ordinal=100000').fetchone()
        self.assertTrue(all(row[3+i]<=row[i]<=row[6+i] for i in range(3)))
        c.close()
