"""Dry, TEST-only migration; never contacts a host or writes a release."""
from copy import deepcopy
import hashlib
import json
import re

TEST_NGINX_SHA='b29a1fb8d880fce8f53ff6ca7e57d7eea435c8dd8e2a2f028adbe6e64f5e5103'
TEST_HOST='d5dc14bhh6hba54q0i3p.hsvi2zuh.apigw.yandexcloud.net'
TARGET='https://153.51.163.106/__mvt/api/v1/{tail}'
WILD='/api/v1/{tail+}'
METHODS=('get','head','post','put','patch','delete','options')
SSE=('events-direct','events-proof','events-ticket')
WIRE={'Cookie':'$http_cookie','Origin':'$http_origin','Content-Type':'$http_content_type',
      'X-CSRF-Token':'$http_x_csrf_token','Sec-Fetch-Site':'$http_sec_fetch_site',
      'Accept':'$http_accept','Accept-Encoding':'$http_accept_encoding','If-None-Match':'$http_if_none_match'}
HARD_DENY=('processing/database/clear','processing/database/clear/prepare')
LOC=re.compile(r'(?m)^([ \t]*)location ([^\n]+?) \{[ \t]*(?:\n|[^\n]*\}[ \t]*$)')
END=lambda pad:re.compile(r'^'+re.escape(pad)+r'\}[ \t]*$', re.M)
FALLBACK='location /__mvt/ { return 404; }'
EXACT_LEGACY={'session','capabilities','health','jobs','bookmarks'}
REGEX_PREFIXES=('^/__mvt/api/v1/(?:graph','^/__mvt/api/v1/processing')
PRIVATE=re.compile(r'(?i)(authorization|marker|machine|bearer|x-forwarded|x-real-ip|x-memory-)')

def sha(b):
    return hashlib.sha256(b).hexdigest()

def spec_sha(value):
    return sha(json.dumps(value,ensure_ascii=True,allow_nan=False,sort_keys=True,
                          separators=(',',':')).encode('ascii'))

def nginx_delta(source):
    if len(source.encode())>131072 or source.count(FALLBACK)!=1:
        raise ValueError('unexpected TEST deny fallback')
    regions=[]
    reserved=set()
    for m in LOC.finditer(source):
        sel=m[2]
        if '/__mvt/api/v1' not in sel:
            continue
        prefix='= /__mvt/api/v1/'
        if not sel.startswith(prefix) and not sel.startswith('~ "'):
            raise ValueError('unknown TEST API app route')
        tail=sel[len(prefix):] if sel.startswith(prefix) else None
        if m.group().endswith('\n'):
            end=END(m[1]).search(source,m.end())
            if not end:
                raise ValueError('unclosed TEST API app route')
            pos=end.end()
        else:
            pos=m.end()
        if tail in SSE:
            if tail in reserved:
                raise ValueError('duplicate TEST SSE route')
            reserved.add(tail)
            continue
        if tail is not None and tail not in EXACT_LEGACY and tail not in HARD_DENY:
            raise ValueError('unreviewed exact TEST API app route')
        if tail is None and not any(sel.startswith('~ "'+p) for p in REGEX_PREFIXES):
            raise ValueError('unreviewed regex TEST API app route')
        regions.append((m.start(),pos))
    if set(SSE)!=reserved or not regions:
        raise ValueError('missing scoped TEST routes')
    # Hard-deny the retired path even if a future Base accidentally mounts it.
    deny=''.join(f'location = /__mvt/api/v1/{x} {{ return 404; }}\n' for x in HARD_DENY)
    block=deny+'location ^~ /__mvt/api/v1/ {\n'
    block+='    if ($request_method !~ ^(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS)$) { return 405; }\n'
    block+='    proxy_pass_request_headers off;\n'
    block+=f'    proxy_set_header Host {TEST_HOST};\n'
    block+=''.join(f'    proxy_set_header {k} {v};\n' for k,v in WIRE.items())
    block+='    proxy_set_header Connection "";\n    proxy_http_version 1.1;\n    proxy_cache off;\n'
    block+='    rewrite ^/__mvt(/api/v1/.*)$ $1 break;\n    proxy_pass http://127.0.0.1:8891;\n}\n'
    for lo,hi in reversed(regions):
        source=source[:lo]+source[hi:]
    return source.replace(FALLBACK,block+FALLBACK,1)

