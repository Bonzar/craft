"""Authenticated QA-only live smoke. Prints metadata, never secrets."""
import json
from pathlib import Path
import sys
import time

import httpx

p = Path(sys.argv[1])
with httpx.Client(base_url='http://localhost:8893', trust_env=False, timeout=3) as m:
    h = {'accept':'application/json, text/event-stream','mcp-protocol-version':'2025-11-25'}
    req = {'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}}
    assert m.post('/memory/mcp',headers=h,json=req).status_code == 401
    h['authorization'] = 'Bearer ' + (p/'mcp-token').read_text().strip()
    listed = m.post('/memory/mcp',headers=h,json=req)
    listed.raise_for_status()
    assert len(listed.json()['result']['tools']) == 17
    req.update(method='tools/call',params={'name':'memory_history','arguments':{'view_id':'thought-vm-synthetic-one'}})
    r = m.post('/memory/mcp',headers=h,json=req)
    r.raise_for_status()
    data=r.json()['result']
    assert not data.get('isError')
    assert 'ёж' in str(data.get('structuredContent') or json.loads(data['content'][0]['text']))
with httpx.Client(base_url='http://localhost:8891', trust_env=False, timeout=3) as g:
    assert g.get('/').status_code == 200
    assert g.get('/api/v1/graph').status_code == 401
    g.post('/api/v1/session',json={'password':(p/'graph-password').read_text().strip()}).raise_for_status()
    deadline=time.monotonic()+15
    while True:
        r = g.get('/api/v1/graph')
        if r.status_code!=202 or time.monotonic()>=deadline:
            r.raise_for_status()
            assert r.status_code==200
            break
        time.sleep(.2)
    revision=r.json()['revision']
    for name in ('nodes.bin','edges.bin'):
        result=g.get(f'/api/v1/graph/{revision}/{name}')
        result.raise_for_status()
        assert result.content
print('Authenticated synthetic MCP and graph passed; no real data or model calls.')
