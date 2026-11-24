"""Existing HTTP startup and health can coexist with one neutral tool service."""
import asyncio
from pathlib import Path
import tempfile
from unittest import TestCase, main
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient
from .http_server import create_http_app
from .mcp_server import ServiceConfig
from .operation_registry import create_operations
from starlette.responses import JSONResponse


class Dual(TestCase):
    def test_legacy_smoke_and_neutral_share_one_service(self):
        async def test(db):
            legacy='a'*64
            scoped='b'*64
            original=ServiceConfig.service
            seen=[]
            def tracked(config,store):
                service=original(config,store)
                seen.append(service)
                return service
            def neutral(config,store,service,background):
                self.assertIs(service,seen[0])
                ops=create_operations(config,store,background=background,service=service)
                async def api(scope,receive,send):
                    allowed=dict(scope['headers']).get(b'authorization')==('Bearer '+scoped).encode()
                    await JSONResponse({'neutral':len(ops.descriptors())} if allowed else {},
                                       status_code=200 if allowed else 401)(scope,receive,send)
                return frozenset(['/memory-mcp-test-tools']),api
            with patch.object(ServiceConfig,'service',tracked):
                app=create_http_app(ServiceConfig(db=db,provider='none',processing_session_ids=[]),
                                    legacy,'memory.example',background=False,neutral_factory=neutral)
                async with app.lifespan(), AsyncClient(transport=ASGITransport(app=app),
                                                      base_url='https://memory.example') as c:
                    headers={'accept':'application/json, text/event-stream',
                             'mcp-protocol-version':'2025-11-25'}
                    body={'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}}
                    self.assertEqual((await c.post('/memory/mcp',headers=headers,json=body)).status_code,401)
                    r=await c.post('/memory/mcp',headers=headers|{'authorization':'Bearer '+legacy},json=body)
                    self.assertIn('memory_status',{tool['name'] for tool in r.json()['result']['tools']})
                    self.assertEqual((await c.post('/memory-mcp-test-tools',headers={'authorization':'Bearer '+legacy})).status_code,401)
                    self.assertEqual((await c.post('/memory-mcp-test-tools',headers={'authorization':'Bearer '+scoped})).status_code,200)
                    self.assertEqual((await c.post('/memory/mcp',headers=headers|{'authorization':'Bearer '+scoped},json=body)).status_code,401)
                    self.assertEqual(len(seen),1)
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(test(str(Path(folder)/'empty.sqlite3')))


if __name__=='__main__':
    main()
