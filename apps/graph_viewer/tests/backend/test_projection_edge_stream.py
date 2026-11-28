"""Keep exact projection edge order, temporal membership, and source bytes."""
import sqlite3
from contextlib import closing
from unittest import TestCase
from apps.graph_viewer.server.common import APIError
from apps.graph_viewer.server.projection import copy_edges

CUTOFF='2026-11-27T00:00:00.000000+00:00'

class ProjectionEdgeStream(TestCase):
    def test_sparse_ids_temporal_boundaries_and_order(self):
        with closing(sqlite3.connect(':memory:')) as origin, closing(sqlite3.connect(':memory:')) as projected:
            origin.row_factory=sqlite3.Row
            origin.execute('CREATE TABLE memory_edges(edge_id,from_id,to_id,kind,recorded_at,metadata)')
            origin.executemany('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',[
                ('excluded','missing','b','supports','not a timestamp','{}'),
                ('first','a','b','supports','2026-11-27T02:00:00+02:00','{"kept": true}'),
                ('unknown','b','a','supports',None,'unknown metadata'),
                ('future','b','a','supports','2026-11-27T00:00:01Z','{}'),
                ('repeat','b','a','contradicts','2026-11-27T02:00:00+02:00','{}'),
            ])
            projected.execute('CREATE TABLE edges(ordinal,edge_id,f,t,kind,recorded_at,metadata)')
            self.assertEqual(copy_edges(origin,projected,{'a':0,'b':7},CUTOFF,allow_unknown=False),2)
            self.assertEqual(projected.execute('SELECT * FROM edges ORDER BY ordinal').fetchall(),[
                (0,'first',0,7,'supports','2026-11-27T02:00:00+02:00','{"kept": true}'),
                (1,'repeat',7,0,'contradicts','2026-11-27T02:00:00+02:00','{}'),
            ])
            # Cursor-local override must not change the source connection factory.
            self.assertIsInstance(origin.execute('SELECT * FROM memory_edges').fetchone(),sqlite3.Row)
            projected.execute('DELETE FROM edges')
            self.assertEqual(copy_edges(origin,projected,{'a':0,'b':7},CUTOFF),3)
            self.assertEqual(projected.execute('SELECT recorded_at FROM edges WHERE edge_id="unknown"').fetchone(),(None,))
            origin.execute('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',
                           ('bad','a','b','supports','not a timestamp','{}'))
            with self.assertRaises(APIError):
                copy_edges(origin,projected,{'a':0,'b':7},CUTOFF)

    def test_unique_instants_fit_projection_semantics_but_cache_remains_bounded(self):
        from datetime import datetime,timedelta,timezone
        from unittest.mock import patch
        from apps.graph_viewer.server import projection

        start=datetime(2026,11,26,tzinfo=timezone.utc)
        values=[(start+timedelta(seconds=i)).isoformat() for i in range(2050)]
        # Latest still lives in the memo; first must have been evicted.
        instants=values+[values[-1],values[0]]
        with closing(sqlite3.connect(':memory:')) as origin, closing(sqlite3.connect(':memory:')) as projected:
            origin.row_factory=sqlite3.Row
            origin.execute('CREATE TABLE memory_edges(edge_id,from_id,to_id,kind,recorded_at,metadata)')
            origin.executemany('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',
                ((str(i),'a','b','supports',instant,'{}') for i,instant in enumerate(instants)))
            projected.execute('CREATE TABLE edges(ordinal,edge_id,f,t,kind,recorded_at,metadata)')
            with patch.object(projection,'timestamp',wraps=projection.timestamp) as normalize:
                self.assertEqual(copy_edges(origin,projected,{'a':0,'b':1},CUTOFF),len(instants))
            self.assertEqual(normalize.call_count,len(values)+1)
            self.assertEqual(projected.execute('SELECT * FROM edges ORDER BY ordinal').fetchall(),
                [(i,str(i),0,1,'supports',instant,'{}') for i,instant in enumerate(instants)])
