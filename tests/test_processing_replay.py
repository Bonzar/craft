import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.model import ModelRequest
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore


class ProcessingReplayTest(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.store=MemoryStore(Path(self.folder.name)/'fixture.db');self.addCleanup(self.store.close)
        self.queue=CaptureQueue(self.store)
        self.queue.capture(CaptureInput('Permit required during pilot.','fixture','fixture://permit','pilot',
            assertion_id='permit'))
        self.claim=self.queue._claim(with_model=True,source_first=True)

    def test_cached_read_does_not_wait_for_an_unrelated_writer(self):
        self.queue._save_refresh_generation(self.claim,'key','verified-response')
        self.store._connection.execute('PRAGMA busy_timeout=1')
        with sqlite3.connect(self.store.path,isolation_level=None) as other:
            other.execute('BEGIN IMMEDIATE')
            self.assertEqual(self.queue._refresh_generation('key'),'verified-response')
            other.rollback()

    def test_retry_reuses_only_the_successfully_consumed_prefix(self):
        from prototypes.assertion_memory.processing_replay import ProcessingReplay
        calls=[]
        def provider(request):
            calls.append(request.task)
            if calls==['first','second']:raise OSError('transient fixture failure')
            return json.dumps({'status':'accepted'})
        first,second=ModelRequest('first',[]),ModelRequest('second',[])
        replay=ProcessingReplay(self.queue,self.claim,provider,('policy','frozen-source'))
        replay(first)
        with self.assertRaises(OSError):replay(second)
        retry=ProcessingReplay(self.queue,self.claim,provider,('policy','frozen-source'))
        retry(first);retry(second);retry.confirm()
        self.assertEqual(calls,['first','second','second'])
        changed=ProcessingReplay(self.queue,self.claim,provider,('policy','changed-source'))
        changed(first)
        self.assertEqual(calls[-1],'first')

    def test_unconsumed_invalid_output_is_not_replayed(self):
        from prototypes.assertion_memory.processing_replay import ProcessingReplay
        calls=[]
        def provider(request):calls.append(request.task);return 'invalid output'
        request=ModelRequest('first',[])
        ProcessingReplay(self.queue,self.claim,provider,'policy')(request)
        ProcessingReplay(self.queue,self.claim,provider,'policy')(request)
        self.assertEqual(calls,['first','first'])

    def test_repeated_repair_request_is_not_poisoned_by_its_previous_output(self):
        from prototypes.assertion_memory.processing_replay import ProcessingReplay
        calls=[]
        def provider(request):calls.append(request.task);return str(len(calls))
        replay=ProcessingReplay(self.queue,self.claim,provider,'policy')
        request=ModelRequest('repair',[])
        self.assertEqual(replay(request),'1')
        self.assertEqual(replay(request),'2')

    def test_cached_result_does_not_bypass_a_lost_owner(self):
        from prototypes.assertion_memory.processing_replay import ProcessingReplay
        from prototypes.assertion_memory.queue import LeaseLost
        calls=[]
        def provider(request):calls.append(request.task);return '{"status":"accepted"}'
        request=ModelRequest('verified',[])
        first=ProcessingReplay(self.queue,self.claim,provider,'policy')
        first(request);first.confirm()
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET lease_token='different-owner' WHERE id=?",(self.claim.job.id,))
        with self.assertRaises(LeaseLost):
            ProcessingReplay(self.queue,self.claim,provider,'policy')(request)
        self.assertEqual(calls,['verified'])

    def test_custom_provider_without_policy_identity_is_not_cached(self):
        from prototypes.assertion_memory.processing_replay import ProcessingReplay
        calls=[]
        def provider(request):calls.append(request.task);return '{"status":"accepted"}'
        request=ModelRequest('verified',[])
        for _ in range(2):
            replay=ProcessingReplay(self.queue,self.claim,provider,None)
            replay(request);replay.confirm()
        self.assertEqual(calls,['verified','verified'])

    def test_real_link_worker_retry_does_not_repeat_completed_model_call(self):
        from datetime import datetime,timedelta,timezone
        from unittest.mock import patch
        from prototypes.assertion_memory.engine import MemoryEngine
        from prototypes.assertion_memory.worker import LinkWorker
        # Use a new fixture store so the synthetic clock owns every lease.
        with MemoryStore(':memory:') as store:
            now=[datetime(2026,1,1,tzinfo=timezone.utc)]
            queue=CaptureQueue(store,clock=lambda:now[0])
            store.append_assertion(assertion_id='old',text='Pilot permit covers inspection.',actor='fixture',
                source='fixture://old',episode='pilot',scope='s',recorded_at='2025-01-01')
            queue.capture(CaptureInput('Pilot inspection requires a permit.','fixture','fixture://new','pilot',
                assertion_id='new',scope='s'))
            calls=[]
            def provider(request):calls.append(request.task);return '{"links":[],"branches":[]}'
            worker=LinkWorker(queue,MemoryEngine(store,model=provider),source_first=True,lane='source')
            with patch.object(queue,'_save_links',side_effect=OSError('fixture commit failure')):
                self.assertEqual(worker.run_once().status,'pending')
            now[0]+=timedelta(seconds=3)
            result=worker.run_once()
            self.assertEqual(result.phase,'refresh')
            self.assertEqual(len(calls),1)
            self.assertEqual(store._connection.execute("SELECT count(*) FROM memory_refresh_generations "
                "WHERE request_key LIKE 'stage-replay:%'").fetchone()[0],0)

    def test_source_commit_retry_halves_calls_without_changing_saved_meaning(self):
        from datetime import datetime,timedelta,timezone
        from unittest.mock import patch
        from prototypes.assertion_memory.engine import MemoryEngine
        from prototypes.assertion_memory.worker import LinkWorker
        from prototypes.assertion_memory.processing_replay import ProcessingReplay
        class NoReplay:
            def __init__(self,queue,claim,provider,binding):self.provider=provider
            def __call__(self,request):return self.provider(request)
            def confirm(self):return None
            def clear(self,connection):return None
        results=[]
        for replay_type in (NoReplay,ProcessingReplay):
            with MemoryStore(':memory:') as store:
                now=[datetime(2026,1,1,tzinfo=timezone.utc)]
                queue=CaptureQueue(store,clock=lambda:now[0])
                text='A permit is required only during the pilot.'
                event=queue.capture_event('fixture','one',text,source='fixture://source',scope='s')
                calls=[]
                def provider(request):
                    calls.append(request.task)
                    if request.task.startswith('Extract'):
                        return json.dumps({'assertions':[{'text':text,'quote':text}]})
                    if request.task.startswith('Verify contextual'):
                        return json.dumps({'verdicts':[{'index':0,'status':'accepted','reason':'Exact qualification.'}]})
                    if request.task=='Review source material coverage':
                        return json.dumps({'source_coverage':{'status':'accepted','reason':'All source meaning.','omissions':[]}})
                    raise AssertionError(request.task)
                worker=LinkWorker(queue,MemoryEngine(store,model=provider),source_first=True,lane='source')
                with patch('prototypes.assertion_memory.processing_replay.ProcessingReplay',replay_type):
                    with patch.object(queue,'_finish_extraction',side_effect=OSError('fixture commit failure')):
                        self.assertEqual(worker.run_once().status,'pending')
                    now[0]+=timedelta(seconds=3)
                    self.assertEqual(worker.run_once().status,'completed')
                results.append((len(calls),[item.text for item in store.list_assertions()]))
        self.assertEqual(results,[(6,[text]),(3,[text])])
