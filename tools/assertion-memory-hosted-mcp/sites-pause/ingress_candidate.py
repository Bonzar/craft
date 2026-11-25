"""Build TEST pause ingress candidates offline; never update Gateway or nginx."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

PATH = '/memory-visual-test-pause'
SIBLING = '/memory-mcp-test-effects'
HEADER = 'X-Memory-Test-Pause-Auth'
TEST_PORT = 8893
DNS = re.compile(r'(?=.{1,253}\Z)[a-z0-9-]+(?:\.[a-z0-9-]+)+\Z')


def _origin(value):
    u = urlsplit(value)
    if (u.scheme != 'https' or not DNS.fullmatch(u.hostname or '') or u.netloc!=u.hostname
            or u.path or u.query or u.fragment or value!='https://'+u.netloc):
        raise ValueError('expected separately verified TLS origin')
    return value


def prepare_gateway(spec, upstream_origin):
    """Add a single method to a verified TEST TLS listener already serving the TEST neutral relay."""
    origin = _origin(upstream_origin)
    if type(spec) is not dict or spec.get('openapi') not in ('3.0.0','3.0.1','3.0.2','3.0.3'):
        raise ValueError('expected the current OpenAPI 3.0 object')
    paths=spec.get('paths')
    if type(paths) is not dict or PATH in paths or PATH+'/' in paths:
        raise ValueError('pause path already exists or spec invalid')
    sibling=paths.get(SIBLING,{}).get('post',{}).get('x-yc-apigateway-integration',{})
    if (sibling.get('type')!='http' or sibling.get('method','POST')!='POST'
            or sibling.get('url')!=origin+SIBLING):
        raise ValueError('known current TEST effects upstream does not exactly match')
    # Do not bypass any existing gateway security or validation extensions.
    # The caller must review ordering if a wildcard / greedy route exists.
    if any(not isinstance(p,str) or ('{' in p and
          (p.split('{')[0]=='/' or PATH.startswith(p.split('{')[0]))) for p in paths):
        raise ValueError('unreviewed overlapping gateway route')
    result=deepcopy(spec)
    result['paths'][PATH]={'post':{
        'summary':'TEST queue pause relay',
        'parameters':[{ 'name':name,'in':'header','required':False,'schema':{'type':'string'}}
                      for name in (HEADER,'Content-Type')],
        'responses':{'default':{'description':'TEST pause authority response'}},
        'x-yc-apigateway-integration':{
             'type':'http','method':'POST','url':origin+PATH,
             'headers':{HEADER:'{'+HEADER+'}','Content-Type':'{Content-Type}'},
             'omitEmptyHeaders':True,'timeouts':{'connect':0.5,'read':4}},
    }}
    if {**result,'paths':{k:v for k,v in result['paths'].items() if k!=PATH}}!=spec:
        raise ValueError('nonadditive gateway candidate')
    return result


def nginx_fragment():
    """Include only inside the separately verified TLS server, not a default/PROD server."""
    return f'''# Scope: exact TEST neutral Base on loopback, never a generalized proxy.
location = {PATH} {{
    access_log off;
    if ($request_method != POST) {{ return 404; }}
    if ($args != "") {{ return 404; }}
    if ($http_x_memory_test_pause_auth !~ "^Bearer [a-f0-9]{{64}}$") {{ return 403; }}
    if ($content_type != "application/json") {{ return 415; }}
    client_max_body_size 1k;
    client_body_timeout 3s;
    proxy_pass_request_headers off;
    proxy_set_header Host 127.0.0.1:{TEST_PORT};
    proxy_set_header Content-Type application/json;
    proxy_set_header Authorization $http_x_memory_test_pause_auth;
    proxy_request_buffering on;
    proxy_next_upstream off;
    proxy_cache off;
    proxy_connect_timeout 1s;
    proxy_read_timeout 4s;
    proxy_pass http://127.0.0.1:{TEST_PORT}{PATH};
}}
'''


def _json(pairs):
    d=dict(pairs)
    if len(d)!=len(pairs):
        raise ValueError('duplicate spec member')
    return d


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec',type=Path,required=True,help='Existing verified Gateway spec, JSON')
    p.add_argument('--out',type=Path,required=True,help='New private staging directory')
    p.add_argument('--verified-tls-origin',required=True)
    x=p.parse_args()
    before=x.spec.read_bytes()
    if len(before)>1_000_000:
        raise ValueError('oversize Gateway spec')
    old=json.loads(before,object_pairs_hook=_json)
    new=prepare_gateway(old,x.verified_tls_origin)
    docs={'gateway.json':(json.dumps(new,sort_keys=True,indent=2,allow_nan=False)+'\n').encode(),
          'nginx-location.conf':nginx_fragment().encode()}
    x.out.mkdir(mode=0o700)
    for name,raw in docs.items():
        with (x.out/name).open('xb') as dest:
            dest.write(raw)
    print(json.dumps({'original_gateway_sha256':sha256(before).hexdigest(),
        'candidate':{key:sha256(raw).hexdigest() for key,raw in docs.items()}},sort_keys=True))


if __name__=='__main__':
    main()
