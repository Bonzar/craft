from datetime import datetime,timedelta,timezone
import sqlite3
from contextlib import closing
from unittest import TestCase
from unittest.mock import patch
from apps.graph_viewer.server import projection,temporal

CAPTURED='2026-11-28T00:00:00.000000+00:00'

class NodeTimeMemo(TestCase):
    def test_copy_nodes_evaluates_availability_once_and_retains_exact_temporal_visibility(self):
        rows=[
            ('published','source','actor','episode','alpha','misc','2026-11-27T02:00:00+02:00','2026-11-27T00:00:00Z','{}'),
            ('undated','source','actor','episode','beta','misc',None,'2026-11-27T00:00:00Z','{}'),
            ('future','source','actor','episode','gamma','misc','2026-11-29T00:00:00Z','2026-11-27T00:00:00Z','{}'),
            ('not_known','source','actor','episode','delta','misc',None,'2026-11-30T00:00:00Z','{}'),
        ]
        cases=[
          ('current','separate',None,['published','undated','future']),
          ('known_now','include',CAPTURED,['published','undated']),
          ('known_now','separate',CAPTURED,['published']),
          ('known_now','only',CAPTURED,['undated']),
          ('known_then','separate',CAPTURED,['published','undated','future']),
        ]
        with closing(sqlite3.connect(':memory:')) as source:
            source.row_factory=sqlite3.Row
            source.execute('CREATE TABLE memory_assertions(assertion_id,source,actor,episode,text,scope,valid_at,recorded_at,metadata)')
            source.executemany('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',rows)
            for mode,undated,at,expected in cases:
                with self.subTest(mode=mode,undated=undated), closing(sqlite3.connect(':memory:')) as c:
                    c.executescript(projection.SCHEMA)
                    with patch.object(projection,'available_at',wraps=projection.available_at) as available:
                        _, ids, counters=projection.copy_nodes(source,c,{'mode':mode,'undated':undated,'at':at},CAPTURED)
                    self.assertEqual(list(ids),expected)
                    self.assertEqual(available.call_count,len(rows))
                    self.assertEqual(counters['future_valid_known_count'],1)
                    self.assertEqual(counters['undated_known_count'],1)

    def test_cache_cardinality_and_timezone_canonicalization(self):
        normalize=temporal.projection_timestamp_cache()
        self.assertEqual(normalize('2026-11-27T02:00:00+02:00'),'2026-11-27T00:00:00.000000+00:00')
        begin=datetime(2025,1,1,tzinfo=timezone.utc)
        for i in range(3000):
            value=(begin+timedelta(seconds=i)).isoformat()
            self.assertEqual(normalize(value),temporal.timestamp(value))
        self.assertEqual(normalize.cache_info().currsize,2048)
