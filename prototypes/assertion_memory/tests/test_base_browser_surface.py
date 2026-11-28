"""A browser lane reads and writes a single real local SQLite, including isolation."""
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import time

from starlette.testclient import TestClient
from apps.graph_viewer.server import queue_dashboard
from apps.graph_viewer.server.auth import COOKIE, OwnerStore
from prototypes.assertion_memory import base_browser_surface as surface
from prototypes.assertion_memory.capture_batch import authorized_machine_batch, capture
from prototypes.assertion_memory.dashboard_projection import projection_path
from prototypes.assertion_memory.queue_rest import snapshot
from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_runtime import initialize_empty
from prototypes.assertion_memory.thought_store import ThoughtStore


class SingleDatabase(TestCase):
    def test_owner_graph_and_pause_share_real_canonical_database_with_lane_isolation(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            opts={}
            origins={}
            for key in ('test','prod'):
                profile=surface.LANES[key]
                lane_root=root/key
                db=lane_root/'state'/'fixture.sqlite3'
                db.parent.mkdir(parents=True)
                with MemoryStore(db) as store:
                    initialize_empty(store)
                    MemoryService(store).capture_message("fictional","message","fixture-user","user","Synthetic only",1,"fixture")
                    thoughts=ThoughtStore(store)
                    for i in range(1 if key=='test' else 2):
                        thoughts.publish(f'fictional {key} {i}')
                owner=OwnerStore(db.parent/'graph-state','synthetic only')
                owner.reset_graph_bookmarks('assertion-memory','0'*32)
                cookie,_=owner.login('synthetic only',key)
                lane=replace(profile,base=replace(profile.base,root=lane_root,db=db))
                config=SimpleNamespace(db=str(db),provider='none',processing_session_ids=[],auto_process_fresh_mac=False)
                opts[key]=(lane,config,cookie)
                origins[key]=lane.origin
            with (patch.object(surface,'LANES',{key:cfg[0] for key,cfg in opts.items()}),
                  patch.dict(queue_dashboard.SCOPES,{'test':('assertion-test-deploy',Path(opts['test'][1].db))}),
                  patch('pwd.getpwnam',return_value=SimpleNamespace(pw_uid=994))):
                clients={}
                identity=lambda:('fixture-release','a'*64)
                for key,(lane,cfg,cookie) in opts.items():
                    dashboard=None
                    if key=='test':
                        dashboard=queue_dashboard.HostedQueueDashboard('test',projection_path('test'),Path(cfg.db),
                            activation=lambda:('a'*40,'b'*64))
                        dashboard.metadata=lambda:dict(release_version='fixture-release',generation='a'*64,
                            auto_process_fresh_mac=False,processing_session_ids=[],
                            model_configured=False,interactive_enabled=False)
                        dashboard.release_lease=nullcontext
                    private=None if key=='test' else SimpleNamespace(
                        queue_pause_ready=lambda:True,queue_clear_database=Path(cfg.db),
                        queue_clear_route=lambda *_:self.fail('Browser bypassed owner proof via legacy REST'))
                    app=surface.base_browser_app(key,config=cfg,private_port=lane.private_port,
                        verified_identity=identity,release_lease=nullcontext,interactive=False,test_dashboard=dashboard,
                        private_owner_queue=private)
                    client=self.enterContext(TestClient(app,base_url=lane.origin,cookies={COOKIE:cookie}))
                    clients[key]=client
                    for _ in range(60):
                        graph=client.get('/api/v1/graph?mode=current&history=0')
                        if graph.status_code!=202:break
                        time.sleep(.02)
                    self.assertEqual(graph.status_code,200,graph.text)
                    self.assertEqual(graph.json()['counts']['nodes'],1 if key=='test' else 2)
                    self.assertEqual(client.get('/memory/mcp').status_code,404)
                c=clients['test']
                with patch.object(surface,'LANES',{key:cfg[0] for key,cfg in opts.items()}):
                    with self.assertRaisesRegex(ValueError,'canonical Base lane'):
                        surface.canonical_lane('prod',config=opts['test'][1],private_port=8903)
                epochs={}
                for key,c in clients.items():
                    cap=c.get('/api/v1/capabilities').json()
                    self.assertTrue(cap['owner_live_graph_v1'])
                    self.assertEqual(cap['owner_live_bootstrap'],1)
                    # Same plaintext endpoint/auth semantics in both deployments;
                    # each stream must publish only its own database epoch.
                    from apps.graph_viewer.server.database_generation import database_generation, epoch_name
                    epochs[key]=epoch_name(database_generation(Path(opts[key][1].db)))
                    token=opts[key][2]
                    c.cookies.clear()
                    self.assertEqual(c.head('/api/v1/events').status_code,401)
                    c.cookies.set(COOKIE,token)
                    with patch('apps.graph_viewer.server.owner_snapshot_stream.LIVE_SECONDS',.10):
                        stream=c.get('/api/v1/events')
                    self.assertEqual(stream.status_code,200,stream.text)
                    self.assertIn('event: hello',stream.text)
                    self.assertIn(epochs[key],stream.text)
                    self.assertIn('event: reset',stream.text)
                    with patch('apps.graph_viewer.server.owner_graph_stream.LIVE_SECONDS',.10):
                        stream=c.get('/api/v1/events?wire=graph-v1')
                    self.assertEqual(stream.status_code,200,stream.text)
                    self.assertIn('event: hello',stream.text)
                self.assertNotEqual(epochs['test'],epochs['prod'])
                def paused(key):
                    cfg=opts[key][1]
                    data=snapshot(cfg,identity=identity,database=Path(cfg.db),interactive_enabled=False,
                                  limit=1,offset=0,lane=key,session_control=True)
                    return data['processing_control']['paused']
                for key in ('test','prod'):
                    c=clients[key]; url='/api/v1/processing/pause'; origin=origins[key]
                    bad=c.post(url+'/prepare',headers={'Origin':origins['prod' if key=='test' else 'test']},json={})
                    self.assertEqual(bad.status_code,403,bad.text)
                    pre=c.post(url+'/prepare',headers={'Origin':origin},json={})
                    self.assertEqual(pre.status_code,200,pre.text)
                    prep=pre.json()
                    answer=c.put(url,headers={'Origin':origin,'X-CSRF-Token':prep['csrf_token']},
                                 json={'stage':key,'paused':True,'expected_revision':prep['revision']})
                    self.assertEqual(answer.status_code,200,answer.text)
                    self.assertTrue(paused(key))
                    if key=='test': self.assertFalse(paused('prod'))
                # Only the disposable TEST lane is cleared. The exact same
                # browser API satisfies its read-backed Clear readiness.
                test=clients['test']; clear='/api/v1/processing/database/clear-v2'
                status=test.get('/api/v1/capabilities?queue_status=1')
                self.assertEqual(status.status_code,200,status.text)
                self.assertTrue(status.json()['capabilities']['owner_database_clear_post'])
                csrf=test.get('/api/v1/session').json()['csrf_token']
                headers={'Origin':origins['test'],'X-CSRF-Token':csrf}
                result=test.post(clear,headers=headers,json={'confirmation':'ОЧИСТИТЬ TEST'})
                self.assertEqual(result.status_code,200,result.text)
                for _ in range(60):
                    graph=test.get('/api/v1/graph?mode=current&history=0')
                    if graph.status_code==200 and graph.json()['counts']['nodes']==0:break
                    time.sleep(.02)
                self.assertEqual(graph.status_code,200,graph.text)
                self.assertEqual(graph.json()['counts']['nodes'],0)
                self.assertEqual(clients['prod'].get('/api/v1/graph?mode=current&history=0').json()['counts']['nodes'],2)
                for key, count in (('test',0),('prod',2)):
                    with MemoryStore(opts[key][1].db) as store:
                        with store.transaction() as db:
                            self.assertEqual(db.execute('SELECT count(*) FROM memory_thoughts').fetchone()[0],count)

                # Same public API and owner session on the independently
                # authorized synthetic PROD fixture, after isolation proof.
                prod=clients['prod']; csrf=prod.get('/api/v1/session').json()['csrf_token']
                headers={'Origin':origins['prod'],'X-CSRF-Token':csrf}
                self.assertTrue(prod.get('/api/v1/capabilities?queue_status=1').json()['capabilities']['owner_database_clear_post'])
                self.assertEqual(prod.post(clear,headers=headers,json={'confirmation':'ОЧИСТИТЬ TEST'}).status_code,400)
                result=prod.post(clear,headers=headers,json={'confirmation':'ОЧИСТИТЬ PROD'})
                self.assertEqual(result.status_code,200,result.text)
                for _ in range(60):
                    g=prod.get('/api/v1/graph?mode=current&history=0')
                    if g.status_code==200 and g.json()['counts']['nodes']==0:break
                    time.sleep(.02)
                self.assertEqual(g.json()['counts']['nodes'],0)

    def test_only_native_test_projects_and_cancels_whole_pending_batch(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        clients, configs, packets = {}, {}, {}
        batch_id, identity = "d" * 64, lambda: ("fixture-release", "a" * 64)
        for stage in ("test", "prod"):
            path = root / stage / "state" / "fixture.sqlite3"
            path.parent.mkdir(parents=True)
            config = configs[stage] = ServiceConfig(db=str(path), provider="none", processing_session_ids=[])
            with MemoryStore(path) as store:
                service = config.service(store)
                session = "fixture-batch-" + stage
                stream = service.ingest_register("fictional-producer", session)
                packets[stage] = [dict(session_id=session, message_id=f"m{i}", actor_id=role, role=role,
                    text=f"Fictional {role}", source=f"fixture://{stage}/{i}",
                    delivery={k:stream[k] for k in ("stream_id", "epoch")} | {"seq":i})
                    for i,role in enumerate(("user","assistant"),1)]
                with authorized_machine_batch():
                    capture(service,batch_id,packets[stage])
            owner = OwnerStore(path.parent / "graph-state", "synthetic only")
            cookie,_ = owner.login("synthetic only",stage)
            profile = surface.LANES[stage]
            lane = replace(profile,base=replace(profile.base,root=root/stage,db=path))
            dashboard = None
            with (patch.object(surface,"LANES",dict(surface.LANES)|{stage:lane}),
                  patch.dict(queue_dashboard.SCOPES,{"test":("assertion-test-deploy",path)}),
                  patch("pwd.getpwnam",return_value=SimpleNamespace(pw_uid=994))):
                if stage == "test":
                    dashboard = queue_dashboard.HostedQueueDashboard(
                        "test",projection_path("test"),path,activation=lambda:("a"*40,"b"*64))
                    dashboard.metadata=lambda:dict(release_version=identity()[0],generation=identity()[1],
                        auto_process_fresh_mac=False,processing_session_ids=[],
                        model_configured=False,interactive_enabled=False)
                    dashboard.release_lease=nullcontext
                app=surface.base_browser_app(stage,config=config,private_port=lane.private_port,
                    verified_identity=identity,release_lease=nullcontext,interactive=False,test_dashboard=dashboard)
                client = self.enterContext(TestClient(app,base_url=lane.origin,cookies={COOKIE:cookie}))
            clients[stage]=client,lane.origin

        def card(stage):
            response = clients[stage][0].get("/api/v1/capabilities?queue_status=1")
            self.assertEqual(response.status_code,200,response.text)
            return next((row for q in response.json()["queues"] if q["id"]=="source"
                         for row in q["items"] if row["id"]=="batch:"+batch_id),None)

        test_row,prod_row=card("test"),card("prod")
        self.assertEqual(test_row["member_count"],2)
        self.assertEqual(prod_row["member_count"],2)
        self.assertNotIn("remove_revision",prod_row)
        client,origin=clients["test"]
        route="/api/v1/processing/items/remove"
        body=dict(stage="test",queue_id="source",item_id=test_row["id"],expected_revision=test_row["remove_revision"])
        headers={"Origin":origin}
        prep=client.post(route+"/prepare",headers=headers,json=body)
        self.assertEqual(prep.status_code,200,prep.text)
        self.assertEqual(card("test")["member_count"],2)
        commit=client.put(route,headers=headers|{"X-CSRF-Token":prep.json()["csrf_token"]},json=body)
        self.assertEqual(commit.status_code,200,commit.text)
        self.assertIsNone(card("test"))
        self.assertEqual(card("prod"),prod_row)
        with MemoryStore(configs["test"].db) as store:
            with authorized_machine_batch(),self.assertRaisesRegex(ValueError,"cannot replay"):
                capture(configs["test"].service(store),batch_id,packets["test"])
            with store.transaction() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM memory_raw_events").fetchone()[0],0)

    def test_base_packaged_standard_component_rejects_alternate_cli_selectors(self):
        from apps.graph_viewer.server.__main__ import main
        base=['viewer','--state-dir','/fixture-state','--browser-owner-api','--queue-status-stage','test']
        for extra in (['--source',str(surface.LANES['prod'].base.db)],
                      ['--public-origin',surface.LANES['prod'].origin]):
            with (self.subTest(extra=extra),patch('sys.argv',base+extra),
                  patch('apps.graph_viewer.server.__main__.validate_static_directory') as validate,
                  patch('apps.graph_viewer.server.__main__.uvicorn.run') as start,
                  patch('sys.stderr')):
                with self.assertRaises(SystemExit) as result: main()
                self.assertEqual(result.exception.code,2)
                validate.assert_not_called()
                start.assert_not_called()
