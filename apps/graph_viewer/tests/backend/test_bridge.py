"""Private Sites bridge credentials grant reads, never owner-session authority."""
import hashlib
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from starlette.testclient import TestClient
from test_service import fixture
from apps.graph_viewer.server.app import create_app

ORIGIN='https://private-sites.example.test'
BRIDGE='bridge.example.test'


class BridgeContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'source.sqlite3';fixture(self.source)
        self.source_hash=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.token=secrets.token_urlsafe(40)
        self.secret_file=self.root/'read.secret';self.secret_file.write_text(self.token);self.secret_file.chmod(0o600)
        self.app=create_app(self.source,self.root/'state',password='owner-test-strong',public_origin=ORIGIN,
                            allowed_proxy_hosts=[BRIDGE],bridge_read_token_file=self.secret_file)
        self.client=TestClient(self.app,base_url='https://'+BRIDGE);self.client.__enter__()
        self.headers={'origin':ORIGIN,'authorization':'Bearer '+self.token}
    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.assertEqual(self.source_hash,hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.tmp.cleanup()
    def manifest(self):
        for _ in range(100):
            response=self.client.get('/api/v1/graph',headers=self.headers)
            if response.status_code!=202:break
            time.sleep(.02)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()
    def test_bridge_host_and_browser_origin_are_independent(self):
        response=self.client.get('/api/v1/capabilities',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get('/api/v1/capabilities',headers={**self.headers,'origin':'https://foreign.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/v1/capabilities',headers={**self.headers,'host':'foreign.example'}).status_code,400)
    def test_valid_service_bearer_reads_exact_public_graph_routes(self):
        manifest=self.manifest();revision=manifest['revision']
        paths=['/api/v1/capabilities',manifest['nodes']['url'],manifest['edges']['url'],manifest['topics_url'],
               '/api/v1/nodes/old?revision='+revision,'/api/v1/nodes/old/edges?revision='+revision,
               '/api/v1/edges/conflict?revision='+revision,'/api/v1/sources/event?revision='+revision,
               '/api/v1/search?q=unicode&revision='+revision,'/api/v1/bookmarks',
               f'/api/v1/graph/{revision}/nodes/0',f'/api/v1/graph/{revision}/nodes/0/edges',
               f'/api/v1/graph/{revision}/edges/0']
        for path in paths:
            with self.subTest(path=path):self.assertEqual(self.client.get(path,headers=self.headers).status_code,200)
        response=self.client.head(manifest['nodes']['url'],headers=self.headers)
        self.assertEqual(response.status_code,200);self.assertEqual(response.content,b'')
    def test_absent_wrong_and_oversized_bearers_do_not_authenticate(self):
        headers=[{'origin':ORIGIN},{'origin':ORIGIN,'authorization':'Bearer incorrect'},
                 {'origin':ORIGIN,'authorization':'Bearer '+'x'*10000}]
        for value in headers:
            self.assertEqual(self.client.get('/api/v1/capabilities',headers=value).status_code,401)
        self.assertEqual(self.client.get('/api/v1/management',headers=self.headers).status_code,401)
        with TestClient(create_app(self.source,self.root/'disabled',password='owner-test-strong')) as disabled:
            self.assertEqual(disabled.get('/api/v1/capabilities',headers={'authorization':'Bearer '+self.token}).status_code,401)
    def test_read_bearer_cannot_mint_session_or_mutate_bookmarks(self):
        response=self.client.get('/api/v1/session',headers=self.headers)
        self.assertEqual(response.status_code,200);self.assertEqual(response.json(),{'authenticated':False})
        self.assertNotIn('set-cookie',response.headers)
        for method,path in [('POST','/api/v1/session'),('DELETE','/api/v1/session'),('PUT','/api/v1/bookmarks/x'),('DELETE','/api/v1/bookmarks/x'),('PATCH','/api/v1/bookmarks/x')]:
            response=self.client.request(method,path,headers=self.headers,json={})
            self.assertEqual(response.status_code,401,(method,response.text))
            self.assertNotIn('set-cookie',response.headers)
    def test_real_cookie_csrf_writes_still_require_owner_origin_and_token(self):
        response=self.client.post('/api/v1/session',json={'password':'owner-test-strong'},headers={'origin':ORIGIN})
        self.assertEqual(response.status_code,200,response.text)
        csrf=response.json()['csrf_token'];self.assertIn('; Secure',response.headers['set-cookie'])
        self.assertNotIn('domain=',response.headers['set-cookie'].lower())
        data={'provider_id':'assertion-memory','object_kind':'node','object_id':'old'}
        self.assertEqual(self.client.put('/api/v1/bookmarks/x',json=data,headers=self.headers).status_code,403)
        self.assertEqual(self.client.put('/api/v1/bookmarks/x',json=data,headers={**self.headers,'x-csrf-token':'wrong'}).status_code,403)
        valid={**self.headers,'x-csrf-token':csrf}
        self.assertEqual(self.client.put('/api/v1/bookmarks/x',json=data,headers={**valid,'origin':'https://foreign.example'}).status_code,403)
        self.assertEqual(self.client.put('/api/v1/bookmarks/x',json=data,headers=valid).status_code,200)
        self.assertEqual(self.client.get('/api/v1/session',headers=self.headers).json()['authenticated'],True)

    def test_bearer_stream_remains_live_without_owner_cookie(self):
        import asyncio
        self.client.post('/api/v1/session',json={'password':'owner-test-strong'},headers={'origin':ORIGIN})
        manifest=self.manifest();manager=self.app.state.manager
        manager.emit('delta',{'base_revision':manifest['revision'],'revision':manifest['revision']})
        app=self.app;headers=self.headers
        class Request:
            cookies={};calls=0
            query_params={'revision':manifest['revision']}
            url=type('URL',(),{'path':'/api/v1/events'})()
            method='GET'
            async def is_disconnected(self):
                self.calls+=1
                return self.calls>1
        request=Request();request.app=app;request.headers=headers
        async def collect():
            return [part async for part in manager.events(request)]
        parts=asyncio.run(collect())
        self.assertTrue(any('event: delta' in part for part in parts))

    def test_token_file_requires_owner_only_permissions_and_nontrivial_secret(self):
        self.secret_file.chmod(0o644)
        with self.assertRaises(ValueError):
            create_app(self.source,self.root/'unsafe',password='owner-test-strong',bridge_read_token_file=self.secret_file)
        self.secret_file.chmod(0o600);self.secret_file.write_text('short')
        with self.assertRaises(ValueError):
            create_app(self.source,self.root/'short',password='owner-test-strong',bridge_read_token_file=self.secret_file)
