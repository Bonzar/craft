from contextlib import closing
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import sqlite3
import unittest

from prototypes.assertion_memory import thought_processing_selection
from prototypes.assertion_memory.query_jobs import QueryJobs
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory import processing_control
from prototypes.assertion_memory.queue_status import build_queue_status
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_worker import ThoughtWorker

NOW = datetime(2026, 11, 25, tzinfo=timezone.utc)
OLD = (NOW - timedelta(seconds=30)).isoformat(timespec='microseconds')
FUTURE = (NOW + timedelta(seconds=30)).isoformat(timespec='microseconds')


class QueueStatusTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / 'queue.db')
        self.store = MemoryStore(self.path)
        self.addCleanup(self.store.close)
        self.queue = CaptureQueue(self.store, clock=lambda: NOW)
        engine = SimpleNamespace(store=self.store, model=object())
        self.worker = ThoughtWorker(self.queue, engine, processing_session_ids=['selected'])
        self.jobs = QueryJobs(self.path, None, None)
        self.jobs._init()

    def source(self, ident, state, *, session='selected', time=OLD, lease=None, attempts=0):
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_raw_events VALUES (?,?,?,?,?,?,?,?,?)',
                ('e-' + ident, 'underlying-session', 'turn', 'PRIVATE PROMPT', 'user', 'test',
                 'scope', OLD, json.dumps({'native_session_id': session})))
            db.execute('INSERT INTO memory_capture_jobs '
                '(id,event_id,status,phase,updated_at,available_at,lease_until,attempts,error) '
                'VALUES (?,?,?,?,?,?,?,?,?)',
                (ident, 'e-' + ident, state, 'extract', OLD, time, lease, attempts, 'PRIVATE ERROR'))

    def maintenance(self, ident, state, *, session='selected', time=OLD, lease=None, attempts=0):
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_thought_work '
                '(thought_id,desired_version,status,available_at,lease_until,attempts,desired_generation) '
                'VALUES (?,1,?,?,?,?,1)', (ident, state, time, lease, attempts))
            db.execute("INSERT INTO memory_thought_work_causes VALUES (?,'cause',1)", (ident,))
            db.execute("INSERT INTO memory_thought_cause_sessions VALUES (?,'cause',?)", (ident, session))

    def interactive(self, ident, state, created):
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_interactive_queries VALUES (?,?,?, ?,NULL,?)',
                       (ident, 'PRIVATE KEY ' + ident, 'PRIVATE ARGUMENT', state, created))

    def status(self, **options):
        with closing(sqlite3.connect(Path(self.path).as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            return build_queue_status(db, **{
                'release_version':'assertion-memory-v0.1.9', 'stage':'test',
                'processing_session_ids':['selected'], 'model_configured':True,
                'interactive_enabled':True, 'now':NOW, **options,
            })

    def lane(self, name, **options):
        return self.status(queue_id=name, **options)['queues'][0]

    def test_source_matches_actual_worker_and_never_exposes_payload(self):
        for ident in ('b','a'):
            self.source(ident,'pending')
        self.source('waiting','waiting_model',time=FUTURE)
        self.source('reclaim','running',lease=OLD)
        self.source('running','running',lease=FUTURE)
        self.source('exhausted','running',lease=OLD,attempts=3)
        self.source('outside','pending',session='other')
        self.source('origin','waiting_origin')
        self.source('delayed','pending',time=FUTURE)
        self.source('done','completed')
        out = self.lane('source')
        self.assertEqual(out['total'],9)
        rows = {row['id']: row for row in out['items']}
        self.assertEqual(rows['running']['status'],'running')
        self.assertEqual(rows['exhausted']['status'],'expired')
        self.assertEqual(rows['outside']['status'],'paused')
        self.assertEqual(rows['origin']['status'],'waiting_origin')
        self.assertEqual(rows['delayed']['status'],'delayed')
        self.assertNotIn('PRIVATE',json.dumps(out))
        ready = [row for row in out['items'] if row['position'] is not None]
        actual = [self.worker._claim_source().job.id for _ in ready]
        self.assertEqual(actual, [row['id'] for row in ready])
        self.assertEqual([row['position'] for row in ready],list(range(1,len(ready)+1)))
        self.assertIsNone(self.worker._claim_source())

    def test_maintenance_matches_worker_cohort_generation_and_retry(self):
        self.maintenance('b','pending')
        self.maintenance('a','running',lease=OLD)
        self.maintenance('excluded','pending',session='other')
        self.maintenance('exhausted','running',lease=OLD,attempts=3)
        self.maintenance('blocked','pending')
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_thought_work SET completed_generation=1 WHERE thought_id='blocked'")
        rows = self.lane('maintenance')['items']
        self.assertEqual([(row['id'], row['status']) for row in rows],
                         [('a','ready'),('b','ready'),('blocked','paused'),('excluded','paused'),
                          ('exhausted','expired')])
        self.assertEqual([self.worker.work.claim()['thought_id'] for _ in range(2)],['a','b'])
        self.assertIsNone(self.worker.work.claim())

    def test_pause_blocks_claims_not_intake_existing_leases_or_interactive(self):
        self.source('a','pending')
        claimed=self.worker._claim_source()
        self.assertEqual(claimed.job.id,'a')
        self.maintenance('t','pending')
        with self.store.transaction(write=True) as db:
            flag,rev=processing_control.set_control(db,paused=True,expected_revision=processing_control.INITIAL)
        self.assertTrue(flag)
        self.source('b','pending')
        self.interactive('question','queued',NOW.timestamp())
        self.assertIsNone(self.worker._claim_source())
        self.assertIsNone(self.worker.work.claim())
        self.assertIsNone(self.queue._claim(with_model=True))
        self.assertEqual(self.jobs._claim()[0],'question')
        status=self.status()
        self.assertEqual(status['processing_control'],{'paused':True,'revision':rev,'can_change':False})
        known={row['id']:row for row in status['queues'][0]['items']}
        self.assertEqual(known['a']['status'],'running')
        self.assertIsNone(known['b']['position'])
        with self.store.transaction() as db:
            self.queue._assert_owned(db,claimed)
        with self.store.transaction(write=True) as db:
            processing_control.set_control(db,paused=False,expected_revision=rev)
        self.assertEqual(self.worker._claim_source().job.id,'b')
        self.assertEqual(self.worker.work.claim()['thought_id'],'t')

    def test_resuming_wakes_actual_launcher_callback_without_another_ingest(self):
        with self.store.transaction(write=True) as db:
            _, revision=processing_control.set_control(db,paused=True,expected_revision=processing_control.INITIAL)
        async def scenario():
            calls=[]
            async with processing_control.restart_on_resume(self.path, lambda: calls.append('launch')):
                await asyncio.sleep(.1)
                self.assertEqual(calls,[])
                with self.store.transaction(write=True) as db:
                    processing_control.set_control(db,paused=False,expected_revision=revision)
                for _ in range(20):
                    if calls:
                        break
                    await asyncio.sleep(.05)
                self.assertEqual(calls,['launch'])
        asyncio.run(scenario())

    def test_interactive_order_and_pagination_match_claim(self):
        self.interactive('b','queued',2)
        self.interactive('a','queued',2)
        self.interactive('c','queued',1)
        self.interactive('current','running',3)
        self.interactive('historical','completed',0)
        result = self.status(limit=2)
        self.assertEqual(result['stage'],'test')
        self.assertEqual(result['release_version'],'assertion-memory-v0.1.9')
        self.assertIs(result['global_order'],False)
        self.assertEqual([lane['id'] for lane in result['queues']], ['source','maintenance','interactive'])
        page = self.lane('interactive',limit=2)
        self.assertEqual(page['total'],4)
        self.assertEqual(page['next_offset'],2)
        rest = self.lane('interactive',limit=2,offset=2)
        self.assertIsNone(rest['next_offset'])
        self.assertEqual([(i['id'],i['position']) for i in page['items']+rest['items']],
                         [('current',None),('c',1),('a',2),('b',3)])
        self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertEqual([self.jobs._claim()[0] for _ in range(3)],['c','a','b'])

    @unittest.skipUnless(hasattr(thought_processing_selection, 'FRESH_MAC_ONLY'),
                         'fresh Mac processing is not present in this release')
    def test_fresh_mac_matches_opt_in_source_selector_and_pauses_maintenance(self):
        options = dict(processing_session_ids=[], auto_process_fresh_mac=True)
        self.source('eligible','pending')
        self.source('excluded','pending')
        self.maintenance('pending','pending')
        self.assertTrue(all(row['status']=='paused' for row in self.lane('source',**options)['items']))
        with self.store.transaction(write=True) as db:
            from prototypes.assertion_memory.fresh_processing import _schema
            _schema(db)
            db.execute("INSERT INTO memory_fresh_mac_events VALUES ('e-eligible')")
        self.assertEqual([(r['id'],r['status']) for r in self.lane('source',**options)['items']],
                         [('eligible','ready'),('excluded','paused')])
        fresh = ThoughtWorker(self.queue,SimpleNamespace(store=self.store,model=object()),
            processing_session_ids=thought_processing_selection.FRESH_MAC_ONLY)
        self.assertEqual(fresh._claim_source().job.id,'eligible')
        self.assertIsNone(fresh._claim_source())
        self.assertEqual(self.lane('maintenance',**options)['items'][0]['status'],'paused')

    def test_paused_missing_schema_and_invalid_page_are_safe(self):
        self.source('a','pending')
        self.assertEqual(self.lane('source',processing_session_ids=[])['items'][0]['status'],'paused')
        self.assertEqual(self.lane('source',model_configured=False)['status'],'unavailable')
        self.assertEqual(self.lane('interactive',interactive_enabled=False)['status'],'disabled')
        for options in ({'limit':101},{'offset':-1},{'stage':'QA'},{'queue_id':'legacy'}):
            with self.assertRaises(ValueError):
                self.status(**options)
        with sqlite3.connect(':memory:') as db:
            result = build_queue_status(db, release_version='v1', stage='prod',
                processing_session_ids=None, model_configured=True, interactive_enabled=True)
            self.assertEqual([q['total'] for q in result['queues']],[0,0,0])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()[0],0)


if __name__ == '__main__':
    unittest.main()
