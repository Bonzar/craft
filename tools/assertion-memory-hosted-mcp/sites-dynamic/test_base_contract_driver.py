"""Connect the unchanged Site test harness to the real plain Base ASGI handler."""
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Annotated, Literal

from pydantic import Field

checkout=Path(os.environ.get('BASE_PRIVATE_HTTP_CHECKOUT',Path(__file__).resolve().parents[3]))
sys.path[:0]=[str(checkout),str(checkout/'tools/assertion-memory-hosted-mcp')]
from prototypes.assertion_memory.operation_registry import OperationRegistry
import policy
from vm_relay import make_app

async def exchange(req):
    lane='test' if req['path']=='/memory-mcp-test-tools' else 'prod'
    registry=OperationRegistry()
    if req['variant']=='original':
        @registry.tool()
        async def memory_contract_example(topic:Literal['cinema','travel']='cinema'):
            """Read the declared input using the real private Base adapter."""
            return {'topic':topic}
    elif req['variant']=='expanded':
        @registry.tool()
        async def memory_contract_example(
             audience:Annotated[Literal[2,3],Field(json_schema_extra={'default':2})],
             topic:Literal['cinema','travel']='cinema'):
            """Read the declared input using the real private Base adapter."""
            return {'topic':topic,'audience':audience}
    else:
        raise ValueError('Unknown fixture')
    decl=registry.descriptors()
    names=set(decl)
    # Only source of these test-only descriptors is Base's typed registry.
    # Never alter the release policy for a production tool.
    policy.OPERATIONS.clear()
    policy.OPERATIONS.update({n:'baseline_read' for n in names})
    policy.NARROW.clear()
    policy.NARROW.update({n:{k:{} for k in d['inputSchema']['properties']} for n,d in decl.items()})
    schemas={n:d['inputSchema'] for n,d in decl.items()}
    doc={'revision':'a'*40,'module_sha256':'b'*64,'runtime_mcp_sdk':'contract-fixture',
         'tools':schemas,'descriptions':{n:d['description'] for n,d in decl.items()}}
    pin={'revision':doc['revision'],'module_sha256':doc['module_sha256'],
         'sdk':doc['runtime_mcp_sdk'],'schema_sha256':policy.digest(schemas)}
    catalog=policy.compile_catalog(doc,pin)
    profile={'lane':lane,'base_release':'assertion-memory-v0.9.0','catalog_sha256':catalog['catalog_sha256'],
             'enabled_capabilities':['baseline_read'],'require_catalog_identity':True}
    generation=('c' if req['variant']=='original' else 'd')*64
    app=make_app(profile,catalog,'a'*64,lambda:(profile['base_release'],generation),operations=registry)
    replies=[]
    async def send(evt):replies.append(evt)
    async def read():return {'type':'http.request','body':json.dumps(req['body']).encode()}
    await app({'type':'http','method':'POST','path':req['path'],'query_string':b'',
               'headers':[(b'content-type',b'application/json'),(b'authorization',b'Bearer '+b'a'*64)]},read,send)
    return {'status':replies[0]['status'],'body':json.loads(replies[1]['body']),'declared':decl}

print(json.dumps(asyncio.run(exchange(json.loads(sys.stdin.readline())))))
