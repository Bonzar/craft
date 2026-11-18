"""Supervisor concurrency/ownership only: synthetic fixed-latency operations, no LLM."""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import sqlite3
import copy
import json
import math
import tempfile
from pathlib import Path
from unittest.mock import patch
import threading
import time
import unittest
from prototypes.assertion_memory.processing_pool import ActiveClaims,ProcessingPool
from prototypes.assertion_memory.mcp_server import ServiceConfig

class ProcessingPoolTest(unittest.TestCase):
    def test_non_busy_sqlite_errors_are_not_hidden_as_contention(self):
        with sqlite3.connect(':memory:') as db:
            pool=ProcessingPool(None,heartbeat=lambda:db.execute('SELECT * FROM missing_table'))
            with self.assertRaisesRegex(sqlite3.OperationalError,'no such table'):
                pool._beat()

    def test_sqlite_writer_contention_does_not_stop_source_lanes(self):
        ready=threading.Event();released=threading.Event();failures=[];renewals=[]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'claims.sqlite3'
            db=sqlite3.connect(path,isolation_level=None)
            db.execute('CREATE TABLE claims(value INTEGER)')
            db.execute('PRAGMA busy_timeout=1')
            def hold_write_lock():
                with sqlite3.connect(path,isolation_level=None) as other:
                    other.execute('BEGIN IMMEDIATE');ready.set()
                    time.sleep(.08);other.rollback();released.set()
            blocker=threading.Thread(target=hold_write_lock);blocker.start()
            self.assertTrue(ready.wait(2))
            def heartbeat():
                try:db.execute('BEGIN IMMEDIATE')
                except sqlite3.OperationalError as error:
                    failures.append(error.sqlite_errorcode);raise
                db.rollback();renewals.append(True)
            @contextmanager
            def factory(lane,observer):
                class Worker:
                    model=object()
                    def run_once(self):
                        if lane=='maintenance':return None
                        if not released.wait(2):raise AssertionError('writer never released')
                        time.sleep(.03);return object()
                    def seconds_until_work(self,*,with_model):return None
                yield Worker()
            pool=ProcessingPool(factory,source_workers=1,limit=1,poll_seconds=.002,
                heartbeat_seconds=.01,heartbeat=heartbeat)
            try:result=pool.run()
            finally:blocker.join(timeout=2);db.close()
        self.assertTrue(failures);self.assertTrue(renewals)
        self.assertEqual(result['completed'],1)
        self.assertTrue(all(not thread.is_alive() for thread in pool.threads))

    def test_two_source_calls_overlap_and_maintenance_advances(self):
        barrier=threading.Barrier(2);maintenance=threading.Event();lock=threading.Lock();sources=[0];owners=[];closed=[]
        @contextmanager
        def factory(lane,observer):
            db=sqlite3.connect(':memory:');owner=threading.get_ident();owners.append(owner)
            class Worker:
                model=object()
                def run_once(self):
                    db.execute('SELECT 1')
                    if lane=='maintenance':maintenance.set();return object()
                    with lock:sources[0]+=1;identity=str(sources[0])
                    observer('claimed','source',identity,identity)
                    try:
                        barrier.wait(timeout=2)
                        if not maintenance.wait(2):raise AssertionError('maintenance starved behind sources')
                        return object()
                    finally:observer('released','source',identity,identity)
                def seconds_until_work(self,*,with_model):return 0
            try:yield Worker()
            finally:db.close();closed.append(owner)
        result=ProcessingPool(factory,source_workers=2,limit=3,poll_seconds=.002,idle_seconds=.02).run()
        self.assertEqual(result['completed'],3);self.assertEqual(result['reason'],'limit')
        self.assertEqual(len(set(owners)),3);self.assertCountEqual(owners,closed)
        self.assertNotIn(threading.get_ident(),owners)
    def test_global_limit_is_shared_not_per_lane(self):
        lock=threading.Lock();calls=[]
        @contextmanager
        def factory(lane,observer):
            class Worker:
                model=object()
                def run_once(self):
                    time.sleep(.002)
                    with lock:calls.append(lane)
                    return object()
                def seconds_until_work(self,*,with_model):return 0
            yield Worker()
        result=ProcessingPool(factory,source_workers=2,limit=7,poll_seconds=.001).run()
        self.assertEqual(result['completed'],7);self.assertEqual(len(calls),7)
    def test_idle_return_and_failure_wait_for_all_connections_to_close(self):
        closed=[]
        @contextmanager
        def factory(lane,observer):
            class Worker:
                model=None
                def run_once(self):return None
                def seconds_until_work(self,*,with_model):return None
            try:yield Worker()
            finally:closed.append(lane)
        pool=ProcessingPool(factory,source_workers=2,idle_seconds=.02,poll_seconds=.002)
        self.assertEqual(pool.run()['reason'],'idle');self.assertEqual(len(closed),3)
        self.assertTrue(all(not thread.is_alive() for thread in pool.threads))
    def test_error_stops_new_turns_but_joins_active_source_and_heartbeats(self):
        started=threading.Event();finished=threading.Event();beats=[]
        @contextmanager
        def factory(lane,observer):
            class Worker:
                model=object()
                def run_once(self):
                    if lane=='maintenance':
                        started.wait(2);raise RuntimeError('synthetic lane failure')
                    observer('claimed','source','job','token');started.set()
                    try:time.sleep(.08);finished.set();return object()
                    finally:observer('released','source','job','token')
                def seconds_until_work(self,*,with_model):return 0
            yield Worker()
        pool=ProcessingPool(factory,source_workers=1,poll_seconds=.002,heartbeat_seconds=.01,heartbeat=lambda:beats.append(threading.get_ident()))
        with self.assertRaisesRegex(RuntimeError,'synthetic lane failure'):pool.run()
        self.assertTrue(finished.is_set());self.assertGreater(len(beats),2)
        self.assertEqual(set(beats),{threading.get_ident()});self.assertTrue(all(not t.is_alive() for t in pool.threads))
    def test_heartbeat_only_owned_registered_unexpired_tokens(self):
        db=sqlite3.connect(':memory:')
        db.executescript('CREATE TABLE memory_capture_jobs(id TEXT,status TEXT,lease_token TEXT,lease_until TEXT);CREATE TABLE memory_derived_work(scope TEXT,status TEXT,lease_token TEXT,lease_until TEXT);CREATE TABLE memory_view_name_work(view_id TEXT,status TEXT,lease_token TEXT,lease_until TEXT);')
        now=datetime.now(timezone.utc);future=(now+timedelta(seconds=1)).isoformat(timespec='microseconds');past=(now-timedelta(seconds=1)).isoformat(timespec='microseconds')
        db.executemany('INSERT INTO memory_capture_jobs VALUES(?,?,?,?)',[('owned','running','yes',future),('unregistered','running','other',future),('revoked','pending',None,future),('expired','running','gone',past)])
        claims=ActiveClaims()
        for identity,token in [('owned','yes'),('revoked','old'),('expired','gone')]:claims.observe('claimed','source',identity,token)
        self.assertEqual(claims.renew(db,lease_seconds=300,now=now),1)
        rows={r[0]:r for r in db.execute('SELECT * FROM memory_capture_jobs')}
        self.assertGreater(rows['owned'][3],future);self.assertEqual(rows['unregistered'][3],future)
        self.assertEqual(rows['expired'][3],past);self.assertIsNone(rows['revoked'][2])
        claims.observe('released','source','owned','yes');self.assertEqual(claims.renew(db,now=now),0)
    def test_fixed_latency_same_conditional_corpus_reports_throughput_and_p95(self):
        corpus=[{'id':str(i),'text':'Synthetic activation remains conditional.',
                 'condition':'Only after trial confirmation','actor':'fixture-'+str(i%2)} for i in range(8)]
        metrics=[]
        for count in (1,2):
            pending=copy.deepcopy(corpus);outputs=[];maintenance=[];latencies=[];mutex=threading.Lock();active=[0];peak=[0];started=time.monotonic()
            @contextmanager
            def factory(lane,observer):
                class Worker:
                    model=object()
                    def run_once(self):
                        if lane=='maintenance':
                            with mutex:
                                if len(maintenance)>=4:return None
                                maintenance.append({'at':time.monotonic()-started,'sources_remaining':len(pending)})
                            time.sleep(.002);return object()
                        with mutex:
                            if not pending:return None
                            item=pending.pop(0);active[0]+=1;peak[0]=max(peak[0],active[0])
                        time.sleep(.02)  # Controlled I/O latency; no semantic/model quality claim.
                        with mutex:
                            active[0]-=1;outputs.append(item);latencies.append(time.monotonic()-started)
                        return object()
                    def seconds_until_work(self,*,with_model):return None
                yield Worker()
            result=ProcessingPool(factory,source_workers=count,idle_seconds=.01,poll_seconds=.001).run()
            elapsed=time.monotonic()-started
            self.assertEqual(sorted(outputs,key=lambda r:r['id']),corpus)
            self.assertEqual(result['completed'],12);self.assertEqual(peak[0],count)
            self.assertTrue(any(row['sources_remaining']>0 for row in maintenance))
            metrics.append({'source_workers':count,'maintenance_workers':1,'source_items':8,
                'fixed_io_seconds':.02,'elapsed_seconds':round(elapsed,6),
                'source_items_per_second':round(8/elapsed,3),
                'source_queue_completion_p95_seconds':round(sorted(latencies)[math.ceil(.95*len(latencies))-1],6),
                'peak_source_io':peak[0],'maintenance_turns':len(maintenance),'conditional_payloads_unchanged':True})
        print('PROCESSING_POOL_FIXED_LATENCY '+json.dumps(metrics))
    def test_run_worker_recovers_once_and_preserves_budget_after_idle_race(self):
        from prototypes.assertion_memory import mcp_server,processing_pool
        from prototypes.assertion_memory.storage import MemoryStore
        from prototypes.assertion_memory.queue import CaptureQueue
        from prototypes.assertion_memory.derived_work import DerivedWorkQueue
        from prototypes.assertion_memory.view_name_work import NameWorkQueue
        limits=[];recovery_sql=[]
        with tempfile.TemporaryDirectory() as directory:
            database=Path(directory)/'memory.sqlite3';config=ServiceConfig(str(database),'none',worker_idle_seconds=.01)
            class ObservedStore(MemoryStore):
                def __init__(self,path):
                    super().__init__(path)
                    self._connection.set_trace_callback(lambda sql:recovery_sql.append(sql) if 'Worker interrupted; source lease recovered' in sql else None)
            class Epoch:
                def __init__(self,*args,**kwargs):limits.append(kwargs['limit'])
                def run(self):return {'reason':'idle','completed':2 if len(limits)==1 else 1}
            derived=DerivedWorkQueue.recover_abandoned;names=NameWorkQueue.recover_abandoned
            with patch.object(mcp_server,'MemoryStore',ObservedStore),patch.object(processing_pool,'ProcessingPool',Epoch), \
                 patch.object(mcp_server,'_idle_handoff',side_effect=[False,True]), \
                 patch.object(DerivedWorkQueue,'recover_abandoned',autospec=True,side_effect=derived) as recover_derived, \
                 patch.object(NameWorkQueue,'recover_abandoned',autospec=True,side_effect=names) as recover_names:
                mcp_server.run_worker(config,limit=5)
            self.assertEqual(limits,[5,3]);self.assertEqual(len(recovery_sql),1)
            self.assertEqual(recover_derived.call_count,1);self.assertEqual(recover_names.call_count,1)
    def test_invalid_global_limit_cannot_recover_or_create_database(self):
        from prototypes.assertion_memory.mcp_server import run_worker
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'untouched.sqlite3';config=ServiceConfig(str(path),'none')
            for value in (-1,True):
                with self.assertRaises(ValueError):run_worker(config,limit=value)
            self.assertFalse(path.exists());self.assertFalse(Path(str(path)+'.worker.lock').exists())
    def test_source_workers_default_two_and_bounded(self):
        self.assertEqual(ServiceConfig('/tmp/synthetic.sqlite3','none').source_workers,2)
        for value in (0,5,True):
            with self.assertRaises(ValueError):ServiceConfig('/tmp/synthetic.sqlite3','none',source_workers=value)

if __name__=='__main__':unittest.main()
