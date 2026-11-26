"""TEST-only SQLite initialization and read-only live smoke; never feeds data."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import sqlite3
from tempfile import TemporaryDirectory
import time

import httpx


STATE = Path('/srv/assertion-release/test/state')
LOCAL_GRAPH = 'http://127.0.0.1:8891'
SESSION_PATH = '/api/v1/session'
TEST_PUBLIC_ORIGIN = 'https://d5dc14bhh6hba54q0i3p.hsvi2zuh.apigw.yandexcloud.net'
GRAPH_SESSION = re.compile(r'[A-Za-z0-9_-]{54}\Z', re.ASCII)
TOOLS = {'memory_capture_message', 'memory_process', 'memory_query', 'memory_status', 'memory_history'}
TABLES = ('memory_raw_events', 'memory_capture_jobs', 'memory_thoughts')


def require(value, label):
    if not value:
        raise ValueError(label)


def initialize(state):
    from prototypes.assertion_memory.mcp_server import ServiceConfig
    from prototypes.assertion_memory.storage import MemoryStore
    path = state/'fictional.sqlite3'
    require(not path.is_symlink(), 'TEST database path is a symlink')
    if path.is_file():
        return
    require(not path.exists() and not any((state/(path.name+s)).exists() or
            (state/(path.name+s)).is_symlink() for s in ('-wal','-shm')), 'Unexpected TEST database remnants')
    with TemporaryDirectory(dir=state, prefix='.unseeded-') as root:
        fresh = Path(root)/path.name
        with MemoryStore(str(fresh)) as store:
            ServiceConfig(db=str(fresh), provider='none', processing_session_ids=[]).service(store)
        with sqlite3.connect(fresh.as_uri()+'?mode=ro', uri=True) as check:
            require(check.execute('PRAGMA quick_check').fetchone()==('ok',),'TEST SQLite check failed')
            for table in TABLES:
                require(check.execute(f'SELECT count(*) FROM {table}').fetchone()==(0,),
                        'New TEST database was not empty')
        os.chmod(fresh,0o600)
        os.link(fresh,path)  # completed, no-overwrite publication on this filesystem


def mcp(client, token):
    common={'accept':'application/json, text/event-stream','mcp-protocol-version':'2025-11-25'}
    def call(method, params, auth=True):
        headers=common | ({'authorization':'Bearer '+token} if auth else {})
        return client.post('/memory/mcp',headers=headers,json={
            'jsonrpc':'2.0','id':1,'method':method,'params':params})
    require(call('tools/list',{},False).status_code==401,'TEST MCP auth probe failed')
    response=call('tools/list',{})
    response.raise_for_status()
    tools={x['name'] for x in response.json()['result']['tools']}
    require(TOOLS <= tools,'Missing canonical TEST tools')
    response=call('tools/call',{'name':'memory_status','arguments':{'section':'overview','limit':1}})
    response.raise_for_status()
    result=response.json()['result']
    require(not result.get('isError'),'TEST canonical database status unavailable')
    value=result.get('structuredContent') or json.loads(result['content'][0]['text'])
    require(value.get('storage')=='unified-thought-v1' and
            all(type(value.get(k)) is int and value[k]>=0 for k in (
                'raw_pending_count','pending_count','error_count','thought_count')),
            'TEST canonical database status invalid')


def graph(client, password, *, deadline_seconds=15):
    require(str(client.base_url).rstrip('/')==LOCAL_GRAPH,'TEST Graph smoke requires its fixed loopback listener')
    require(client.get('/api/v1/graph',follow_redirects=False).status_code==401,'TEST graph auth probe failed')
    require(client.get('/api/v1/health',follow_redirects=False).status_code==200,'TEST graph API health unavailable')
    login=client.post(SESSION_PATH,headers={'Origin':TEST_PUBLIC_ORIGIN},
                      json={'password':password},follow_redirects=False)
    require(login.status_code==200 and str(login.request.url)==LOCAL_GRAPH+SESSION_PATH,
            'TEST Graph loopback login unavailable')
    cookies=list(login.cookies.jar)
    require(len(cookies)==1 and cookies[0].name=='gv_session' and not cookies[0].domain_specified
            and cookies[0].path=='/api/v1' and isinstance(cookies[0].value,str)
            and bool(GRAPH_SESSION.fullmatch(cookies[0].value)),
            'TEST Graph loopback session has unexpected cookie shape')
    # Browser cookies remain Secure. The trusted smoke actor uses an explicitly
    # scoped, same-listener request and must not depend on browser cookie forwarding
    # over its plain local socket. Never log, retain outside the client, redirect or export it.
    session_headers={'cookie':'gv_session='+cookies[0].value}
    deadline=time.monotonic()+deadline_seconds
    while True:
        response=client.get('/api/v1/graph',headers=session_headers,follow_redirects=False)
        if response.status_code!=202 or time.monotonic()>=deadline:
            break
        time.sleep(.1)
    require(response.status_code==200,'TEST graph manifest unavailable')
    manifest=response.json()
    ref=manifest.get('revision')
    require(isinstance(ref,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',ref)
            and manifest.get('schema')==1 and manifest.get('complete_graph') is True
            and manifest.get('layout',{}).get('stable') is True, 'TEST graph manifest invalid')
    for key,stride in (('nodes',16),('edges',8)):
        count=manifest['counts'][key]
        descriptor=manifest[key]
        url=f'/api/v1/graph/{ref}/{key}.bin'
        require(type(count) is int and count>=0 and descriptor.get('url')==url and
                descriptor.get('stride')==stride and descriptor.get('byte_length')==count*stride,
                'TEST graph layout descriptor invalid')
        with client.stream('GET',url,headers=session_headers|{'accept-encoding':'identity'},
                           follow_redirects=False) as buffer:
            require(buffer.status_code==200 and 'application/octet-stream' in buffer.headers.get('content-type',''),
                    'TEST graph buffer unavailable')
            total=0
            for part in buffer.iter_bytes():
                total+=len(part)
                require(total<=count*stride,'TEST graph buffer exceeds layout')
            require(total==count*stride,'TEST graph buffer does not match layout')


def smoke_mcp(state):
    with httpx.Client(base_url='http://localhost:8893', trust_env=False, timeout=3) as client:
        mcp(client,(state/'mcp-token').read_text().strip())


def smoke(state):
    smoke_mcp(state)
    with httpx.Client(base_url=LOCAL_GRAPH,trust_env=False,timeout=3,follow_redirects=False) as client:
        graph(client,(state/'graph-password').read_text().strip())
    print('Authenticated TEST MCP schema and graph layout healthy; no data or model requests.')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('initialize','smoke','smoke-mcp'))
    p.add_argument('state',type=Path)
    args=p.parse_args()
    require(args.state==STATE and args.state.resolve(strict=True)==STATE
            and pwd.getpwuid(os.getuid()).pw_name=='assertion-test-deploy','Wrong TEST service identity')
    {'initialize':initialize,'smoke':smoke,'smoke-mcp':smoke_mcp}[args.action](STATE)


if __name__=='__main__':
    main()
