"""Plan only: add exact synthetic TEST ticket/direct paths, never write a live config."""
from copy import deepcopy
from ipaddress import ip_address
import re
from urllib.parse import urlsplit
from render_test import apigw as validate_gateway_get, nginx as validate_nginx, PRIVATE_PATH, PUBLIC_PATH, WILDCARD

ISSUER='/api/v1/events-ticket'
DIRECT='/api/v1/events-direct'
PREFIX='/__mvt'
ISSUER_HEADERS={'Cookie':'{Cookie}','Origin':'{Origin}','X-CSRF-Token':'{X-CSRF-Token}'}

def gateway(spec):
    attested=validate_gateway_get(spec)
    paths=spec.get('paths',{})
    if ISSUER in paths or DIRECT in paths:
        raise ValueError('direct event routes already present')
    carrier=paths[WILDCARD].get('post',{}).get('x-yc-apigateway-integration',{})
    original=attested['paths'][PUBLIC_PATH]['get']['x-yc-apigateway-integration']['url']
    upstream=urlsplit(carrier.get('url',''))
    target=urlsplit(original)
    if (carrier.get('type')!='http' or carrier.get('method','POST')!='POST'
        or upstream._replace(path=PRIVATE_PATH).geturl()!=target.geturl()
        or upstream.path!='/__mvt/api/v1/{tail}'
        or any(carrier.get('headers',{}).get(k)!=v for k,v in ISSUER_HEADERS.items())):
        raise ValueError('expected same verified TEST POST carrier and owner authority')
    result=deepcopy(spec)
    route={'type':'http','url':target._replace(path=PREFIX+ISSUER).geturl(),
           'method':'POST','headers':dict(ISSUER_HEADERS),'omitEmptyHeaders':True,
           'timeouts':{'connect':3,'read':8}}
    if 'serviceAccountId' in carrier:
        route['serviceAccountId']=carrier['serviceAccountId']
    result['paths'][ISSUER]={'post':{
        'parameters':[{'name':name,'in':'header','required':True,'schema':{'type':'string'}} for name in ISSUER_HEADERS],
        'responses':{'200':{'description':'Private short-lived stream permit'},'401':{'description':'Owner login required'}},
        'x-yc-apigateway-integration':route}}
    return result

def nginx(source):
    if any(PREFIX+path in source for path in (ISSUER,DIRECT)):
        raise ValueError('direct event routes already present')
    checked=validate_nginx(source)
    marker=re.search(r'^([ \t]*)location = '+re.escape(PRIVATE_PATH)+r' \{\n',checked,re.M)
    if marker is None:
        raise ValueError('expected verified TEST Graph insertion point')
    hostmatch=re.search(r'proxy_set_header Host ([^;\s]+);',checked[marker.end():])
    if hostmatch is None:
        raise ValueError('expected existing Graph Host')
    host=hostmatch[1]
    common=['    proxy_pass_request_headers off;',
            f'    proxy_set_header Host {host};',
            '    proxy_set_header Origin $http_origin;',
            '    proxy_cache off;',
            '    access_log off;']
    ticket=(
        f'location = {PREFIX+ISSUER} {{',
        '    if ($request_method != POST) { return 405; }',
        '    if ($args != "") { return 404; }',
        '    client_max_body_size 1k;',
        *common,
        '    proxy_set_header Cookie $http_cookie;',
        '    proxy_set_header X-CSRF-Token $http_x_csrf_token;',
        '    proxy_set_header Content-Length $content_length;',
        f'    proxy_pass http://127.0.0.1:8891{ISSUER};',
        '}',
    )
    direct=(
        f'location = {PREFIX+DIRECT} {{',
        '    if ($request_method !~ "^(GET|OPTIONS)$") { return 405; }',
        '    if ($args != "") { return 404; }',
        '    if ($http_cookie != "") { return 403; }',
        *common,
        '    proxy_set_header Authorization $http_authorization;',
        '    proxy_set_header Access-Control-Request-Method $http_access_control_request_method;',
        '    proxy_set_header Access-Control-Request-Headers $http_access_control_request_headers;',
        '    proxy_set_header Connection "";',
        '    proxy_http_version 1.1;',
        '    proxy_buffering off;',
        '    proxy_read_timeout 15s;',
        f'    proxy_pass http://127.0.0.1:8891{DIRECT};',
        '}',
    )
    insert=marker.start()
    return source[:insert]+''.join(marker[1]+line+'\n' for line in (*ticket,*direct))+source[insert:]

def csp(policy,direct_origin):
    target=urlsplit(direct_origin)
    ip=ip_address(target.hostname)
    if not ip.is_global or ip.version!=4 or direct_origin!='https://'+str(ip):
        raise ValueError('expected exact public HTTPS/IP origin for TEST')
    marker="connect-src 'self';"
    if policy.count('connect-src ')!=1 or policy.count(marker)!=1:
        raise ValueError('expected current owner connect-src policy')
    return policy.replace(marker,"connect-src 'self' "+direct_origin+";")
