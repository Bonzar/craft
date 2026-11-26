"""Source-only candidate: derive two scoped TEST routes from an already verified regex BFF."""
from copy import deepcopy
from hashlib import sha256
import ipaddress
import re

from queue_owner_ingress import owner_routes, METHODS
from clear_v2_ingress import ROOT, CLEAR, LEGACY, spec_pin

PREFIX='/__mvt'
AUTH='/__mvt_auth'
MARKERS='/etc/nginx/assertion-visual-test-edge/markers.conf'
WILDCARD='/api/v1/{tail+}'
LOC=re.compile(r'^(?P<pad>[ \t]*)location = /__mvt_auth \{[ \t]*\n'
               r'(?P<body>[\s\S]*?)^(?P=pad)\}[ \t]*$', re.M)
MATCH=re.compile(r'^(?P<pad>[ \t]*)location ~ "(?P<regex>[^"\r\n]+)" \{[ \t]*\n'
                 r'(?P<body>[\s\S]*?)^(?P=pad)\}[ \t]*$', re.M)
BASE=('internal;', 'proxy_pass_request_headers off;',
      'proxy_set_header Cookie $http_cookie;', 'proxy_pass http://127.0.0.1:8891/api/v1/capabilities;')
ALLOWED={'Cookie','Origin','Content-Type','X-CSRF-Token','Sec-Fetch-Site'}
PROCESSING=r'^/__mvt/api/v1/processing(?:/[a-z0-9-]+){1,4}$'
STRICT_FALLBACK=re.compile(r'(?m)^\s*location /__mvt/ \{ return 404; \}\s*$')

def one(text, needle):
    if len(re.findall(r'(?m)^\s*'+re.escape(needle)+r'\s*$',text))!=1:
        raise ValueError('The existing TEST carrier does not have its exact scoped directives')

def witnesses(nginx, host):
    matches=list(LOC.finditer(nginx))
    if len(matches)!=1:
        raise ValueError('Expected one trusted scoped TEST owner callback')
    body=matches[0]['body']
    for word in BASE:
        one(body,word)
    if re.search(r'(?im)^\s*(?:include|auth_request|rewrite|proxy_set_header\s+Authorization|add_header|error_page|satisfy)\b',
                 body):
        raise ValueError('TEST owner callback may redirect or reuse other authority')
    carriers=[b for b in MATCH.finditer(nginx) if b['regex']==PROCESSING]
    if len(carriers)!=1:
        raise ValueError('Expected exactly the observed scoped TEST processing BFF')
    b=carriers[0]
    for directive in (f'auth_request {AUTH};', f'include {MARKERS};',
                      'proxy_pass_request_headers off;', f'proxy_set_header Host {host};',
                      'proxy_set_header Origin $http_origin;', 'proxy_pass http://127.0.0.1:8829;'):
        one(b['body'],directive)
    if re.search(r'(?im)^\s*(?:proxy_set_header\s+Authorization|error_page|satisfy)\b',b['body']):
        raise ValueError('Unexpected TEST owner control authority')
    for _,suffix in METHODS:
        for path in (PREFIX+ROOT+CLEAR+suffix,PREFIX+ROOT+LEGACY+suffix):
            if path in nginx:
                raise ValueError('Existing clear path must be compared, not retried')
    if len(STRICT_FALLBACK.findall(nginx))!=1:
        raise ValueError('Exact existing TEST deny fallback is required')
    return b

def plan(spec, nginx, *, host, ipv4, previous_nginx_sha, previous_gateway_sha,
         owner_dashboard_proven=False):
    if owner_dashboard_proven is not True:
        raise ValueError('Authenticated exact TEST owner controls not witnessed')
    if len(nginx.encode())>128*1024 or sha256(nginx.encode()).hexdigest()!=previous_nginx_sha:
        raise ValueError('Nginx baseline bytes changed')
    if spec_pin(spec)!=previous_gateway_sha:
        raise ValueError('Gateway baseline bytes changed')
    b=witnesses(nginx,host)
    here=spec['paths']
    ip=str(ipaddress.IPv4Address(ipv4))
    target=f'https://{ip}{PREFIX}/api/v1/{{tail}}'
    expected,rendered=owner_routes(lane='test',host=host,backend_ip=ipv4,
                                  prefix=PREFIX,owner_auth=AUTH,marker_include=MARKERS)
    edits={}
    for verb,suffix in METHODS:
        name=verb.lower()
        anchor=here.get(WILDCARD,{}).get(name,{})
        integ=anchor.get('x-yc-apigateway-integration',{})
        if integ.get('type')!='http' or integ.get('method',verb)!=verb or integ.get('url')!=target:
            raise ValueError('Unknown live TEST Gateway owner write carrier')
        forwarding=integ.get('headers',{})
        required={'Cookie','Origin','Content-Type'} | ({'X-CSRF-Token'} if verb=='PUT' else set())
        if not required<=set(forwarding)<=ALLOWED or any(forwarding[k]!='{'+k+'}' for k in forwarding):
            raise ValueError('Unknown TEST Gateway owner credential scope')
        path=ROOT+CLEAR+suffix
        if path in here or ROOT+LEGACY+suffix in here:
            raise ValueError('Existing V2 or legacy entry must not be duplicated')
        edits[path]=expected[path]
    candidate=deepcopy(spec)
    candidate['paths'].update(edits)
    start=rendered.index('location = '+PREFIX+ROOT+CLEAR+'/prepare {')
    allowed=rendered[start:]
    # Explicit exact locations take precedence over the observed broad BFF.
    # Do not remove the denies when rolling back both application components.
    deny=lambda operation: ''.join(f'location = {PREFIX+ROOT+operation+suffix} {{\n'
                                     '    return 404;\n}\n' for _,suffix in METHODS)
    pad=b['pad']
    def with_locations(text):
        extra=''.join(pad+line for line in text.splitlines(keepends=True))
        pos=b.start()
        return nginx[:pos]+extra+nginx[pos:]
    dark=with_locations(deny(LEGACY)+deny(CLEAR))
    active=with_locations(deny(LEGACY)+allowed)
    if dark==active:
        raise ValueError('TEST V2 activation must be explicit')
    return {
        'baseline': {'gateway_sha256':previous_gateway_sha,'nginx_sha256':previous_nginx_sha},
        'dark': {'gateway':spec, 'nginx':dark, 'gateway_sha256':previous_gateway_sha,
                 'nginx_sha256':sha256(dark.encode()).hexdigest()},
        'active': {'gateway':candidate, 'nginx':active, 'gateway_sha256':spec_pin(candidate),
                   'nginx_sha256':sha256(active.encode()).hexdigest()},
    }


def rollback(plan, current_spec, current_nginx):
    """Return the previously reviewed fail-closed pair, or abort on unknown drift."""
    expected=plan['active']
    if (spec_pin(current_spec)!=expected['gateway_sha256']
            or sha256(current_nginx.encode()).hexdigest()!=expected['nginx_sha256']
            or current_spec!=expected['gateway'] or current_nginx!=expected['nginx']):
        raise ValueError('Active TEST ingress drifted; do not roll back a stale plan')
    return deepcopy(plan['dark'])
