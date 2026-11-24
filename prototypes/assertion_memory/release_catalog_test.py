"""Deterministic exact catalog from an empty, private offline schema."""
import asyncio
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from . import release_catalog as catalog
from .http_server import create_http_app
from .mcp_server import ServiceConfig
from httpx import ASGITransport, AsyncClient


class Export(unittest.TestCase):
    def test_actual_http_schemas_equal_exported_bytes(self):
        async def check(db):
            rev='a'*40
            first=await catalog.export(rev)
            second=await catalog.export(rev)
            self.assertEqual(catalog.encoded(first),catalog.encoded(second))
            self.assertEqual(first['revision'],rev)
            self.assertEqual(first['schema_sha256'],catalog.sha(first['tools']))
            modules = (catalog.mcp_server, catalog.operation_registry)
            self.assertEqual(first['module_sha256'], catalog.sha({module.__name__:
                hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() for module in modules}))
            self.assertEqual(first['runtime_operation_registry'], 'base-typed-operations-v1')
            token='release-only-token-at-least-32-bytes'
            app=create_http_app(ServiceConfig(db=db,provider='none',processing_session_ids=[]),
                                token,'memory.example',background=False)
            async with app.lifespan(), AsyncClient(transport=ASGITransport(app=app),
                                                  base_url='https://memory.example') as client:
                r=await client.post('/memory/mcp',headers={
                    'accept':'application/json, text/event-stream','authorization':'Bearer '+token,
                    'mcp-protocol-version':'2025-11-25'},json={'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}})
                self.assertEqual(r.status_code,200)
                actual={entry['name']:entry['inputSchema'] for entry in r.json()['result']['tools']}
            self.assertEqual(actual,first['tools'])
            self.assertIn('memory_query',actual)
        with TemporaryDirectory() as d:
            asyncio.run(check(str(Path(d)/'empty.sqlite3')))

    def test_not_a_mutable_branch_or_tag(self):
        for value in ('main','assertion-memory-v0.1.1','a'*39):
            with self.assertRaises(ValueError):
                asyncio.run(catalog.export(value))


if __name__=='__main__':
    unittest.main()
