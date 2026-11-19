"""Disconnected source arcs determine viewer geometry, never semantic membership."""
from copy import deepcopy
import math
import sqlite3
import unittest
from unittest.mock import patch
from apps.graph_viewer.server.layout import pack
from apps.graph_viewer.server import layout


def projection(order=None, arcs=None):
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    c.executescript('CREATE TABLE nodes(ordinal INTEGER PRIMARY KEY,id TEXT UNIQUE,x REAL,y REAL,z REAL); CREATE TABLE edges(ordinal INTEGER PRIMARY KEY,id TEXT,f INTEGER,t INTEGER,kind TEXT); CREATE TABLE memberships(topic INTEGER,node INTEGER,PRIMARY KEY(topic,node));')
    names=order or list('abcdefghij');indices={name:i for i,name in enumerate(names)}
    c.executemany('INSERT INTO nodes VALUES(?,?,NULL,NULL,NULL)',enumerate(names))
    arcs=arcs if arcs is not None else [('b','a'),('b','c'),('c','b'),('d','e'),('e','d'),('f','f')]
    c.executemany('INSERT INTO edges VALUES(?,?,?,?,?)',((i,'edge-'+str(i),indices[a],indices[b],'supports') for i,(a,b) in enumerate(arcs)))
    c.executemany('INSERT INTO memberships VALUES(?,?)',((topic,indices[name]) for topic,names_ in enumerate(['abcdefghij','aceg','bcde']) for name in names_))
    topics={'topics':[{'id':'topic:'+str(i),'index':i,'depth':0,'member_count':n} for i,n in enumerate([10,4,4])], 'links':[], 'memberships':[{'topic_index':i,'node_indices':[r[0] for r in c.execute('SELECT node FROM memberships WHERE topic=?',(i,))]} for i in range(3)]}
    return c,topics


def positions(c):
    return {r['id']:tuple(r[axis] for axis in ['x','y','z']) for r in c.execute('SELECT * FROM nodes')}


def weak_parts(c):
    names={r[0]:r[1] for r in c.execute('SELECT ordinal,id FROM nodes')};neighbors={n:set() for n in names}
    for a,b in c.execute('SELECT f,t FROM edges'):neighbors[a].add(b);neighbors[b].add(a)
    remaining=set(names);result=set()
    while remaining:
        pending=[remaining.pop()];visited=set(pending)
        while pending:
            for node in neighbors[pending.pop()]-visited:
                visited.add(node);remaining.discard(node);pending.append(node)
        result.add(frozenset(names[n] for n in visited))
    return result


def disjoint(test,c):
    boxes=[tuple(r) for r in c.execute('SELECT x0,y0,z0,x1,y1,z1 FROM layout_bins')]
    for i,a in enumerate(boxes):
        for b in boxes[i+1:]:
            test.assertTrue(any(a[axis+3]<b[axis] or b[axis+3]<a[axis] for axis in range(3)),(a,b))
    for row in c.execute('SELECT n.x,n.y,n.z,b.x0,b.y0,b.z0,b.x1,b.y1,b.z1 FROM nodes n JOIN layout_bin_nodes m ON m.node=n.ordinal JOIN layout_bins b ON b.id=m.bin'):
        test.assertTrue(all(row[3+axis]<=row[axis]<=row[6+axis] for axis in range(3)))


