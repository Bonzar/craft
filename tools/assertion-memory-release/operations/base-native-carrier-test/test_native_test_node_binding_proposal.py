"""Synthetic topology handoff with one real legacy session and no socket binds."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_runtime import initialize_empty
from prototypes.assertion_memory.thought_store import ThoughtStore
from apps.graph_viewer.server import queue_dashboard as qd
import native_test_node_binding_proposal as b


class NativeReceiverSeam(TestCase):
    def test_reopen_same_owner_and_enable_only_after_admitted_test_handoff(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            state=root/'state'; state.mkdir()
            db=state/'fictional.sqlite3'
            owner_state=state/'graph-state'
            (root/'deploy.lock').touch(mode=0o600)
            uid=b.os.getuid()
            self.assertGreater(uid,0)
            profile=replace(b.TEST,root=root,db=db)
            config=SimpleNamespace(db=db,auto_process_fresh_mac=False,processing_session_ids=None)
            origin=b.TEST_GRAPH_ORIGIN
            claims=('synthetic-running-test','opaque-generation')
            physical=('b'*40,'c'*64)
            header={'Origin':origin}
            writer='/api/v1/processing/graph-nodes/remove'
            with MemoryStore(db) as store:
                initialize_empty(store)
                thoughts=ThoughtStore(store)
                chosen=thoughts.publish('synthetic selected node')
                untouched=thoughts.publish('another synthetic node')
                request={'stage':'test','thought_id':chosen.id,'thought_version':chosen.version}
                old=TestClient(create_app(db,owner_state,password='fictional same owner',public_origin=origin),
                               base_url=origin)
                logged=old.post('/api/v1/session',headers=header,json={'password':'fictional same owner'})
                self.assertEqual(logged.status_code,200,logged.text)
                with (patch.object(b,'TEST',profile),patch('pwd.getpwnam',return_value=SimpleNamespace(pw_uid=uid)),
                      patch.dict(qd.SCOPES,{'test':(profile.account,db)}),
                      patch.object(qd,'_test_activation',return_value=physical)):
                    def build(admission,port=8893):
                        return TestClient(b.build_existing_test_surface(config,private_port=port,carrier_port=8891,
                            admitted=admission,runtime_identity=lambda:claims),base_url=origin,cookies=old.cookies)
                    shadow=b.ProtectedTestBinding('test','shadow',claims,uid,physical)
                    staged=build(shadow)
                    self.assertTrue(staged.get('/api/v1/session').json()['authenticated'])
                    self.assertEqual(staged.post(writer+'/prepare',headers=header,json=request).status_code,404)
                    self.assertIn(old.post(writer+'/prepare',headers=header,json=request).status_code,(403,404))
                    with self.assertRaises(ValueError):
                        build(replace(shadow,stage='prod'))
                    with self.assertRaises(ValueError):
                        build(shadow,8903)
                    with self.assertRaises(ValueError):
                        build(replace(shadow,current_scope=('wrong','stale')))
                    carrier=build(replace(shadow,mode='carrier'))
                    self.assertTrue(carrier.get('/api/v1/session').json()['authenticated'])
                    self.assertEqual(carrier.get('/memory-api-test').status_code,404)
                    prep=carrier.post(writer+'/prepare',headers=header,json=request)
                    self.assertEqual(prep.status_code,200,prep.text)
                    proof={'X-CSRF-Token':prep.json()['csrf_token']}
                    complete=carrier.put(writer,headers=header|proof,
                        json=request|{'expected_revision':prep.json()['revision']})
                    self.assertEqual(complete.status_code,200,complete.text)
                    self.assertTrue(complete.json()['removed'])
                    self.assertIn(old.post(writer+'/prepare',headers=header,json=request).status_code,(403,404))
                    self.assertTrue(old.get('/api/v1/session').json()['authenticated'])
                    self.assertEqual(thoughts.get(untouched.id),untouched)
                    self.assertIsNone(thoughts.get(chosen.id))
