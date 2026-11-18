"""Durable scope checkpoints reference immutable source rows instead of bodies."""
import json
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prototypes.assertion_memory.derived_work import DerivedWorkQueue
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.query_snapshot import capture, _restore_frozen


class CompactDerivedSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'memory.sqlite3'
        self.now = datetime(2026,11,12,tzinfo=timezone.utc)
        self.store = MemoryStore(self.path)
        self.addCleanup(lambda:self.store.close())
        self.engine = MemoryEngine(self.store)
        self.work = DerivedWorkQueue(self.store,clock=lambda:self.now)

    def add(self, key, text=None, **kwargs):
        return self.store.append_assertion(assertion_id=key,text=text or 'Fictional '+key,
            actor='Mira',source='fixture://'+key,episode='fiction',scope='garden',
            recorded_at='2026-01-01',**kwargs)

    def stored_checkpoint(self):
        with self.store.transaction() as connection:
            return connection.execute("SELECT checkpoint FROM memory_derived_work WHERE scope='garden'").fetchone()[0]

    def review(self, key):
        with self.store.transaction(write=True) as connection:
            connection.execute("INSERT INTO memory_branch_reviews VALUES ('context',?,'accepted','fixture',"
                "'Fixture relevance','2026-01-01T00:00:00.000000+00:00','{}')",(key,))

    def test_checkpoint_has_only_descriptor_and_never_copies_long_source_body(self):
        marker='UNIQUE-FICTIONAL-SOURCE-BODY-'+'z'*12000
        self.add('first',marker)
        claim=self.work.claim(self.engine,with_model=True)
        self.assertNotIn(marker,self.stored_checkpoint())
        self.assertEqual(claim.checkpoint['_scheduler']['snapshot'],claim.snapshot.descriptor())
        self.work.save_checkpoint(claim,{**claim.checkpoint,'overview':{'stage':'discovery','cursor':1}})
        self.assertNotIn(marker,self.stored_checkpoint())
        self.assertLess(len(self.stored_checkpoint()),2000)

    def test_restart_restores_original_rows_edges_reviews_and_time_after_backdated_arrivals(self):
        self.add('first');self.add('second')
        self.add('future',valid_at=(self.now+timedelta(seconds=20)).isoformat())
        self.add('future-interpretation',metadata={'interpretation':{'interpreted_at':
            (self.now+timedelta(seconds=20)).isoformat()}})
        self.store.append_edge(edge_id='original',from_id='first',to_id='second',kind='depends_on',recorded_at='2026-01-01')
        self.engine.define_view(ViewSpec('context','Context','Known context?',scopes=('garden',)))
        self.review('second')
        claim=self.work.claim(self.engine,with_model=True)
        frozen=asdict(claim.snapshot)
        self.work.save_checkpoint(claim,{**claim.checkpoint,'overview':{'stage':'leaf','accepted':{'leaf':{'version_id':7}}}})
        self.work.release(claim)
        self.add('backdated-later')
        self.store.append_edge(edge_id='backdated-later',from_id='first',to_id='backdated-later',kind='supports',recorded_at='2026-01-01')
        self.review('backdated-later')
        self.now+=timedelta(seconds=100)
        cached=self.work.claim(self.engine,with_model=True)
        self.assertIs(cached.snapshot,claim.snapshot)
        self.work.release(cached)
        self.store.close()
        self.store=MemoryStore(self.path)
        self.engine=MemoryEngine(self.store)
        restarted=DerivedWorkQueue(self.store,clock=lambda:self.now)
        resumed=restarted.claim(self.engine,with_model=True)
        self.assertEqual(asdict(resumed.snapshot),frozen)
        self.assertEqual(resumed.frontier,claim.frontier)
        self.assertEqual(resumed.checkpoint['overview']['accepted'],{'leaf':{'version_id':7}})
        self.assertNotIn('assertions',json.loads(self.stored_checkpoint())['_scheduler']['snapshot'])
        restarted.finish(resumed,{})
        self.assertEqual(restarted.status('garden')[0]['state'],'pending')

    def test_explicit_requested_cutoffs_and_unknown_valid_policy_survive_hydration(self):
        self.add('unknown')
        self.add('dated',valid_at='2026-01-01')
        self.add('interpreted-later',valid_at='2026-01-01',
                 metadata={'interpretation':{'interpreted_at':'2026-01-04'}})
        snapshot=capture(self.engine,valid_as_of='2026-01-02',recorded_as_of='2026-01-03',
                         captured_at=self.now.isoformat())
        self.add('backdated-after-capture',valid_at='2026-01-01')
        restored=_restore_frozen(self.engine,snapshot.descriptor())
        self.assertEqual(set(restored.assertions),{'dated'})
        self.assertEqual(asdict(restored),asdict(snapshot))


if __name__=='__main__':
    unittest.main()
