"""Exercise production CLI composition and exact binding, without a deployed host."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from apps.graph_viewer.server.__main__ import configure_owner_lanes
from apps.graph_viewer.server import graph_node_test_binding as binding


class GraphNodeTestStartup(TestCase):
    def test_default_opt_in_and_prod_rejection(self):
        args=SimpleNamespace(
            queue_status_config=Path('/test/config'),queue_status_stage='test',
            queue_control=False,browser_owner_clear=False,browser_owner_api=True,
            public_origin=binding.TEST_GRAPH_ORIGIN,bridge_read_token_file=None,
            database_clear_read_token_file=None,database_clear_lane=None,allowed_proxy_host=[],
            test_direct_events_origin=None,synthetic_fixture=True,host='127.0.0.1',port=8891,
            source=binding.TEST.db,browser_owner_graph_node_removal=False)
        dashboard=SimpleNamespace(stage='test',db=binding.TEST.db)
        bridge=binding.GraphNodeOwnerBridge(*[str(i)*64 for i in range(1,5)],bytes.fromhex('5'*64),stage='test')
        with (patch('apps.graph_viewer.server.queue_dashboard.configured_browser_dashboard',return_value=dashboard),
                patch('apps.graph_viewer.server.device_test_binding.installed_test_device_binding',return_value=None),
                patch('apps.graph_viewer.server.browser_owner_api.installed_test_clear_profile',return_value=None),
                patch.object(binding,'installed_test_graph_node_bridge',return_value=bridge) as load_bridge):
            self.assertIsNone(configure_owner_lanes(args)[1].node_removal)
            load_bridge.assert_not_called()
            args.browser_owner_graph_node_removal=True
            self.assertTrue(callable(configure_owner_lanes(args)[1].node_removal))
            load_bridge.assert_called_once_with(dashboard)
            for key,bad in [('queue_status_stage','prod'),('port',8829),('synthetic_fixture',False),
                            ('public_origin','https://other.example'),('browser_owner_api',False)]:
                valid=getattr(args,key)
                setattr(args,key,bad)
                with self.assertRaises(ValueError):
                    configure_owner_lanes(args)
                setattr(args,key,valid)

    def test_pinned_canonical_credential_authority(self):
        db=SimpleNamespace(stage='test',db=binding.TEST.db)
        values=tuple(str(i)*64 for i in range(1,6))
        def h(v): return hashlib.sha256(v.encode('ascii')).hexdigest()
        policy=dict(read_sha256=h(values[1]),write_sha256=h(values[2]),
                    owner_sha256=values[3],signer_sha256=h(values[4]))
        local=Path('/run/user/994/credentials')/binding.TEST_GRAPH_UNIT
        unit='0::/user.slice/user-994.slice/user@994.service/app.slice/'+binding.TEST_GRAPH_UNIT+'\n'
        with (patch.dict(binding.os.environ,{'CREDENTIALS_DIRECTORY':str(local)}),
                patch.object(binding,'SELF_CGROUP',SimpleNamespace(read_text=lambda **_:unit)),
                patch.object(binding.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=994)),
                patch.object(binding.os,'getuid',return_value=994),
                patch.object(binding.os,'geteuid',return_value=994),
                patch.object(binding,'_policy',return_value=policy) as profile,
                patch.object(binding,'_read_owner_credentials',return_value=values) as credentials):
            bridge=binding.installed_test_graph_node_bridge(db)
            self.assertEqual((bridge.stage,bridge.port),('test',8893))
            credentials.assert_called_once_with(local)
            profile.assert_called_once_with(binding.TEST)
            for wrong in (None,policy|{'write_sha256':'0'*64},policy|{'owner_sha256':'0'*64}):
                profile.return_value=wrong
                with self.assertRaises(ValueError):
                    binding.installed_test_graph_node_bridge(db)
            profile.return_value=policy
            for wrong_dir in ('/run/credentials/'+binding.TEST_GRAPH_UNIT,
                              '/run/user/995/credentials/'+binding.TEST_GRAPH_UNIT,
                              '/run/user/994/credentials/another.service'):
                with patch.dict(binding.os.environ,{'CREDENTIALS_DIRECTORY':wrong_dir}), self.assertRaises(ValueError):
                    binding.installed_test_graph_node_bridge(db)
            for wrong in (unit.replace('app.slice','test.slice'),unit.replace('994','995'),unit.replace(binding.TEST_GRAPH_UNIT,'another.service')):
                with patch.object(binding,'SELF_CGROUP',SimpleNamespace(read_text=lambda **_:wrong)), self.assertRaises(ValueError):
                    binding.installed_test_graph_node_bridge(db)

# This exercises the actual HostedQueueDashboard lease and ordinary browser
# boundary with a disposable database. Only the private wire hop is adapted to
# the domain fixture, since binding the live service port is not a unit test.
from apps.graph_viewer.tests.backend import test_browser_owner_api as fixture
from apps.graph_viewer.server import queue_dashboard
from apps.graph_viewer.server.browser_owner_api import configured_browser_api
from prototypes.assertion_memory.thought_graph import export_graph
from prototypes.assertion_memory.thought_store import ThoughtStore
import fcntl
import os


class GraphNodeGuardIntegration(fixture.BrowserOwnerBoundaryTest):
    def test_boot_composed_dashboard_guard_commit_and_provenance_conflict(self):
        thoughts=ThoughtStore(self.store)
        selected=thoughts.publish('one isolated current thought')
        retained=thoughts.publish('required original thought')
        dependent=thoughts.publish('independent use with retained provenance',bases=[{'thought_id':retained.id}])
        route=fixture.PREFIX+'graph-nodes/remove'
        lock=self.db.parent/'deploy.lock'
        fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_EXCL,0o600)
        self.addCleanup(os.close,fd)
        config=queue_dashboard.dashboard_projection.projection_path('test')
        with (patch.dict(queue_dashboard.SCOPES,{'test':('fixture',self.db)}),
                patch.object(queue_dashboard.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.geteuid())),
                patch.object(queue_dashboard,'TEST_DEPLOY_ROOT',self.db.parent),
                patch.object(queue_dashboard,'_scoped_reader_activation',return_value=('a'*40,'b'*64))):
            dashboard=queue_dashboard.configured_browser_dashboard('test',config,self.db)
        self.assertIsNone(dashboard.read_fence)
        self.assertEqual(dashboard.control_lock,lock)
        bridge=binding.GraphNodeOwnerBridge(*[str(i)*64 for i in range(1,5)],bytes.fromhex('5'*64),stage='test')

        def private_writer(action,args):
            args=dict(args)
            if action=='graph_node_preview':
                self.assertEqual(args.pop('expected_revision'),'0'*32)
            # Same kind of shared, nonblocking acquisition as the real Base
            # writer performs through its separate descriptor on the same file.
            with dashboard.release_lease():
                return self.control(action,args,authorize=(lambda _:None) if action=='graph_node_remove' else None)
        browser=configured_browser_api(dashboard,graph_node_bridge=bridge)
        client=self.make(browser=browser)
        self.login(client)
        def data(thought):
            return dict(stage='test',thought_id=thought.id,thought_version=thought.version)
        with (patch.object(dashboard,'activation',return_value=('a'*40,'b'*64)),
                patch.object(bridge,'perform',side_effect=private_writer) as wire):
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            try:
                blocked=client.post(route+'/prepare',headers=fixture.BROWSER,json=data(selected))
                self.assertEqual(blocked.status_code,503,blocked.text)
                wire.assert_not_called()
            finally:
                fcntl.flock(fd,fcntl.LOCK_UN)
            prep=self.prepare(client,route,data(selected))
            commit=client.put(route,headers=fixture.BROWSER|{'x-csrf-token':prep['csrf_token']},
                              json=data(selected)|{'expected_revision':prep['revision']})
            self.assertEqual(commit.status_code,200,commit.text)
            self.assertIsNone(thoughts.get(selected.id))
            before=export_graph(thoughts)['source_revision']
            self.assertEqual(commit.json()['source_revision'],before)
            blocked=client.post(route+'/prepare',headers=fixture.BROWSER,json=data(retained))
            self.assertEqual(blocked.status_code,409,blocked.text)
            self.assertEqual(blocked.json(),{'error':'dependent_thoughts'})
            self.assertEqual(export_graph(thoughts)['source_revision'],before)
            self.assertEqual(thoughts.get(retained.id),retained)
            self.assertEqual(thoughts.get(dependent.id),dependent)
