"""Pure, inert render for the two exact PROD owner-stream ingress locations."""
from copy import deepcopy
from ipaddress import IPv4Address
import re
from urllib.parse import urlsplit

# Anchors are the existing reviewed routes, not request-controlled routing.
ORIGIN='https://d5dus2psgclvh5ulbtbh.fovt0b64.apigw.yandexcloud.net'
PROD_ANCHOR='/api/v1/graph/{revision}/{component}'
PROD_ANCHOR_EDGE='/__visual-owner-graph'+PROD_ANCHOR
TICKET='/api/v1/events-ticket'
DIRECT='/api/v1/events-direct'
PRIVATE='/__visual-prod-stream'
AUTH='/__visual-owner-auth-check'
UPSTREAM='127.0.0.1:8901'
HTTP='x-yc-apigateway-integration'
FIELDS=('Cookie','Origin','X-CSRF-Token')


def ipv4(value):
    ip=IPv4Address(value)
    if not ip.is_global or value!=str(ip):
        raise ValueError('Expected existing public canonical IPv4')
    return str(ip)


def gateway(spec, *, direct_ip):
    """Add only the authenticated same-origin POST. Gateway never streams."""
    direct_ip=ipv4(direct_ip)
    paths=spec['paths']
    if any(p in paths for p in (TICKET,DIRECT,PRIVATE+DIRECT)):
        raise ValueError('New PROD route already exists')
    source=paths[PROD_ANCHOR]['get'][HTTP]
    origin=urlsplit(source['url'])
    if (source.get('type')!='http' or source.get('method','GET')!='GET'
            or source.get('headers')!={'Cookie':'{Cookie}'}
            or origin.geturl()!=f'https://{direct_ip}{PROD_ANCHOR_EDGE}'):
        raise ValueError('Expected existing pinned PROD owner Graph ingress')
    out=deepcopy(spec)
    integration={'type':'http','method':'POST','url':f'https://{direct_ip}{PRIVATE+TICKET}',
                 'headers':{name:'{'+name+'}' for name in FIELDS},'omitEmptyHeaders':True,
                 'timeouts':{'connect':3,'read':8}}
    if 'serviceAccountId' in source:
        integration['serviceAccountId']=source['serviceAccountId']
    out['paths'][TICKET]={'post':{'parameters':[
        {'name':name,'in':'header','required':True,'schema':{'type':'string'}} for name in FIELDS],
        'responses':{'200':{'description':'One-use private PROD event-stream permit'},
                     '401':{'description':'Owner login required'}},
        HTTP:integration}}
    return out


def nginx(existing, *, direct_ip):
    """Return an additive include fragment; existing route bytes never change."""
    direct_ip=ipv4(direct_ip)
    if type(existing) is not str or len(existing)>256_000 or '\r' in existing:
        raise ValueError('Unrecognized PROD ingress')
    if any(p in existing for p in (PRIVATE,TICKET,DIRECT)):
        raise ValueError('New PROD route already exists')
    # Exact existing owner boundary. Additional undisclosed directives do not
    # qualify the Nginx include; actual owner and anonymous probes are required.
    auth=list(re.finditer(r'^([ \t]*)location = '+re.escape(AUTH)
                         +r' \{[ \t]*\n([\s\S]*?)^\1\}[ \t]*$',existing,re.M))
    if len(auth)!=1:
        raise ValueError('Expected PROD owner auth handler')
    directives=auth[0][2]
    required=(r'\binternal;',r'proxy_pass_request_headers\s+off;',
              r'proxy_set_header\s+Cookie\s+\$http_cookie;',
              r'proxy_pass\s+http://127\.0\.0\.1:8901/api/v1/capabilities;')
    if (any(len(re.findall(p,directives))!=1 for p in required)
            or re.search(r'proxy_set_header\s+Authorization\s',directives,re.I)):
        raise ValueError('Unproven PROD owner authorization')
    common=f'''    if ($args != "") {{ return 404; }}
    if ($http_origin != "{ORIGIN}") {{ return 403; }}
    proxy_pass_request_headers off;
    proxy_set_header Host {UPSTREAM};
    proxy_set_header Origin $http_origin;
    proxy_cache off;
    proxy_redirect off;
    proxy_hide_header Set-Cookie;
    access_log off;'''
    return f'''
location = {PRIVATE+TICKET} {{
    if ($request_method != POST) {{ return 405; }}
    auth_request {AUTH};
    client_max_body_size 1k;
{common}
    proxy_set_header Cookie $http_cookie;
    proxy_set_header X-CSRF-Token $http_x_csrf_token;
    proxy_set_header Content-Length $content_length;
    proxy_pass http://{UPSTREAM}{TICKET};
}}
location = {PRIVATE+DIRECT} {{
    if ($request_method !~ "^(GET|OPTIONS)$") {{ return 405; }}
    if ($host != "{direct_ip}") {{ return 404; }}
    if ($http_cookie != "") {{ return 403; }}
{common}
    proxy_set_header Authorization $http_authorization;
    proxy_set_header Access-Control-Request-Method $http_access_control_request_method;
    proxy_set_header Access-Control-Request-Headers $http_access_control_request_headers;
    proxy_set_header Connection "";
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 15s;
    gzip off;
    proxy_pass http://{UPSTREAM}{DIRECT};
}}
'''


def owner_csp(policy, *, direct_ip):
    direct_ip=ipv4(direct_ip)
    old="connect-src 'self';"
    if policy.count('connect-src ')!=1 or policy.count(old)!=1:
        raise ValueError('Expected exactly the current restrictive owner CSP')
    return policy.replace(old,"connect-src 'self' https://"+direct_ip+";")
