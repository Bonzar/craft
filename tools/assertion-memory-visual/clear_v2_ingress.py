"""Pure additive V2 plan on an independently reviewed lane baseline."""
from copy import deepcopy
from hashlib import sha256
import json
import re
from queue_owner_ingress import owner_routes, METHODS

ROOT='/api/v1/processing/'
ANCHOR='items/remove'
CLEAR='database/clear-v2'
LEGACY='database/clear'
EXACT=re.compile(r'^(?P<indent>[ \t]*)location = (?P<path>/[^ \t\r\n]+) \{\n', re.M)

def spec_pin(spec):
    return sha256(json.dumps(spec,sort_keys=True,separators=(',',':'),ensure_ascii=True,
                             allow_nan=False).encode('ascii')).hexdigest()

def plan(spec, server, **lane):
    if not isinstance(spec,dict) or not isinstance(server,str) or len(server)>128*1024:
        raise ValueError('Expected reviewed bounded ingress inputs')
    paths=spec.get('paths')
    if not isinstance(paths,dict) or any(not isinstance(p,str) for p in paths):
        raise ValueError('No exact existing path map')
    prefix=lane['prefix']
    entries=tuple(EXACT.finditer(server))
    def legacy(path):
        return path==ROOT+LEGACY or path.startswith(ROOT+LEGACY+'/')
    if any(legacy(p) for p in paths) or any(legacy(m['path'][len(prefix):])
                                          for m in entries if m['path'].startswith(prefix)):
        raise ValueError('Legacy clear path is present; stop')
    expected, nginx=owner_routes(**lane)
    target={}
    anchor=[]
    for method,suffix in METHODS:
        old=ROOT+ANCHOR+suffix
        current=paths.get(old)
        if not isinstance(current,dict) or set(current)!={method.lower()}:
            raise ValueError('Missing exact existing owner remove route')
        name='x-yc-apigateway-integration'
        if (not isinstance(current[method.lower()],dict)
                or any(current[method.lower()].get(k)!=expected[old][method.lower()][k]
                       for k in (name,'parameters'))):
            raise ValueError('Existing owner Gateway differs from reviewed authority')
        expected_anchor=[m for m in EXACT.finditer(nginx) if m['path']==prefix+old]
        observed=[m for m in entries if m['path']==prefix+old]
        if len(expected_anchor)!=1 or len(observed)!=1:
            raise ValueError('Missing/duplicate owner nginx anchor')
        off=expected_anchor[0].start()
        raw=nginx[off:nginx.index('\n}\n',off)+3]
        indent=observed[0]['indent']
        raw=''.join(indent+line for line in raw.splitlines(keepends=True))
        start=observed[0].start()
        if server[start:start+len(raw)]!=raw:
            raise ValueError('Existing owner nginx anchor differs from reviewed authority')
        anchor.append((start+len(raw),indent))
        path=ROOT+CLEAR+suffix
        if path in paths or any(m['path']==prefix+path for m in entries):
            raise ValueError('V2 exists; read back instead of reapplying')
        target[path]=expected[path]
    new=deepcopy(spec)
    new['paths'].update(target)
    addition=nginx[nginx.index('location = '+prefix+ROOT+CLEAR+'/prepare {'):]
    at,indent=max(anchor)
    addition=''.join(indent+line for line in addition.splitlines(keepends=True))
    after=server[:at]+addition+server[at:]
    return {'gateway_before_sha256':spec_pin(spec),'gateway_after_sha256':spec_pin(new),
            'nginx_before_sha256':sha256(server.encode()).hexdigest(),
            'nginx_after_sha256':sha256(after.encode()).hexdigest(),
            'gateway_after':new,'nginx_after':after}

def reverse(spec, server, **lane):
    """Remove only the two intact rendered additions; require reproducible inversion."""
    original=server
    before=deepcopy(spec)
    paths=before.get('paths')
    if not isinstance(paths,dict):
        raise ValueError('No exact current path map')
    for method,suffix in METHODS:
        path=ROOT+CLEAR+suffix
        if path not in paths:
            raise ValueError('V2 pair is incomplete')
        del paths[path]
        pattern=(r'^(?P<indent>[ \t]*)location = '+re.escape(lane['prefix']+path)
                 +r' \{\n[\s\S]*?^(?P=indent)\}\n')
        server,n=re.subn(pattern,'',server,count=1,flags=re.M)
        if n!=1:
            raise ValueError('V2 location is missing')
    forward=plan(before,server,**lane)
    if forward['gateway_after']!=spec or forward['nginx_after']!=original:
        raise ValueError('V2 additions drifted; refuse rollback')
    return before,server,forward
