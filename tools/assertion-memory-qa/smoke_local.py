"""Local ASGI smoke over explicitly synthetic data only. Opens no network socket."""
import asyncio
import json
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
import time
from httpx import ASGITransport,AsyncClient
from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from prototypes.assertion_memory.http_server import create_http_app
from prototypes.assertion_memory.mcp_server import ServiceConfig

ROOT=Path(__file__).resolve().parents[2]
async def mcp(db):
    token=secrets.token_urlsafe(32)
    app=create_http_app(ServiceConfig(db=str(db),provider='none',processing_session_ids=[]),token,'memory.example')
    async with app.lifespan():
        async with AsyncClient(transport=ASGITransport(app=app),base_url='https://memory.example') as client:
            common={'accept':'application/json, text/event-stream','mcp-protocol-version':'2025-11-25'}
            async def send(method,params=None,auth=True):
                headers={**common,'authorization':'Bearer '+token} if auth else common
                return await client.post('/memory/mcp',headers=headers,json={
                    'jsonrpc':'2.0','id':1,'method':method,'params':params or {}})
            assert (await send('tools/list',auth=False)).status_code==401
            listed=(await send('tools/list')).json()['result']['tools']
            async def tool(name,args):
                r=await send('tools/call',{'name':name,'arguments':args})
                result=r.json()['result']
                assert r.status_code==200 and not result.get('isError')
                return result.get('structuredContent') or json.loads(result['content'][0]['text'])
            assert len(listed)==17
            saved=await tool('memory_history',{'view_id':'thought-vm-synthetic-one'})
            assert '«ёж»' in saved['content']
            event=json.loads(saved['content'])[0]['bases'][0]['event_id']
            assert (await tool('memory_source',{'record_id':event,'start':8,'end':10}))['span']['text']=='ёж'
            assert (await tool('memory_process',{'limit':1}))['scheduled'] is False
            print('MCP: 401 without bearer; 17 tools; persisted history; Unicode span; model worker disabled')

def graph(db,directory):
    password=secrets.token_urlsafe(24)
    app=create_app(db,directory,password=password,synthetic_fixture=True,
                   static_dir=ROOT/'apps/graph_viewer/web/dist')
    with TestClient(app) as client:
        assert client.get('/api/v1/graph').status_code==401
        assert client.get('/').status_code==200
        assert client.post('/api/v1/session',json={'password':password}).status_code==200
        until=time.monotonic()+15
        while time.monotonic()<until:
            result=client.get('/api/v1/graph')
            if result.status_code!=202:break
            time.sleep(.05)
        assert result.status_code==200
        graph_data=result.json()
        assert graph_data.get('revision')
        for name in ('nodes.bin','edges.bin'):
            binary=client.get('/api/v1/graph/'+graph_data['revision']+'/'+name)
            assert binary.status_code==200 and len(binary.content)>0
        print('Graph: original index 200; owner login; protected revision and both binary buffers ready')
if __name__=='__main__':
    import sys
    db=Path(sys.argv[1]).resolve()
    asyncio.run(mcp(db))
    with TemporaryDirectory(prefix='craft-vm-viewer-') as d:
        graph(db,Path(d))
