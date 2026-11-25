"""Existing authenticated capabilities bridge and bounded live queue behavior."""
import asyncio
from collections import deque
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.queue_dashboard import HostedQueueDashboard, dashboard_events, decode_cursor
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_service import ThoughtService

ORIGIN='https://owner.private.test'
GATEWAY='graph.bridge.test'
TAG='assertion-memory-v1.2.3'


class FixtureDash(HostedQueueDashboard):
    def __init__(self,path):
        self.db=path
        self.stage='test'
        self.streams=asyncio.Semaphore(8)
        self.epoch='d'*16

    def metadata(self):
        return {'stage':self.stage,'release_version':TAG,'model_configured':True,
                'interactive_enabled':True,'processing_session_ids':('session',)}


class QueueDashboardTest(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve()
        self.path=self.root/'canonical.sqlite3'
        self.store=MemoryStore(self.path)
        self.addCleanup(self.store.close)
        ThoughtService(self.store,None)
        self.dash=FixtureDash(self.path)
        self.key=self.root/'read-bridge'
        self.key.write_text('c'*64)
        self.key.chmod(0o600)

    def seed(self):
        time='2021-01-01T00:00:00.000000+00:00'
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_raw_events VALUES(?,?,?,?,?,?,?,?,?)',
                ('e','scope','turn','DO NOT LEAK body','user','test','scope',time,'{"native_session_id":"session"}'))
            db.execute('INSERT INTO memory_capture_jobs (id,event_id,status,phase,updated_at,available_at)'
                ' VALUES (?,?,?,?,?,?)',('job','e','pending','extract',time,time))

    def test_owner_scope_and_actual_scheduler_rows_through_existing_path(self):
        self.seed()
        app=create_app(self.path,self.root/'graph',password='synthetic-owner-strong',public_origin=ORIGIN,
                       allowed_proxy_hosts=[GATEWAY],bridge_read_token_file=self.key,queue_dashboard=self.dash)
        h={'origin':ORIGIN,'authorization':'Bearer '+'c'*64}
        path='/api/v1/capabilities?queue_status=1'
        with TestClient(app,base_url='https://'+GATEWAY) as cli:
            self.assertEqual(cli.get(path).status_code,401)
            self.assertEqual(cli.get(path,headers={**h,'origin':'https://foreign.test'}).status_code,403)
            self.assertEqual(cli.get(path+'&stage=prod',headers=h).status_code,400)
            self.assertEqual(cli.get(path+'&limit=1&limit=2',headers=h).status_code,400)
            self.assertEqual(cli.post(path,headers=h).status_code,401)
            self.assertEqual(cli.head('/api/v1/events?channel=dashboard',headers=h).status_code,200)
            r=cli.get(path,headers=h)
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.headers['cache-control'],'private, no-store')
            self.assertNotIn('DO NOT LEAK',r.text)
            self.assertEqual((r.json()['stage'],r.json()['release_version']),('test',TAG))
            self.assertEqual(r.json()['queues'][0]['items'][0]['position'],1)
            with self.store.transaction(write=True) as db:
                db.execute("UPDATE memory_capture_jobs SET status='running',phase='audit',lease_until='2099-01-01T00:00:00.000000+00:00'")
            row=cli.get(path,headers=h).json()['queues'][0]['items'][0]
            self.assertEqual((row['status'],row['phase'],row['position']),('running','audit',None))
            with self.store.transaction(write=True) as db:
                db.execute("UPDATE memory_capture_jobs SET status='completed'")
            self.assertEqual(cli.get(path,headers=h).json()['queues'][0]['items'],[])

    def test_signal_emitted_from_real_sqlite_commit_without_content(self):
        async def run():
            values=[]
            change=asyncio.Event()
            def publisher(event,data):
                values.append((event,data))
                change.set()
            task=asyncio.create_task(self.dash.watch(publisher))
            try:
                await asyncio.sleep(.15)
                self.seed()
                await asyncio.wait_for(change.wait(), 3)
                self.assertEqual(values,[('queue_changed',{'stage':'test'})])
            finally:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
        asyncio.run(run())

    def test_cursor_resync_lane_restart_and_journal_expiry(self):
        e=self.dash.epoch
        for raw in (None,f'prod:{e}:2',f'test:{"f"*16}:2',f'test:{e}:0',f'test:{e}:99','fake'):
            self.assertEqual(decode_cursor(self.dash,raw,10,5),(10,True))
        self.assertEqual(decode_cursor(self.dash,f'test:{e}:4',10,5),(4,False))

    def test_existing_event_stream_uses_safe_invalidation_and_rechecks_auth(self):
        async def run():
            m=SimpleNamespace(cursor=2,journal=deque([(1,'queue_changed',{'stage':'prod'}),
                (2,'queue_changed',{'stage':'test','body':'NEVER LEAK'})]),closed=False)
            async def connected(): return False
            owner=SimpleNamespace(session=lambda req:'ok')
            req=SimpleNamespace(headers={'last-event-id':f'test:{self.dash.epoch}:0'},
                  app=SimpleNamespace(state=SimpleNamespace(owner=owner)),is_disconnected=connected)
            events=dashboard_events(req,m,self.dash)
            self.assertEqual(await anext(events),': connected\n\n')
            frame=await anext(events)
            self.assertIn('event: queue_changed',frame)
            self.assertIn(f'id: test:{self.dash.epoch}:2',frame)
            self.assertNotIn('NEVER LEAK',frame)
            owner.session=lambda _:None
            with self.assertRaises(StopAsyncIteration):
                await asyncio.wait_for(anext(events),1)
        asyncio.run(run())