def gateway_delta(spec):
    data=deepcopy(spec)
    paths=data['paths']
    session='/api/v1/session'
    if any(k.startswith('/api/v1/') and k not in {WILD, session, *('/api/v1/'+x for x in SSE)} for k in paths):
        raise ValueError('unreviewed exact TEST Gateway API override')
    legacy=paths.get(session)
    if set(legacy or {})!={'get','post','delete'}:
        raise ValueError('legacy TEST session route shape drift')
    for method, op in legacy.items():
        carrier=op.get('x-yc-apigateway-integration',{})
        headers=carrier.get('headers',{})
        allowed={'Cookie','Origin','Content-Type','X-CSRF-Token','Sec-Fetch-Site'}
        if (carrier.get('type')!='http' or carrier.get('url')!=TARGET.replace('{tail}','session')
            or carrier.get('method',method.upper())!=method.upper()
            or set(headers)!=allowed or any(v!='{'+k+'}' for k,v in headers.items())
            or carrier.get('serviceAccountId') or op.get('security') or carrier.get('query')!={'*':'*'}):
            raise ValueError('legacy TEST session route authority drift')
    del paths[session]
    wild=paths[WILD]
    if not {'get','post','put'}<=set(wild)<=set(METHODS):
        raise ValueError('unexpected TEST wildcard methods')
    name='x-yc-apigateway-integration'
    get=wild['get']
    for method, op in wild.items():
        carrier=op[name]
        headers=carrier.get('headers',{})
        if (carrier.get('type')!='http' or carrier.get('url')!=TARGET
            or carrier.get('method',method.upper())!=method.upper()
            or not {'Cookie','Origin'}<=set(headers)<=set(WIRE)
            or any('{' + k + '}' != v or PRIVATE.search(k) for k,v in headers.items())
            or carrier.get('serviceAccountId')):
            raise ValueError('unexpected TEST wildcard authority')
    expected={'name':'tail','in':'path','required':True,'schema':{'type':'string'}}
    for method in METHODS:
        op=wild.get(method)
        if op is None:
            op={'parameters':[deepcopy(expected)],'responses':deepcopy(get.get('responses',{})),name:{
                k:deepcopy(v) for k,v in get[name].items() if k in ('type','url','timeouts')}}
            wild[method]=op
        params=op.get('parameters',[])
        if (not all(p.get('in') in ('path','query','header') for p in params)
            or sum(p.get('in')=='path' and p.get('name')=='tail' for p in params)!=1
            or any(p.get('in')=='path' and p.get('name')!='tail' for p in params)):
            raise ValueError('unexpected TEST wildcard path params')
        op['parameters']=[p for p in params if p.get('in')=='path']+[
            {'name':k,'in':'header','required':False,'schema':{'type':'string'}} for k in WIRE]
        op[name].update(method=method.upper(),headers={k:'{'+k+'}' for k in WIRE},
                        query={'*':'*'},omitEmptyHeaders=True,omitEmptyQueryParameters=True)
    return data

def gateway_wire(candidate):
    """Emit an alias-free UTF-8 OpenAPI JSON document for the provider CLI."""
    return json.dumps(candidate,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('utf-8')

def plan(source, spec, *, gateway_sha, verified_backend=False, verified_old_deny=False):
    if not verified_backend:
        raise ValueError('Base-only browser boundary proof required')
    if not verified_old_deny:
        raise ValueError('prior destructive route denial proof required')
    if sha(source.encode())!=TEST_NGINX_SHA or spec_sha(spec)!=gateway_sha:
        raise ValueError('TEST baseline drift')
    after=nginx_delta(source),gateway_delta(spec)
    return {'before':(source,deepcopy(spec)),'after':after,
            'pins':((TEST_NGINX_SHA,gateway_sha),(sha(after[0].encode()),spec_sha(after[1])))}

def inverse(p, current, *, verified_old_deny=False):
    if not verified_old_deny:
        raise ValueError('prior destructive route denial proof required for rollback')
    if current!=p['after'] or (sha(current[0].encode()),spec_sha(current[1]))!=p['pins'][1]:
        raise ValueError('TEST active generation drift')
    return deepcopy(p['before'])
