"""Actual run_worker/source queue with deterministic providers, never external models."""
import json
import fcntl
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from prototypes.assertion_memory.model import CandidateSearch
from prototypes.assertion_memory.mcp_server import ServiceConfig,run_worker
from prototypes.assertion_memory.thought_service import ThoughtService
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.storage import MemoryStore

def source_draft(record):
    fragments=record['fragments']
    return {'thoughts':[{'text':''.join(fragment['text'] for fragment in fragments),
                        'question':None,'support_fragments':[fragment['option'] for fragment in fragments]}]}


class PoolSourceIntegrationTest(unittest.TestCase):
    def test_real_source_jobs_overlap_on_two_isolated_provider_instances(self):
        barrier=threading.Barrier(2);mutex=threading.Lock();active=[0];peak=[0];owners=[];calls=[]
        class Provider:
            def __init__(self):self.owner=threading.get_ident();self.identity=id(self);owners.append((self.owner,self.identity))
            def __call__(self,request):
                if self.owner!=threading.get_ident():raise AssertionError('provider crossed thread boundary')
                calls.append((self.owner,self.identity,request.task))
                if request.task=='Construct thought':
                    target=next(row for row in request.records if row['role']=='source')
                    with mutex:active[0]+=1;peak[0]=max(peak[0],active[0])
                    try:barrier.wait(timeout=3);time.sleep(.01)
                    finally:
                        with mutex:active[0]-=1
                    return json.dumps(source_draft(target))
                if request.task=='Verify thought':
                    return json.dumps({'verdict':'accept','text':'Synthetic literal condition retained.'})
                raise AssertionError('unexpected provider work '+request.task)
        def components(config,store):return Provider(),CandidateSearch(),'pool-fixture'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.sqlite3';config=ServiceConfig(str(path),'none',source_workers=2,worker_idle_seconds=.01)
            texts=['Synthetic office A activates only after trial confirmation.','Synthetic office B retains manual exit until an independent check.']
            with MemoryStore(str(path)) as store:
                service=config.service(store)
                for i,text in enumerate(texts):service.capture_message('fixture-'+str(i),'m','fixture','user',text,0,'fixture://'+str(i),'s')
            with patch.object(ServiceConfig,'components',components):run_worker(config,limit=2)
            with MemoryStore(str(path)) as store:
                self.assertEqual(sorted(row.text for row in ThoughtStore(store).list_thoughts()),sorted(texts))
                statuses=store._connection.execute("SELECT status,phase FROM memory_capture_jobs WHERE event_id IS NOT NULL").fetchall()
                self.assertEqual([row['status'] for row in statuses],['completed','completed'])
            extraction=[row for row in calls if row[2]=='Construct thought']
            self.assertEqual(len(extraction),2);self.assertEqual(len({row[0] for row in extraction}),2)
            self.assertEqual(len({row[1] for row in extraction}),2);self.assertEqual(peak[0],2)
            self.assertNotIn(threading.get_ident(),{row[0] for row in extraction})
    def test_sigint_drains_the_current_source_and_closes_its_trace(self):
        from prototypes.assertion_memory.processing_pool import ProcessingPool
        started = threading.Event()
        resume = threading.Event()

        class Provider:
            def __call__(self, request):
                if request.task == 'Construct thought':
                    started.set()
                    if not resume.wait(3):
                        raise AssertionError('supervisor did not signal the provider')
                    target = next(row for row in request.records if row['role'] == 'source')
                    return json.dumps(source_draft(target))
                if request.task == 'Verify thought':
                    return json.dumps({'verdict': 'accept', 'text': 'Synthetic evidence preserved.'})
                raise AssertionError(request.task)

        original_beat = ProcessingPool._beat

        def beat(pool):
            if started.is_set() and not resume.is_set():
                try:
                    signal.raise_signal(signal.SIGINT)
                finally:
                    resume.set()
            return original_beat(pool)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'memory.sqlite3'
            config = ServiceConfig(str(path), 'none', source_workers=1, worker_idle_seconds=.01)
            with MemoryStore(path) as store:
                service = config.service(store)
                for i in range(2):
                    service.capture_message(f'session-{i}', 'm', 'fixture', 'user',
                        f'Synthetic conditional report {i}', 0, f'fixture://{i}', 's')
            original_handler = signal.getsignal(signal.SIGINT)
            with patch.object(ServiceConfig, 'components', lambda config, store: (
                    Provider(), CandidateSearch(), 'pause-fixture')), patch.object(ProcessingPool, '_beat', beat):
                run_worker(config)
            self.assertIs(signal.getsignal(signal.SIGINT), original_handler)
            with MemoryStore(path) as store, store.transaction() as db:
                jobs = db.execute('SELECT status, attempts, lease_token FROM memory_capture_jobs '
                    'WHERE event_id IS NOT NULL ORDER BY status').fetchall()
                self.assertEqual([tuple(row) for row in jobs], [('completed', 1, None), ('waiting_model', 0, None)])
                traces = db.execute("SELECT status, finished_at FROM memory_thought_runs WHERE operation='source'").fetchall()
                self.assertEqual(len(traces), 1)
                self.assertEqual(traces[0]['status'], 'completed')
                self.assertIsNotNone(traces[0]['finished_at'])
                self.assertEqual(db.execute("SELECT count(*) FROM memory_thought_model_calls WHERE status='running'").fetchone()[0], 0)

    def test_cancellation_after_claim_before_provider_releases_job_cleanly(self):
        from prototypes.assertion_memory.model import processing_cancellation
        from prototypes.assertion_memory.thought_processing import TracedThoughtWorker

        with MemoryStore(':memory:') as store:
            def unexpected_model(_request):
                self.fail('Cancellation should stop the job before starting the model')

            service = ThoughtService(store, unexpected_model)
            service.capture_message('synthetic-session', 'm', 'fixture', 'user',
                                    'A synthetic report.', 0, 'fixture://source', 's')
            worker = TracedThoughtWorker(service.queue, service.engine)
            source = worker._claim_source()
            thought = service.thoughts.publish('A synthetic thought.')
            worker.work.enqueue([thought])
            maintenance = worker.work.claim()
            with processing_cancellation(lambda: True):
                worker._source(source)
                worker._consolidate(maintenance)
            self.assertEqual(worker.queue.get(source.job.id).status, 'pending')
            self.assertEqual(worker.work.get(thought.id)['status'], 'pending')
            with store.transaction() as db:
                self.assertEqual([row['status'] for row in db.execute(
                    'SELECT status FROM memory_thought_runs ORDER BY rowid')], ['interrupted', 'interrupted'])

    def test_explicit_cancel_reaps_only_the_owned_model_and_requeues_the_attempt(self):
        from prototypes.assertion_memory.model import CodexModel
        from prototypes.assertion_memory.processing_pool import ProcessingPool

        original_beat = ProcessingPool._beat
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'memory.sqlite3'
            pid_path = Path(directory) / 'model.pid'
            model_script = r'''
import json, os, signal, sys, time
from pathlib import Path

payload = json.loads(sys.stdin.read().split('\n\n', 1)[1])
if payload['task'] == 'Construct thought':
    source = next(row for row in payload['records'] if row['role'] == 'source')
    elements = source['fragments']
    print(json.dumps({'thoughts': [{'text': ''.join(row['text'] for row in elements),
        'question': None, 'support_fragments': [row['option'] for row in elements]}]}))
else:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.write(1, bytes([0xe2]))
    Path(sys.argv[1]).write_text(str(os.getpid()))
    time.sleep(60)
'''
            command = (sys.executable, '-c', model_script, str(pid_path))
            config = ServiceConfig(str(path), 'none', source_workers=1, worker_idle_seconds=.01)
            with MemoryStore(path) as store:
                config.service(store).capture_message('session', 'm', 'fixture', 'user',
                    'A synthetic conditional report.', 0, 'fixture://source', 's')
            notified = False

            def beat(pool):
                nonlocal notified
                if not notified and pid_path.is_file() and pid_path.stat().st_size:
                    notified = True
                    signal.raise_signal(signal.SIGUSR1)
                return original_beat(pool)

            previous = signal.signal(signal.SIGUSR1, signal.SIG_IGN)
            unrelated = subprocess.Popen((sys.executable, '-c', 'import time; time.sleep(60)'),
                                         start_new_session=True)
            try:
                with patch.object(ServiceConfig, 'components', lambda config, store: (
                        CodexModel(command, timeout=5), CandidateSearch(), 'cancel-fixture')), \
                        patch.object(ProcessingPool, '_beat', beat):
                    run_worker(config, limit=1)
                self.assertEqual(signal.getsignal(signal.SIGUSR1), signal.SIG_IGN)
                self.assertIsNone(unrelated.poll())
            finally:
                signal.signal(signal.SIGUSR1, previous)
                unrelated.terminate()
                unrelated.wait(timeout=3)

            self.assertTrue(notified)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid_path.read_text()), 0)
            with MemoryStore(path) as store, store.transaction() as db:
                job = db.execute('SELECT status, attempts, lease_token FROM memory_capture_jobs '
                                 'WHERE event_id IS NOT NULL').fetchone()
                self.assertEqual(tuple(job), ('pending', 0, None))
                source = db.execute("SELECT status, finished_at FROM memory_thought_runs WHERE operation='source'").fetchone()
                self.assertEqual(source['status'], 'interrupted')
                self.assertIsNotNone(source['finished_at'])
                calls = db.execute('SELECT status, finished_at FROM memory_thought_model_calls ORDER BY started_at').fetchall()
                self.assertEqual([row['status'] for row in calls], ['succeeded', 'interrupted'])
                self.assertTrue(all(row['finished_at'] is not None for row in calls))
                cached = db.execute('SELECT response FROM memory_refresh_generations').fetchall()
                self.assertEqual(len(cached), 1)
                self.assertEqual(json.loads(cached[0]['response'])['thoughts'][0]['text'],
                                 'A synthetic conditional report.')

    def test_cancel_terminates_owned_descendant_with_redirected_streams(self):
        from prototypes.assertion_memory.model import (ModelRequest, ProcessingCancelled, SubprocessModel,
                                                       processing_cancellation)

        child_script = r'''
import fcntl, signal, sys, time
from pathlib import Path

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    Path(sys.argv[2]).touch()
    deadline = time.monotonic() + 10
    while not Path(sys.argv[3]).exists() and time.monotonic() < deadline:
        time.sleep(.02)
'''
        model_script = r'''
import subprocess, sys

subprocess.call([sys.executable, '-c', *sys.argv[1:]], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
'''
        with tempfile.TemporaryDirectory() as directory:
            process_lock, ready, stop = (Path(directory) / name for name in ('lock', 'ready', 'stop'))
            command = (sys.executable, '-c', model_script, child_script,
                       str(process_lock), str(ready), str(stop))
            model = SubprocessModel(command, timeout=5)
            try:
                with processing_cancellation(ready.exists), self.assertRaises(ProcessingCancelled):
                    model(ModelRequest('Synthetic cancellation', [], 'Return an empty object.'))
                with process_lock.open('a') as lock:
                    deadline = time.monotonic() + 1
                    while True:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic() >= deadline:
                                self.fail('The owned model descendant still held its process lock after cancellation')
                            time.sleep(.02)
            finally:
                stop.touch()
                with process_lock.open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)

    def test_supervisor_heartbeat_keeps_real_claims_alive_during_longer_provider_wait(self):
        from contextlib import contextmanager
        from prototypes.assertion_memory.processing_pool import ProcessingPool,ActiveClaims
        from prototypes.assertion_memory.queue import CaptureQueue
        from prototypes.assertion_memory.thought_worker import ThoughtWorker
        lease=.15;claims=ActiveClaims();renewed=[];barrier=threading.Barrier(2)
        class Provider:
            def __call__(self,request):
                if request.task=='Construct thought':
                    barrier.wait(timeout=3);time.sleep(.35)
                    target=next(row for row in request.records if row['role']=='source')
                    return json.dumps(source_draft(target))
                if request.task=='Verify thought':
                    return json.dumps({'verdict':'accept','text':'Synthetic complete target.'})
                raise AssertionError(request.task)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.sqlite3'
            with MemoryStore(str(path)) as supervisor:
                service=ThoughtService(supervisor)
                for i in range(2):service.capture_message('session-'+str(i),'m','fixture','user','Synthetic conditional report '+str(i),0,'fixture://'+str(i),'s')
                @contextmanager
                def factory(lane,observer):
                    with MemoryStore(str(path)) as store:
                        engine=ThoughtService(store,Provider()).engine
                        yield ThoughtWorker(CaptureQueue(store,lease_seconds=lease),engine,
                            lane=lane,lock_owned=True,claim_observer=observer)
                def heartbeat():
                    if claims.snapshot():
                        with supervisor.transaction(write=True) as db:renewed.append(claims.renew(db,lease_seconds=lease))
                with Path(str(path)+'.worker.lock').open('a') as lock:
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    result=ProcessingPool(factory,source_workers=2,limit=2,claims=claims,heartbeat=heartbeat,
                        heartbeat_seconds=.01,poll_seconds=.002).run()
                rows=supervisor._connection.execute("SELECT status,error FROM memory_capture_jobs WHERE event_id IS NOT NULL").fetchall()
                self.assertEqual([row['status'] for row in rows],['completed','completed'],[dict(row) for row in rows])
                self.assertTrue(all(row['error'] is None for row in rows));self.assertGreater(sum(renewed),2)
                self.assertEqual(result['completed'],2);self.assertEqual(claims.snapshot(),())

if __name__=='__main__':unittest.main()
