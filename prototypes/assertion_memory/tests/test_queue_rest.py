"""CPU-only TEST REST proof through the real Base ASGI dispatcher."""
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.http_server import AuthenticatedMemoryApp
from prototypes.assertion_memory import queue_rest as rest

TOKEN='a'*64
KEY=sha256(TOKEN.encode()).hexdigest()
LEGACY='b'*64
TAG='assertion-memory-v0.1.15'
GEN='c'*64
PORT=8893


class Route(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.db=Path(d.name).resolve()/'synthetic.sqlite3'
        with sqlite3.connect(self.db) as db:
            db.executescript('CREATE TABLE memory_interactive_queries (id TEXT,state TEXT,created REAL);'
                             "INSERT INTO memory_interactive_queries VALUES ('fixture-q','queued',12.0);")
        self.config=SimpleNamespace(processing_session_ids=None,provider='fixture')
        self.hits=[]
        self.ident=TAG,GEN
        async def old(scope,receive,send):
            await send({'type':'http.response.start','status':418,'headers':[]})
            await send({'type':'http.response.body','body':b'{}'})
        def do_read(**page):
            self.hits.append(page)
            return rest.snapshot(self.config,identity=lambda:self.ident,database=self.db,
                                 interactive_enabled=True,**page)
        queue=rest.make_queue_rest(fingerprint=KEY,port=PORT,project=do_read)
        self.base=AuthenticatedMemoryApp(old,LEGACY,queue_rest=queue)

    async def send(self, *, raw=b'', token=TOKEN, peer=('127.0.0.1',9020), method='GET',
                   extra=(), host=b'127.0.0.1:8893', body=b'', path=rest.PATH, app=None, path_raw=None):
        headers=[(b'host',host),*extra]
        if token:
            headers.append((b'authorization',('Bearer '+token).encode()))
        events=[]
        async def req():
            return {'type':'http.request','body':body}
        async def respond(event):
            events.append(event)
        await (app or self.base)({'type':'http','method':method,'path':path,'raw_path':path.encode() if path_raw is None else path_raw,
                                 'query_string':raw,'client':peer,'server':('127.0.0.1',PORT),'headers':headers},req,respond)
        return events[0]['status'],json.loads(events[1]['body']),dict(events[0]['headers'])

    async def test_real_queue_get_preserves_exact_identity_and_read_only_state(self):
        original=self.db.read_bytes()
        status,body,headers=await self.send()
        self.assertEqual(status,200)
        self.assertEqual((body['stage'],body['release_version'],body['release_generation']),('test',TAG,GEN))
        self.assertEqual([v['id'] for v in body['queues']],['source','maintenance','interactive'])
        self.assertEqual(body['queues'][2]['items'][0]['id'],'fixture-q')
        self.assertFalse(body['processing_control']['can_change'])
        self.assertEqual(headers[b'cache-control'],b'private, no-store')
        status,body,_=await self.send(raw=b'limit=1&offset=1')
        self.assertEqual(status,200)
        self.assertEqual(body['queues'][2]['items'],[])
        self.assertEqual(self.db.read_bytes(),original)
        self.assertFalse(Path(str(self.db)+'-journal').exists())
        self.assertEqual(self.hits,[dict(limit=50,offset=0),dict(limit=1,offset=1)])

    async def test_rejects_wrong_authority_peer_headers_and_method_before_snapshot(self):
        cases=(
            ({'token':None},401),({'token':LEGACY},401),({'token':'d'*64},401),
            ({'peer':('192.0.2.4',9000),'extra':[(b'x-forwarded-for',b'127.0.0.1')]},403),
            ({'extra':[(b'x-forwarded-for',b'127.0.0.1')]},403),
            ({'extra':[(b'x-memory-lane',b'prod')]},403),
            ({'extra':[(b'forwarded',b'for=127.0.0.1')]},403),
            ({'extra':[(b'authorization',('Bearer '+TOKEN).encode())]},401),
            ({'host':b'elsewhere.example'},403),({'method':'POST'},405),
            ({'path_raw':b'/api/v1/queue%2dstatus'},404),
            ({'body':b'unexpected'},400),({'extra':[(b'content-length',b'1')]},400),
        )
        for options, expected in cases:
            with self.subTest(options=options):
                self.assertEqual((await self.send(**options))[0],expected)
        self.assertEqual(self.hits,[])

    async def test_strict_pagination_and_stale_release(self):
        for value in (b'limit=51',b'limit=0',b'offset=-1',b'offset=1000001',b'limit=1&limit=2',
                      b'offset=1&offset=2',b'lane=prod',b'limit=01',b'limit=%31',b'limit=1&&offset=2'):
            with self.subTest(value=value):
                self.assertEqual((await self.send(raw=value))[0],400)
        self.assertEqual(self.hits,[])
        with patch.object(rest,'snapshot',side_effect=ValueError('simulated stale current')):
            self.assertEqual((await self.send())[0],503)

    async def test_no_mcp_dispatch_for_queue_and_no_mcp_authority_from_queue(self):
        # Existing MCP keeps its app/authorization route unchanged.
        self.assertEqual((await self.send(path='/mcp',token=LEGACY))[0],418)
        async def denied(status,code,scope,receive,send):
            await send({'type':'http.response.start','status':status,'headers':[]})
            await send({'type':'http.response.body','body':b'{}'})
        with patch('prototypes.assertion_memory.http_server._error_response',denied):
            self.assertEqual((await self.send(path='/mcp'))[0],401)
            self.assertEqual((await self.send(app=AuthenticatedMemoryApp(None,LEGACY)))[0],404)

    def test_default_off_non_test_account_does_not_access_config(self):
        with patch.object(rest.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='assertion-production-deploy')),\
                patch.object(rest.os.path,'lexists',side_effect=AssertionError('must not touch opt-in')):
            self.assertIsNone(rest.configured_queue_rest('/srv/assertion-release/test/state/http.json',PORT,LEGACY))

    def test_default_off_in_verified_test_scope_without_marker(self):
        with patch.object(rest.os,'getuid',return_value=994), patch.object(rest.os,'geteuid',return_value=994),\
                patch.object(rest.pwd,'getpwuid',return_value=SimpleNamespace(pw_name=rest.TEST_ACCOUNT)),\
                patch.object(rest.os.path,'lexists',return_value=False) as exists:
            self.assertIsNone(rest.configured_queue_rest('/srv/assertion-release/test/state/http.json',PORT,LEGACY))
            exists.assert_called_once_with(rest.TEST_CONFIG/'queue-read-rest-v1.json')
