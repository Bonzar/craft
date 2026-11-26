"""Authenticated browser path through a disposable Base graph."""
from apps.graph_viewer.tests.backend import test_browser_owner_api as fixture
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
from prototypes.assertion_memory.thought_graph import export_graph
from prototypes.assertion_memory.thought_store import ThoughtStore


class BrowserNodeBoundaryTest(fixture.BrowserOwnerBoundaryTest):
    def test_node_button_capability_and_real_owner_browser_commit(self):
        thoughts=ThoughtStore(self.store)
        selected=thoughts.publish('synthetic visible node')
        other=thoughts.publish('synthetic other node')
        path=fixture.PREFIX+'graph-nodes/remove'
        data={'stage':'test','thought_id':selected.id,'thought_version':selected.version}
        dark=self.make()
        self.login(dark)
        self.assertNotIn('owner_graph_node_removal',dark.get('/api/v1/capabilities').json())
        self.assertEqual(dark.post(path+'/prepare',headers=fixture.BROWSER,json=data).status_code,404)

        def nodes(action,args):
            kw=dict(args)
            if action=='graph_node_preview':
                self.assertEqual(kw.pop('expected_revision'),'0'*32)
            return self.control(action,kw,authorize=(lambda db:None) if action=='graph_node_remove' else None)
        api=BrowserOwnerAPI(stage='test',perform=self.control,queue_snapshot=lambda **_:None,node_removal=nodes)
        c=self.make(browser=api)
        self.assertEqual(c.post(path+'/prepare',headers=fixture.BROWSER,json=data).status_code,401)
        self.login(c)
        self.assertEqual(c.get('/api/v1/capabilities').json()['owner_graph_node_removal'],{'version':1,'stage':'test'})
        self.assertEqual(c.post(path+'/prepare',headers={'origin':'https://other.test'},json=data).status_code,403)
        review=self.prepare(c,path,data)
        payload=data|{'expected_revision':review['revision']}
        headers=fixture.BROWSER|{'x-csrf-token':review['csrf_token']}
        self.assertEqual(c.put(path,headers=fixture.BROWSER,json=payload).status_code,403)
        self.assertEqual(c.put(path,headers=headers,json=payload|{'thought_id':other.id}).status_code,403)
        resp=c.put(path,headers=headers,json=payload)
        self.assertEqual(resp.status_code,200,resp.text)
        self.assertTrue(resp.json()['removed'])
        self.assertEqual(resp.json()['source_revision'],export_graph(thoughts)['source_revision'])
        self.assertIsNone(thoughts.get(selected.id))
        self.assertEqual(thoughts.get(other.id),other)
        self.assertEqual(c.put(path,headers=headers,json=payload).status_code,403)
