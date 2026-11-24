"""Read-only contracts over a completely empty canonical database."""
import asyncio
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from httpx import ASGITransport, AsyncClient
from .http_server import create_http_app
from .mcp_server import ServiceConfig
from .storage import MemoryStore
from .embeddings import CachedEmbedder, read_only_embeddings


class SearchReadOnly(unittest.TestCase):
    def test_empty_search_no_sqlite_writes(self):
        with TemporaryDirectory() as d:
            path=str(Path(d)/'empty.sqlite3')
            with MemoryStore(path) as store:
                service=ServiceConfig(db=path,provider='none',processing_session_ids=[]).service(store)
                connection=store._connection
                writes={sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,sqlite3.SQLITE_CREATE_TABLE}
                connection.set_authorizer(lambda action,*args: sqlite3.SQLITE_DENY if action in writes else sqlite3.SQLITE_OK)
                for args in ({}, {'scope':'different'}, {'session_id':'other-session'},
                             {'scope':'different','session_id':'other-session','limit':1}):
                    self.assertEqual(service.search('Find an existing thought',**args)['matches'],[])
                with self.assertRaises(ValueError):
                    service.search('Find an existing thought',limit=0)

    def test_cache_hits_can_be_used_without_persisting_misses(self):
        with TemporaryDirectory() as d, MemoryStore(str(Path(d)/'empty.sqlite3')) as store:
            ranker=CachedEmbedder(store,lambda texts:[[0.1,0.0] for _ in texts],'unit-vector')
            with read_only_embeddings():
                self.assertEqual(len(ranker(['discardable query'])),1)
            with store.transaction() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM memory_embedding_cache').fetchone()[0],0)

    def test_authenticated_mcp_advertises_and_calls_tool_without_answer(self):
        async def exercise(path):
            token='test-only-authentication-token-over-32'
            app=create_http_app(ServiceConfig(db=path, provider='none',processing_session_ids=[]),
                                token,'memory.example',background=False)
            header={'accept':'application/json, text/event-stream','mcp-protocol-version':'2025-11-25'}
            async with app.lifespan(), AsyncClient(transport=ASGITransport(app=app),
                                                  base_url='https://memory.example') as client:
                async def send(method,params,authorized):
                    headers={**header,**({'authorization':'Bearer '+token} if authorized else {})}
                    return await client.post('/memory/mcp',headers=headers,json={
                        'jsonrpc':'2.0','id':1,'method':method,'params':params})
                self.assertEqual((await send('tools/list',{},False)).status_code,401)
                listed=(await send('tools/list',{},True)).json()['result']['tools']
                self.assertIn('memory_search',{entry['name'] for entry in listed})
                result=(await send('tools/call',{'name':'memory_search','arguments':{
                    'query':'existing thought', 'session_id':'other-session'}},True)).json()['result']
                self.assertFalse(result.get('isError'))
                self.assertEqual(result.get('structuredContent') or json.loads(result['content'][0]['text']),{
                    'matches':[], 'scope':None,'session_id':'other-session'})
        with TemporaryDirectory() as d:
            asyncio.run(exercise(str(Path(d)/'empty.sqlite3')))


if __name__=='__main__':
    unittest.main()
