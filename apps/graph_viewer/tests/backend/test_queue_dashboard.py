"""Existing authenticated capabilities bridge and bounded live queue behavior."""
import asyncio
import fcntl
import json
from contextlib import nullcontext
import os
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server import queue_dashboard
from apps.graph_viewer.server.queue_dashboard import HostedQueueDashboard, dashboard_events, decode_cursor
from prototypes.assertion_memory import dashboard_projection
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_service import ThoughtService
# Include the companion transport tests in the existing protected Base PR test selection.
from test_prod_owner_bridge import ProdOwnerBridgeTests

ORIGIN='https://owner.private.test'
GATEWAY='graph.bridge.test'
TAG='assertion-memory-v1.2.3'


class FixtureDash(HostedQueueDashboard):
    def __init__(self,path):
        self.db=path
        self.stage='test'
        self.streams=asyncio.Semaphore(8)
        self.epoch='d'*16
        self.control_lock=path.parent/'deploy.lock'
        self.control_lock.touch(mode=0o600)
        self.publisher_uid=os.getuid()

    def metadata(self):
        return {'stage':self.stage,'release_version':TAG,'model_configured':True,
                'interactive_enabled':True,'processing_session_ids':('session',),'auto_process_fresh_mac':False}


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

    def test_metadata_uses_only_leased_minimal_projection_and_db_identity(self):
        run=self.root/'projection'
        directory=run/'test'
        directory.mkdir(parents=True,mode=0o750)
        directory.chmod(0o750)
        alternate=self.root/'wrong.sqlite3'
        alternate.touch()
        cfg=SimpleNamespace(db=str(self.path),provider='codex',
            processing_session_ids=('session',),auto_process_fresh_mac=False,
            endpoint='NEVER SERIALIZE ME',credential='SECRET')
        with patch.object(dashboard_projection,'RUN',run), \
             patch.object(queue_dashboard,'SCOPES',{'test':('assertion-test-deploy',self.path)}), \
             patch.object(queue_dashboard.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.getuid())):
            projection=dashboard_projection.projection_path('test')
            owned=HostedQueueDashboard('test',projection,self.path)
            with self.assertRaises(OSError):
                owned.metadata()
            with dashboard_projection.publish(stage='test',config=cfg,release=TAG,
                    generation='a'*64,interactive_enabled=True) as published:
                self.assertTrue(published)
                raw=projection.read_text()
                self.assertNotIn('SECRET',raw)
                self.assertNotIn('NEVER SERIALIZE',raw)
                self.assertNotIn(str(self.path),raw)
                self.assertEqual(owned.metadata()['processing_session_ids'],['session'])
                with self.assertRaises(ValueError):
                    dashboard_projection.read(stage='test',uid=os.getuid(),database=alternate)
                with self.assertRaises(ValueError):
                    HostedQueueDashboard('prod',projection,self.path)
                with self.assertRaises(ValueError):
                    HostedQueueDashboard('test',Path('/wrong/private-config.json'),self.path)
                with self.assertRaises(ValueError):
                    HostedQueueDashboard('test',projection,alternate)
                with dashboard_projection.publish(stage='test',config=cfg,release=TAG,
                        generation='b'*64,interactive_enabled=False) as duplicate:
                    self.assertFalse(duplicate)
                    self.assertEqual(owned.metadata()['generation'],'a'*64)
            with self.assertRaisesRegex(ValueError,'No live scheduler publisher'):
                owned.metadata()

    def test_legacy_create_app_binds_only_verified_test_unit_and_release(self):
        run=self.root/'projection'
        directory=run/'test'
        directory.mkdir(parents=True)
        directory.chmod(0o750)
        scoped=self.root/'deploy'
        revision,archive='3'*40,'4'*64
        active=scoped/'releases'/(revision+'-'+archive)
        active.mkdir(parents=True)
        (scoped/'current').symlink_to(active,target_is_directory=True)
        env={
            queue_dashboard.DASHBOARD_STAGE_ENV:'test',
            queue_dashboard.DASHBOARD_PATH_ENV:str(directory/'scheduler.json'),
        }
        good_uid=os.getuid()
        cfg=SimpleNamespace(db=str(self.path),provider='codex',processing_session_ids=('session',),
                            auto_process_fresh_mac=False)
        web=dict(password='synthetic-owner-strong',public_origin=ORIGIN,
                 allowed_proxy_hosts=[GATEWAY],bridge_read_token_file=self.key)
        headers={'origin':ORIGIN,'authorization':'Bearer '+'c'*64}
        endpoint='/api/v1/capabilities?queue_status=1'
        with patch.object(dashboard_projection,'RUN',run), \
             patch.object(queue_dashboard,'TEST_DEPLOY_ROOT',scoped), \
             patch.object(queue_dashboard,'SCOPES',{'test':('assertion-test-deploy',self.path)}), \
             patch.object(queue_dashboard,'LOADED_RELEASE',active), \
             patch.object(queue_dashboard.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=good_uid)):
            self.assertIsNone(queue_dashboard.configured_for_legacy_test_reader(self.path,env={}))
            self.assertIsNone(queue_dashboard.configured_for_legacy_test_reader(self.path,env={
                **env,queue_dashboard.DASHBOARD_STAGE_ENV:'prod'}))
            self.assertIsNone(queue_dashboard.configured_for_legacy_test_reader(self.path,env={
                **env,queue_dashboard.DASHBOARD_PATH_ENV:'/var/private/config.json'}))
            self.assertIsNone(queue_dashboard.configured_for_legacy_test_reader(self.root/'opposite',env=env))
            with patch.object(queue_dashboard.os,'geteuid',return_value=good_uid+1):
                self.assertIsNone(queue_dashboard.configured_for_legacy_test_reader(self.path,env=env))
            with patch.object(queue_dashboard,'LOADED_RELEASE',Path('/opt/untrusted')):
                self.assertIsNone(queue_dashboard.configured_for_legacy_test_reader(self.path,env=env))
            self.seed()
            with patch.dict(os.environ,env,clear=False):
                # The actual Sites wrapper doesn't pass a queue_dashboard argument.
                app=create_app(self.path,self.root/'web',**web)
            self.assertIsNotNone(app.state.queue_dashboard)
            generation=dashboard_projection.release_generation(revision,archive,TAG,'test')
            with TestClient(app,base_url='https://'+GATEWAY) as c:
                self.assertEqual(c.get(endpoint,headers=headers).status_code,503)
                self.assertEqual(c.get(endpoint).status_code,401)
                with dashboard_projection.publish(stage='test',config=cfg,release=TAG,
                        generation='f'*64,interactive_enabled=True):
                    self.assertEqual(c.get(endpoint,headers=headers).status_code,503)
                with dashboard_projection.publish(stage='test',config=cfg,release=TAG,
                        generation=generation,interactive_enabled=True):
                    r=c.get(endpoint,headers=headers)
                    self.assertEqual(r.status_code,200,r.text)
                    self.assertEqual(r.json()['release_version'],TAG)
                    self.assertEqual(r.json()['release_generation'],generation)
                    self.assertEqual(r.json()['capabilities'],{'live':True})
                    self.assertEqual(r.json()['readiness'],[])
                    self.assertEqual(r.json()['queues'][0]['items'][0]['position'],1)
                    (scoped/'current').unlink()
                    next_release = scoped/'releases'/('5'*40+'-'+'6'*64)
                    next_release.mkdir()
                    (scoped/'current').symlink_to(next_release)
                    self.assertEqual(c.get(endpoint,headers=headers).status_code,503)

    def test_prod_reader_uses_separate_root_pinned_uid_and_current_scope(self):
        run=self.root/'projection'
        directory=run/'prod'
        directory.mkdir(parents=True,mode=0o750)
        directory.chmod(0o750)
        scoped=self.root/'production'
        revision,archive='3'*40,'4'*64
        active=scoped/'releases'/(revision+'-'+archive)
        active.mkdir(parents=True)
        (scoped/'current').symlink_to(active,target_is_directory=True)
        uid=os.getuid()
        policy=self.root/'policy.json'
        policy.write_text(json.dumps({'stage':'prod','account':'existing-graph','uid':uid}))
        policy.chmod(0o644)
        stagekey=queue_dashboard.DASHBOARD_STAGE_ENV
        pathkey=queue_dashboard.DASHBOARD_PATH_ENV
        env={stagekey:'prod',pathkey:str(directory/'scheduler.json')}
        graph_cfg={'password':'synthetic-owner-strong','public_origin':ORIGIN,
                   'allowed_proxy_hosts':[GATEWAY],'bridge_read_token_file':self.key}
        h={'origin':ORIGIN,'authorization':'Bearer '+'c'*64}
        source=SimpleNamespace(db=str(self.path),provider='codex',
            processing_session_ids=('session',),auto_process_fresh_mac=False)
        self.seed()
        tested_policy=queue_dashboard._prod_reader_uid
        with patch.object(queue_dashboard.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=uid)), \
             patch.object(dashboard_projection,'RUN',run), \
             patch.object(queue_dashboard,'PROD_READER_POLICY',policy), \
             patch.object(queue_dashboard,'_prod_reader_uid',side_effect=lambda p:tested_policy(p,policy_owner=uid)), \
             patch.object(queue_dashboard,'PROD_DEPLOY_ROOT',scoped), \
             patch.object(queue_dashboard,'SCOPES',{'prod':('publisher',self.path)}), \
             patch.object(queue_dashboard,'LOADED_RELEASE',active), \
             patch.object(queue_dashboard,'_prod_read_fence',nullcontext):
            self.assertEqual(tested_policy(policy,policy_owner=uid),uid)
            policy.chmod(0o664)
            self.assertIsNone(queue_dashboard.configured_for_legacy_reader(self.path,env=env))
            policy.chmod(0o644)
            self.assertIsNone(queue_dashboard.configured_for_legacy_reader(self.path,env={
                **env,pathkey:str(run/'test'/'scheduler.json')}))
            self.assertIsNone(queue_dashboard.configured_for_legacy_reader(self.root/'other',env=env))
            with patch.object(queue_dashboard.os,'geteuid',return_value=uid+1):
                self.assertIsNone(queue_dashboard.configured_for_legacy_reader(self.path,env=env))
            with patch.dict(os.environ,env,clear=False):
                app=create_app(self.path,self.root/'web-prod',**graph_cfg)
            self.assertEqual(app.state.queue_dashboard.stage,'prod')
            path='/api/v1/capabilities?queue_status=1'
            gen=dashboard_projection.release_generation(revision,archive,TAG,'prod')
            with TestClient(app,base_url='https://'+GATEWAY) as c:
                self.assertEqual(c.get(path,headers=h).status_code,503)
                self.assertEqual(c.get(path).status_code,401)
                self.assertEqual(c.put('/api/v1/processing/pause',headers=h,json={}).status_code,401)
                with dashboard_projection.publish(stage='prod',config=source,release=TAG,
                    generation=gen,interactive_enabled=True):
                    answer=c.get(path,headers=h)
                    self.assertEqual(answer.status_code,200,answer.text)
                    self.assertEqual(answer.json()['stage'],'prod')
                    self.assertEqual(answer.json()['release_generation'],gen)
                    self.assertNotIn('DO NOT LEAK',answer.text)
                    policy.unlink()
                    self.assertEqual(c.get(path,headers=h).status_code,503)

    def test_prod_dashboard_uses_shared_lane_fence_not_write_or_replace(self):
        parent=self.root/'root-transition'
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
        fence=parent/'fence.lock'
        fence.touch(mode=0o644)
        fence.chmod(0o644)
        with queue_dashboard._prod_read_fence(fence,owner=os.getuid()):
            with fence.open('rb') as f:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        fence.unlink()
        with self.assertRaises(FileNotFoundError):
            with queue_dashboard._prod_read_fence(fence,owner=os.getuid()):
                pass
        self.assertFalse(fence.exists())

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

    def test_pause_mutation_is_explicit_same_origin_owner_cookie_only(self):
        self.seed()
        opts={'password':'synthetic-owner-strong','public_origin':ORIGIN,'allowed_proxy_hosts':[GATEWAY],
              'bridge_read_token_file':self.key,'queue_dashboard':self.dash}
        auth={'origin':ORIGIN,'authorization':'Bearer '+'c'*64}
        url='/api/v1/processing/pause'
        read='/api/v1/capabilities?queue_status=1'
        req={'stage':'test','paused':True,'expected_revision':'0'*32}
        disabled=create_app(self.path,self.root/'disabled',**opts)
        with TestClient(disabled,base_url=ORIGIN) as cli:
            login=cli.post('/api/v1/session',headers={'origin':ORIGIN},json={'password':opts['password']}).json()
            h={'origin':ORIGIN,'x-csrf-token':login['csrf_token']}
            self.assertFalse(cli.get(read,headers=h).json()['processing_control']['can_change'])
            self.assertEqual(cli.put(url,headers=h,json=req).status_code,503)
        enabled=create_app(self.path,self.root/'enabled',queue_control=True,**opts)
        with TestClient(enabled,base_url=ORIGIN) as cli:
            self.assertFalse(cli.get(read,headers=auth).json()['processing_control']['can_change'])
            self.assertEqual(cli.put(url,headers=auth,json=req).status_code,401)
            login=cli.post('/api/v1/session',headers={'origin':ORIGIN},json={'password':opts['password']}).json()
            h={'origin':ORIGIN,'x-csrf-token':login['csrf_token']}
            self.assertEqual(cli.put(url,headers={'origin':ORIGIN},json=req).status_code,403)
            self.assertEqual(cli.put(url,headers={**h,'origin':'https://wrong.test'},json=req).status_code,403)
            self.assertEqual(cli.put(url,headers=h,json={**req,'stage':'prod'}).status_code,409)
            self.assertEqual(cli.put(url,headers=h,json={**req,'paused':'true'}).status_code,400)
            self.assertEqual(cli.put(url,headers={**h,'content-type':'application/json'},
                content='{"stage":"prod","stage":"test","paused":true,"expected_revision":"'+'0'*32+'"}').status_code,400)
            with self.dash.control_lock.open('rb') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                self.assertEqual(cli.put(url,headers=h,json=req).status_code,503)
                self.assertFalse(cli.get(read,headers=h).json()['processing_control']['can_change'])
            r=cli.put(url,headers=h,json=req)
            self.assertEqual(r.status_code,200,r.text)
            revision=r.json()['revision']
            self.assertTrue(r.json()['paused'])
            self.assertEqual(cli.put(url,headers=h,json={**req,'paused':False}).status_code,409)
            status=cli.get(read,headers=h).json()
            self.assertEqual(status['processing_control'],{'paused':True,'revision':revision,'can_change':True})
            self.assertIsNone(status['queues'][0]['items'][0]['position'])
            self.assertEqual(status['queues'][0]['items'][0]['status'],'paused')
            r=cli.put(url,headers=h,json={**req,'expected_revision':revision,'paused':False})
            self.assertEqual(r.status_code,200,r.text)
            self.assertFalse(r.json()['paused'])
            self.assertEqual(cli.get(read,headers=h).json()['queues'][0]['items'][0]['position'],1)

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
class OwnerBrowserStartup(unittest.TestCase):
    @staticmethod
    def args(**changes):
        options=dict(queue_status_config=Path('/canonical/projection.json'),queue_status_stage='prod',
            source=Path('/canonical/memory.sqlite3'),queue_control=False,browser_owner_api=True,
            browser_owner_clear=False,public_origin='https://owner.example.test',
            bridge_read_token_file=None,database_clear_read_token_file=None,database_clear_lane=None,
            allowed_proxy_host=[],test_direct_events_origin=None,synthetic_fixture=False)
        return SimpleNamespace(**(options|changes))

    def test_dark_and_missing_prod_writer_fail_before_opening_scope(self):
        from apps.graph_viewer.server.__main__ import configure_owner_lanes
        with patch.object(queue_dashboard,'configured_browser_dashboard') as binder:
            self.assertEqual(configure_owner_lanes(self.args(browser_owner_api=False,queue_status_stage=None,
                                                            queue_status_config=None)),(None,None))
            with self.assertRaisesRegex(ValueError,'installed private Base writer'):
                configure_owner_lanes(self.args())
            binder.assert_not_called()

    def test_browser_flags_follow_existing_private_writer_readiness_in_prod(self):
        from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
        for stage,private_ready,want_ready in [('prod',False,False),('prod',True,True),('test',False,True)]:
            with self.subTest(stage=stage,private_ready=private_ready):
                api=BrowserOwnerAPI(stage=stage,perform=lambda *_:self.fail(),
                    queue_snapshot=lambda **_:{'stage':stage,'processing_control':{'can_change':private_ready}})
                raw=asyncio.run(api.snapshot(None,10,0))
                value=json.loads(raw.body)
                self.assertIs(value['capabilities']['standalone_pause'],want_ready)
                self.assertIs(value['capabilities']['queue_processing_policy'],want_ready)
                self.assertIs(value['processing_control']['can_change'],False)

    def test_installed_prod_adapter_accepts_only_canonical_credential_directory(self):
        import sys
        from apps.graph_viewer.server.__main__ import PROD_OWNER_CREDENTIALS,installed_prod_owner_api
        from apps.graph_viewer.server import browser_owner_api
        from unittest.mock import Mock
        dashboard=SimpleNamespace(stage='prod')
        bridge=object()
        constructor=Mock(return_value=bridge)
        module=SimpleNamespace(configured_prod_owner_bridge=constructor)
        self.assertEqual(str(PROD_OWNER_CREDENTIALS),
                         '/run/credentials/assertion-base-prod-browser-graph.service')
        for wrongly_configured in ({},
                {'CREDENTIALS_DIRECTORY':'/run/credentials/other.service'},
                {'CREDENTIALS_DIRECTORY':'/run/credentials/assertion-memory-prod-site-graph.service'}):
            with patch.dict(os.environ,wrongly_configured,clear=True), self.assertRaisesRegex(ValueError,'canonical systemd'):
                installed_prod_owner_api(dashboard)
        constructor.assert_not_called()
        with (patch.dict(os.environ,{'CREDENTIALS_DIRECTORY':str(PROD_OWNER_CREDENTIALS)},clear=True),
              patch.dict(sys.modules,{'apps.graph_viewer.server.prod_owner_bridge':module}),
              patch.object(browser_owner_api,'configured_browser_api') as configured):
            self.assertIs(installed_prod_owner_api(dashboard),configured.return_value)
            constructor.assert_called_once_with(PROD_OWNER_CREDENTIALS)
            configured.assert_called_once_with(dashboard,clear_enabled=False,prod_owner_bridge=bridge)

    def test_explicit_prod_never_uses_test_modes_or_legacy_authority(self):
        from apps.graph_viewer.server.__main__ import configure_owner_lanes
        for update in (dict(bridge_read_token_file=Path('/forbidden')),dict(database_clear_read_token_file=Path('/forbidden')),
                       dict(database_clear_lane='prod'),dict(allowed_proxy_host=['legacy.example.test']),
                       dict(queue_control=True),dict(browser_owner_clear=True),dict(synthetic_fixture=True),
                       dict(test_direct_events_origin='https://192.0.2.1'),dict(public_origin='http://not-secure.test'),
                       dict(queue_status_stage='test',queue_control=True)):
            with self.subTest(update=update), patch.object(queue_dashboard,'configured_browser_dashboard') as binder:
                with self.assertRaises(ValueError):
                    configure_owner_lanes(self.args(**update),prod_owner_api=lambda _scope: self.fail('writer called'))
                binder.assert_not_called()

    def test_only_trusted_injected_same_lane_browser_type_can_bind_prod(self):
        from apps.graph_viewer.server.__main__ import configure_owner_lanes
        from apps.graph_viewer.server import browser_owner_api
        from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
        dummy=SimpleNamespace(stage='prod')
        def factory(stage,clear=False,single=False):
            return BrowserOwnerAPI(stage=stage,clear_enabled=clear,single_clear=single,perform=lambda *_:self.fail(),
                                   queue_snapshot=lambda **_:self.fail())
        with (patch.object(queue_dashboard,'configured_browser_dashboard',return_value=dummy) as binder,
              patch.object(browser_owner_api,'configured_browser_api') as direct):
            application=factory('prod')
            dashboard,browser=configure_owner_lanes(self.args(),prod_owner_api=lambda d: application if d is dummy else None)
            self.assertIs(dashboard,dummy);self.assertIs(browser,application)
            binder.assert_called_once_with('prod',self.args().queue_status_config,self.args().source)
            for wrong in (None,SimpleNamespace(stage='prod',clear_enabled=False),factory('test'),factory('prod',True)):
                with self.assertRaisesRegex(ValueError,'Wrong private Base writer'):
                    configure_owner_lanes(self.args(),prod_owner_api=lambda _d: wrong)
            protected_clear=factory('prod',True,True)
            self.assertIs(configure_owner_lanes(self.args(),prod_owner_api=lambda _: protected_clear)[1],
                          protected_clear)
            direct.assert_not_called()
            configure_owner_lanes(self.args(queue_status_stage='test'))
            direct.assert_called_once_with(dummy,clear_profile=None,graph_node_bridge=None,
                                           ordinary_test_clear=True)

    def test_prod_reader_policy_canonical_db_release_and_fence_are_distinct_from_writer(self):
        from contextlib import contextmanager
        root=Path(tempfile.mkdtemp())
        self.addCleanup(__import__('shutil').rmtree,root)
        canonical=root/'prod.sqlite3'; canonical.touch()
        alternate=root/'other.sqlite3'; alternate.touch()
        proof=('a'*40,'b'*64)
        events=[]
        @contextmanager
        def fence():
            events.append('fence')
            yield
        with (patch.object(queue_dashboard,'SCOPES',{'prod':('fictional-publisher',canonical)}),
              patch.object(dashboard_projection,'RUN',root),
              patch.object(queue_dashboard.os,'getuid',return_value=4321),
              patch.object(queue_dashboard.os,'geteuid',return_value=4321),
              patch.object(queue_dashboard.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=1234)),
              patch.object(queue_dashboard,'_prod_reader_uid',return_value=4321) as reader,
              patch.object(queue_dashboard,'_active_reader_release',return_value=proof) as active,
              patch.object(queue_dashboard,'_prod_read_fence',side_effect=fence)):
            projection=dashboard_projection.projection_path('prod')
            for config,path in ((projection,alternate),(dashboard_projection.projection_path('test'),canonical)):
                with self.assertRaisesRegex(ValueError,'canonical lane'):
                    queue_dashboard.configured_browser_dashboard('prod',config,path)
            dash=queue_dashboard.configured_browser_dashboard('prod',projection,canonical)
            self.assertEqual(events,[],'activation must not wait for shared lease or publisher')
            reader.assert_called_with(queue_dashboard.PROD_READER_POLICY)
            active.assert_called_with('prod')
            self.assertEqual(dash.publisher_uid,1234);self.assertIsNone(dash.control_lock)
            with self.assertRaisesRegex(ValueError,'lease unavailable'):
                with dash.release_lease():self.fail('reader obtained write lease')
            with patch.object(dash,'_snapshot',return_value={'mock-status':True}) as local_read:
                self.assertEqual(dash.snapshot(limit=10,offset=0),{'mock-status':True})
                local_read.assert_called_once_with(limit=10,offset=0)
                self.assertEqual(events,['fence'])
            reader.return_value=1234
            with self.assertRaisesRegex(ValueError,'authorized lane'):
                queue_dashboard.configured_browser_dashboard('prod',projection,canonical)
