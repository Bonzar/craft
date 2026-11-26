"""Verify strict managed-Origin smoke against a disposable Graph DB."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.tests.backend.test_service import fixture

def load(path):
    spec=importlib.util.spec_from_file_location('test_health_release',path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

health=load(Path(__file__).with_name('test-health.py'))

class BrowserScope:
    def __init__(self, app):
        self.app=app
    async def __call__(self, scope, receive, send):
        from starlette.responses import JSONResponse
        if scope['type']=='http' and scope['path']=='/':
            return await JSONResponse({},status_code=404)(scope,receive,send)
        if scope['type']=='http' and scope['path']==health.SESSION_PATH and scope['method']=='POST':
            if dict(scope['headers']).get(b'origin')!=health.TEST_PUBLIC_ORIGIN.encode():
                return await JSONResponse({},status_code=403)(scope,receive,send)
        return await self.app(scope,receive,send)

class FixedTestSmoke(unittest.TestCase):
    def test_compatible_with_existing_graph_and_new_strict_mount(self):
        for strict in (False,True):
            with self.subTest(strict=strict), tempfile.TemporaryDirectory() as d:
                p=Path(d); source=p/'source.sqlite3'; fixture(source)
                password='only-disposable-graph-password'
                app=create_app(source,p/'state',password=password,public_origin=health.TEST_PUBLIC_ORIGIN)
                if strict:
                    app=BrowserScope(app)
                with TestClient(app,base_url=health.LOCAL_GRAPH) as client:
                    if strict:
                        self.assertEqual(client.get('/').status_code,404)
                        self.assertEqual(client.post(health.SESSION_PATH,json={'password':password}).status_code,403)
                        self.assertEqual(client.post(health.SESSION_PATH,headers={'Origin':'null'},
                                                     json={'password':password}).status_code,403)
                    health.graph(client,password)

if __name__=='__main__': unittest.main()
