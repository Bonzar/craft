"""Resource bounds, state privacy, and write schema validation."""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from starlette.requests import Request
from starlette.testclient import TestClient
from test_service import fixture
from apps.graph_viewer.server.app import body,create_app
from apps.graph_viewer.server.auth import OwnerStore
from apps.graph_viewer.server.common import APIError


class SecurityContract(unittest.TestCase):
    def test_chunked_login_body_stops_at_bounded_bytes(self):
        received=[]
        async def receive():
            received.append(1)
            return {'type':'http.request','body':b'x'*20001,'more_body':len(received)<2}
        scope={'type':'http','method':'POST','path':'/api/v1/session','headers':[]}
        with self.assertRaises(APIError) as error:
            asyncio.run(body(Request(scope,receive)))
        self.assertEqual(error.exception.status,413)
        self.assertEqual(len(received),1,'Must stop before consuming a second oversized chunk')
    def test_existing_state_directory_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'state'; directory.mkdir(mode=0o755)
            OwnerStore(directory,'owner-test-strong')
            self.assertEqual(directory.stat().st_mode&0o777,0o700)
            self.assertEqual((directory/'viewer.sqlite3').stat().st_mode&0o777,0o600)
    def test_frontend_directory_must_be_separate_from_private_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            public=root/'dist'; public.mkdir()
            source=root/'source.sqlite3'; fixture(source)
            bundled_source=public/'source.sqlite3'; fixture(bundled_source)
            for path,state,secret in [
                (source,public/'private-state',None),
                (bundled_source,root/'private-state',None),
                (source,root/'private-state',public/'bridge.secret'),
            ]:
                with self.subTest(path=path,state=state,secret=secret):
                    with self.assertRaisesRegex(ValueError,'Frontend directory overlaps a private path'):
                        create_app(path,state,password='owner-test-strong',static_dir=public,
                                   bridge_read_token_file=secret)
    def test_bookmark_label_and_selector_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=OwnerStore(Path(tmp)/'state','owner-test-strong')
            base={'provider_id':'p','object_kind':'node','object_id':'n'}
            for extra in [{'label':{'evil':'object'}},{'selector':{'mode':'imaginary'}},{'selector':{'mode':'known_then','at':'bogus'}}]:
                with self.assertRaises(APIError):store.bookmark('b',dict(base,**extra))
    def test_content_length_rejected_without_read_and_login_rate_limited(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.sqlite3';fixture(path)
            with TestClient(create_app(path,Path(tmp)/'state',password='owner-test-strong')) as c:
                r=c.post('/api/v1/session',content='{}',headers={'content-length':'999999'})
                self.assertEqual(r.status_code,413)
                for _ in range(6):self.assertEqual(c.post('/api/v1/session',json={'password':'wrong'}).status_code,401)
                self.assertEqual(c.post('/api/v1/session',json={'password':'wrong'}).status_code,429)

if __name__=='__main__':unittest.main()

class IngressContract(unittest.TestCase):
    def test_explicit_https_origin_sets_secure_cookie_without_trusting_forwarded_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.sqlite3';fixture(path)
            app=create_app(path,Path(tmp)/'state',password='owner-test-strong')
            app.state.public_origin='https://viewer.example.test'
            with TestClient(app,base_url='http://viewer.example.test') as c:
                result=c.post('/api/v1/session',json={'password':'owner-test-strong'},headers={'origin':'https://viewer.example.test'})
                self.assertEqual(result.status_code,200,result.text)
                self.assertIn('; Secure',result.headers['set-cookie'])
                self.assertIn('Strict-Transport-Security',result.headers)
    def test_untrusted_host_is_rejected_before_any_source_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.sqlite3';fixture(path)
            with TestClient(create_app(path,Path(tmp)/'state',password='owner-test-strong')) as c:
                response=c.get('/api/v1/session',headers={'host':'attacker.invalid'})
                self.assertEqual(response.status_code,400)