class SourceComponentLayout(unittest.TestCase):
    def test_exact_weak_components_preserve_arcs_memberships_and_relative_geometry(self):
        c,topics=projection();original=deepcopy(topics)
        arcs=[tuple(r) for r in c.execute('SELECT * FROM edges')];members=[tuple(r) for r in c.execute('SELECT * FROM memberships')]
        joined,joined_topics=projection(arcs=[(a,b) for a,b in zip('abcdefghi','bcdefghij')]);pack(joined,joined_topics);before=positions(joined)
        pack(c,topics);after=positions(c)
        a=[after[name] for name in 'abc'];b=[after[name] for name in 'de']
        self.assertTrue(any(max(p[axis] for p in a)+60<=min(p[axis] for p in b) or max(p[axis] for p in b)+60<=min(p[axis] for p in a) for axis in range(3)),'Disconnected source components still overlap')
        groups={frozenset(r[0] for r in c.execute('SELECT n.id FROM nodes n JOIN layout_bin_nodes m ON m.node=n.ordinal WHERE m.bin=?',(row[0],))) for row in c.execute('SELECT id FROM layout_bins')}
        self.assertEqual(groups,{frozenset(x) for x in ['abc','de','f','g','h','i','j']})
        self.assertEqual(arcs,[tuple(r) for r in c.execute('SELECT * FROM edges')]);self.assertEqual(members,[tuple(r) for r in c.execute('SELECT * FROM memberships')])
        self.assertEqual(original['memberships'],topics['memberships'])
        for group in groups:
            anchor=min(group)
            for node in group:
                for axis in range(3):self.assertAlmostEqual(after[node][axis]-after[anchor][axis],before[node][axis]-before[anchor][axis],places=8)
        for topic in topics['topics']:
            points=[after[r[0]] for r in c.execute('SELECT n.id FROM nodes n JOIN memberships m ON m.node=n.ordinal WHERE m.topic=?',(topic['index'],))]
            for axis in range(3):self.assertAlmostEqual(topic['position'][axis],sum(p[axis] for p in points)/len(points))
            self.assertTrue(all(math.dist(p,topic['position'])<=topic['radius']+1e-8 for p in points),'Recomputed topic bound must contain every exact member')
        disjoint(self,c)
        c.close();joined.close()

    def test_node_edge_insertion_permutations_do_not_change_positions_by_source_id(self):
        c,topics=projection();pack(c,topics)
        other,other_topics=projection(order=list(reversed('abcdefghij')),arcs=[('f','f'),('e','d'),('d','e'),('c','b'),('b','c'),('b','a')]);pack(other,other_topics)
        self.assertEqual(positions(c),positions(other));disjoint(self,other)
        c.close();other.close()

    def test_append_inside_one_component_or_new_island_keeps_old_coordinates(self):
        c,topics=projection();pack(c,topics);before=positions(c)
        c.executemany('INSERT INTO nodes VALUES(?,?,NULL,NULL,NULL)',[(10,'new-attached'),(11,'new-island'),(12,'another-new')])
        c.executemany('INSERT INTO edges VALUES(?,?,?,?,?)',[(6,'new-link',10,0,'supports'),(7,'new-island-link',12,11,'supports')])
        self.assertTrue(layout.append_bins(c,[10,11,12],[6,7]))
        self.assertEqual(before,{key:value for key,value in positions(c).items() if key in before});disjoint(self,c)
        self.assertEqual(c.execute('SELECT bin FROM layout_bin_nodes WHERE node=10').fetchone()[0],c.execute('SELECT bin FROM layout_bin_nodes WHERE node=0').fetchone()[0])
        self.assertEqual(c.execute('SELECT bin FROM layout_bin_nodes WHERE node=11').fetchone()[0],c.execute('SELECT bin FROM layout_bin_nodes WHERE node=12').fetchone()[0])
        c.close()

    def test_large_component_does_not_force_every_singleton_into_a_large_cell(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.executescript('CREATE TABLE nodes(ordinal INTEGER PRIMARY KEY,id TEXT UNIQUE,x REAL,y REAL,z REAL); CREATE TABLE edges(f INTEGER,t INTEGER);')
        c.executemany('INSERT INTO nodes VALUES(?,?,?,?,?)',[(0,'large-a',0,0,0),(1,'large-b',5000,5000,5000)]+[(i,'single-'+str(i),0,0,0) for i in range(2,258)])
        c.execute('INSERT INTO edges VALUES(1,0)');layout.pack_components(c);disjoint(self,c)
        global_span=[c.execute(f'SELECT max({axis}1)-min({axis}0) FROM layout_bins').fetchone()[0] for axis in 'xyz']
        large=tuple(c.execute("SELECT x1-x0,y1-y0,z1-z0 FROM layout_bins WHERE id='large-a'").fetchone())
        self.assertLess(math.prod(global_span),math.prod(large)*8,'A large component must not multiply empty space by the singleton count')
        c.close()

    def test_append_order_does_not_change_source_id_geometry(self):
        result=[]
        for names in [['new-a','new-b','new-c'],['new-c','new-b','new-a']]:
            c,topics=projection();pack(c,topics)
            c.executemany('INSERT INTO nodes VALUES(?,?,NULL,NULL,NULL)',enumerate(names,10))
            self.assertTrue(layout.append_bins(c,[10,11,12],[]));result.append(positions(c));c.close()
        self.assertEqual(*result)

    def test_new_chain_between_bins_uses_a_real_neighbor_and_preserves_every_arc(self):
        c,topics=projection();pack(c,topics);before=positions(c)
        c.executemany('INSERT INTO nodes VALUES(?,?,NULL,NULL,NULL)',[(10,'new-x'),(11,'new-y'),(12,'new-z')])
        c.executemany('INSERT INTO edges VALUES(?,?,?,?,?)',[(6,'x-a',10,0,'supports'),(7,'d-y',3,11,'supports'),(8,'x-y',10,11,'supports'),(9,'old-bridge',0,3,'supports'),(10,'z-d',12,3,'supports')])
        original_arcs=[tuple(r) for r in c.execute('SELECT * FROM edges')]
        self.assertTrue(layout.append_bins(c,[10,11,12],[6,7,8,9,10]))
        bins={r[0]:r[1] for r in c.execute('SELECT node,bin FROM layout_bin_nodes')}
        self.assertEqual(bins[10],bins[0]);self.assertEqual(bins[11],bins[0])
        self.assertEqual(bins[12],bins[3],'An old bridge must not invent another direct placement neighbor')
        self.assertEqual(before,{key:value for key,value in positions(c).items() if key in before})
        self.assertEqual(original_arcs,[tuple(r) for r in c.execute('SELECT * FROM edges')]);disjoint(self,c)
        self.assertTrue(any({'a','e','new-x','new-y','new-z'}<=part for part in weak_parts(c)))
        c.close()

    def test_missing_or_corrupt_derived_metadata_requests_rebuild(self):
        for damage in ['DROP TABLE layout_bin_nodes','DROP TABLE layout_bins','DROP TABLE layout_packing',"DELETE FROM layout_packing WHERE key='target'","UPDATE layout_packing SET value='invalid' WHERE key='target'","UPDATE layout_bins SET x0=x1+1"]:
            with self.subTest(damage=damage):
                c,topics=projection();pack(c,topics);before=positions(c);c.execute(damage)
                c.execute("INSERT INTO nodes VALUES(10,'new',NULL,NULL,NULL)")
                c.execute("INSERT INTO edges VALUES(6,'new-edge',10,0,'supports')")
                self.assertFalse(layout.append_bins(c,[10],[6]))
                self.assertEqual(before,{key:value for key,value in positions(c).items() if key in before});c.close()

    def test_bridge_keeps_physical_bins_and_old_positions_while_connectivity_merges(self):
        c,topics=projection();pack(c,topics);before=positions(c)
        c.execute("INSERT INTO edges VALUES(6,'bridge',2,3,'supports')")
        self.assertTrue(layout.append_bins(c,[],[6]));self.assertEqual(before,positions(c));disjoint(self,c)
        self.assertNotEqual(c.execute('SELECT bin FROM layout_bin_nodes WHERE node=0').fetchone()[0],c.execute('SELECT bin FROM layout_bin_nodes WHERE node=4').fetchone()[0])
        self.assertIn(frozenset('abcde'),weak_parts(c))
        c.close()


# Placement bins are geometric caches. A real source bridge changes current
# connectivity immediately without relocating the previously placed stars.
import asyncio
import hashlib
import json
from test_live import LiveFixture
from apps.graph_viewer.server.cache_restore import restore_graph
from apps.graph_viewer.server.layout import VERSION


class ComponentPublisher(LiveFixture,unittest.IsolatedAsyncioTestCase):
    async def changed(self,old):
        for _ in range(100):
            latest=self.manager.graphs[self.manager.latest[self.manager.key(old.manifest['selector'])]]
            if latest.revision!=old.revision:return latest
            await asyncio.sleep(.03)
        self.fail('Component layout was never published')

    async def test_source_bridge_publishes_exact_delta_without_reset_or_moving_stars(self):
        old=await self.graph()
        with old.connect() as c:
            before=positions(c);members=[tuple(r) for r in c.execute('SELECT * FROM memberships')]
        with sqlite3.connect(self.source_path) as c:
            c.execute('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',('island-bridge','unicode','undated','supports','2025-01-01T00:00:00+00:00','{}'))
            c.execute('UPDATE memory_state SET version=version+1')
        source_hash=hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        with patch('apps.graph_viewer.server.manager.prepare',side_effect=AssertionError('A source bridge must not rebuild the graph')):
            new=await self.changed(old)
        self.assertEqual(self.manager.journal[-1][1],'delta')
        self.assertEqual((old.directory/'nodes.bin').read_bytes(),(new.directory/'nodes.bin').read_bytes())
        with old.connect() as c:self.assertEqual(before,positions(c))
        with new.connect() as c:
            disjoint(self,c)
            self.assertEqual(members,[tuple(r) for r in c.execute('SELECT * FROM memberships')])
            self.assertEqual(tuple(c.execute("SELECT a.id,b.id,e.kind FROM edges e JOIN nodes a ON a.ordinal=e.f JOIN nodes b ON b.ordinal=e.t WHERE e.id='island-bridge'").fetchone()),('unicode','undated','supports'))
            self.assertEqual(c.execute("SELECT count(DISTINCT m.bin) FROM layout_bin_nodes m JOIN nodes n ON n.ordinal=m.node WHERE n.id IN ('unicode','undated')").fetchone()[0],2)
            self.assertTrue(any({'unicode','undated'}<=part for part in weak_parts(c)))
        self.assertEqual(source_hash,hashlib.sha256(self.source_path.read_bytes()).hexdigest())
        self.assertEqual(set(p.name for p in (self.root/'cache').iterdir()),{old.revision,new.revision},'A bridge must publish exactly one new delta revision')

    async def test_missing_cached_component_metadata_falls_back_to_snapshot_reset(self):
        old=await self.graph()
        with old.connect() as c:c.execute('DROP TABLE layout_packing')
        with sqlite3.connect(self.source_path) as c:
            c.execute("INSERT INTO memory_assertions VALUES('new','new','actor','saved','session','2025-01-01T00:00:00+00:00','2025-01-01T00:00:00+00:00','s','{}')")
            c.execute('UPDATE memory_state SET version=version+1')
        new=await self.changed(old)
        self.assertEqual(self.manager.journal[-1][1],'reset')
        self.assertTrue(self.manager.available)
        self.assertEqual(new.manifest['counts']['nodes'],7)
        with new.connect() as c:disjoint(self,c)

    async def test_legacy_layout_rebuilds_current_without_source_change_but_remains_readable(self):
        old=await self.graph();before_revision=old.manifest['source_revision']
        old.manifest['layout']['version']='source-membership-volume-v2'
        (old.directory/'manifest.json').write_text(json.dumps(old.manifest))
        restored=restore_graph(self.root/'cache',old.revision,self.manager.source.provider_id)
        self.assertIsNotNone(restored,'Historical geometry must remain readable')
        new=await self.changed(old)
        self.assertEqual(new.manifest['layout']['version'],VERSION)
        self.assertEqual(new.manifest['source_revision'],before_revision)
        self.assertEqual(self.manager.journal[-1][1],'reset')
        with old.connect() as c:self.assertEqual(len(positions(c)),6)
