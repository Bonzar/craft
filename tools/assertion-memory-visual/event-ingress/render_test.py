"""Render only the owner Graph SSE route into review copies of existing TEST specs."""
from copy import deepcopy
import argparse
import re
from pathlib import Path
from urllib.parse import urlsplit

PUBLIC_PATH='/api/v1/events'
PRIVATE_PATH='/__mvt/api/v1/events'
WILDCARD='/api/v1/{tail+}'
APPROVED_MACROS={'Cookie','Origin','Sec-Fetch-Site'}

def nginx(source):
    if PRIVATE_PATH in source:
        raise ValueError('events route already present')
    starts=list(re.finditer(r'^([ \t]*)location ~ "\^/__mvt/api/v1/\(\?:graph.*\{[ \t]*$',source,re.M))
    if len(starts)!=1:
        raise ValueError('expected one current TEST Graph regex route')
    start=starts[0]
    end=re.search(r'^'+re.escape(start[1])+r'\}[ \t]*$',source[start.end():],re.M)
    if end is None:
        raise ValueError('expected end of TEST Graph regex route')
    block=source[start.end():start.end()+end.start()]
    checks=(r'proxy_pass_request_headers\s+off;',r'proxy_set_header\s+Cookie\s+\$http_cookie;',
            r'proxy_set_header\s+Host\s+[^;\s]+;',r'proxy_pass\s+http://127\.0\.0\.1:8891;',
            r'rewrite\s+\^/__mvt\(/\.\*\)\$\s+\$1\s+break;',
            r'proxy_cache\s+off;')
    if any(len(re.findall(pattern,block))!=1 for pattern in checks):
        raise ValueError('unexpected TEST Graph route shape')
    if re.search(r'(?i)proxy_set_header\s+(?:Authorization|X-Memory-Visual-Owner-Ingress)\s+',block):
        raise ValueError('expected existing Graph owner cookie authority only')
    # Mirror established exact Host and cookie; no browser Authorization.
    host=re.search(r'proxy_set_header\s+Host\s+([^;\s]+);',block)[1]
    pad=start[1]
    directives=(
        f'location = {PRIVATE_PATH} {{',
        '    if ($request_method != GET) { return 405; }',
        '    proxy_pass_request_headers off;',
        f'    proxy_set_header Host {host};',
        '    proxy_set_header Cookie $http_cookie;',
        '    proxy_set_header Last-Event-ID $http_last_event_id;',
        '    proxy_set_header Connection "";',
        '    proxy_http_version 1.1;',
        '    proxy_buffering off;',
        '    proxy_cache off;',
        '    proxy_read_timeout 15s;',
        '    proxy_pass http://127.0.0.1:8891/api/v1/events;',
        '}',
    )
    return source[:start.start()]+''.join(pad+line+'\n' for line in directives)+source[start.start():]

def apigw(spec):
    paths=spec.get('paths',{})
    current=paths.get(WILDCARD,{}).get('get',{})
    incoming=current.get('x-yc-apigateway-integration',{})
    if PUBLIC_PATH in paths or incoming.get('type')!='http' or incoming.get('method','GET')!='GET':
        raise ValueError('expected current TEST HTTP wildcard only')
    target=urlsplit(incoming.get('url',''))
    if (target.scheme!='https' or not target.hostname or target.username or target.password
        or target.port not in (None,443) or target.path!='/__mvt/api/v1/{tail}'
        or target.query or target.fragment):
        raise ValueError('unexpected TEST wildcard target')
    old_headers=incoming.get('headers',{})
    expected={header:'{'+header+'}' for header in APPROVED_MACROS}
    if any(old_headers.get(name)!=value for name,value in expected.items()):
        raise ValueError('expected established TEST owner cookie and Origin mappings')
    params=[]
    for key in ('Cookie','Origin','Sec-Fetch-Site','Last-Event-ID'):
        params.append({'name':key,'in':'header','required':False,'schema':{'type':'string'}})
    for key in ('after','revision'):
        params.append({'name':key,'in':'query','required':False,'schema':{'type':'string'}})
    integration={
        'type':'http','url':target._replace(path=PRIVATE_PATH).geturl(),
        'headers':{**expected,'Last-Event-ID':'{Last-Event-ID}'},
        'query':{'after':'{after}','revision':'{revision}'},
        'omitEmptyHeaders':True,'omitEmptyQueryParameters':True,
        'timeouts':{'connect':3,'read':22},
    }
    if 'serviceAccountId' in incoming:
        integration['serviceAccountId']=incoming['serviceAccountId']
    copied=deepcopy(spec)
    copied['paths'][PUBLIC_PATH]={'get':{
        'parameters':params,
        'responses':{'200':{'description':'Owner Graph event stream'},'401':{'description':'Owner session required'}},
        'x-yc-apigateway-integration':integration,
    }}
    return copied

def main():
    import yaml
    p=argparse.ArgumentParser()
    p.add_argument('nginx_config',type=Path);p.add_argument('gateway_spec',type=Path);p.add_argument('review_dir',type=Path)
    a=p.parse_args()
    n=nginx(a.nginx_config.read_text());g=apigw(yaml.safe_load(a.gateway_spec.read_text()))
    a.review_dir.mkdir(mode=0o700)
    for output,data in [('routes.conf',n),('gateway.yaml',yaml.safe_dump(g,allow_unicode=True,sort_keys=False))]:
        with (a.review_dir/output).open('x') as f:
            f.write(data)

if __name__=='__main__':
    main()
