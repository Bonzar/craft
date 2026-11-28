"""Fixed versioned browser carrier for the qualified Base-owned API, dry render."""
from dataclasses import dataclass
from hashlib import sha256
import re

HOSTS={
    'test':('d5dc14bhh6hba54q0i3p.hsvi2zuh.apigw.yandexcloud.net',8891,'/__mvt'),
    'prod':('d5dus2psgclvh5ulbtbh.fovt0b64.apigw.yandexcloud.net',8902,'/__base_owner_prod_v1'),
}
WIRE={
    'Cookie':'$http_cookie','Origin':'$http_origin','Content-Type':'$http_content_type',
    'X-CSRF-Token':'$http_x_csrf_token','Sec-Fetch-Site':'$http_sec_fetch_site',
    'Accept':'$http_accept','Accept-Encoding':'$http_accept_encoding',
    'If-None-Match':'$http_if_none_match','Last-Event-ID':'$http_last_event_id',
}


@dataclass(frozen=True)
class Qualified:
    stage:str
    revision:str
    archive:str
    component:str
    port:int
    same_database:bool
    owner_post_clear:bool
    graph_live_sse:bool


def render(stage, witness, *, expected_revision, expected_archive):
    """Call on independent released evidence, never browser-supplied input."""
    if (stage not in HOSTS or type(witness) is not Qualified or witness.stage!=stage
            or witness.component!='base-browser-api'
            or not isinstance(expected_revision,str) or not re.fullmatch('[a-f0-9]{40}',expected_revision)
            or not isinstance(expected_archive,str) or not re.fullmatch('[a-f0-9]{64}',expected_archive)
            or (witness.revision,witness.archive)!=(expected_revision,expected_archive)
            or any(v is not True for v in (witness.same_database,witness.owner_post_clear,witness.graph_live_sse))):
        raise ValueError('Exact same-Base lane receiver and parity not qualified')
    host,port,prefix=HOSTS[stage]
    if witness.port!=port:
        raise ValueError('Wrong lane-local browser receiver')
    lines=[f'location ^~ {prefix}/api/v1/ {{',
        '    proxy_pass_request_headers off;',
        f'    proxy_set_header Host {host};',
        *(f'    proxy_set_header {name} {value};' for name,value in WIRE.items()),
        '    proxy_set_header Connection "";',
        '    proxy_http_version 1.1;',
        '    proxy_buffering off;',
        '    proxy_cache off;',
        f'    rewrite ^{prefix}(/api/v1/.*)$ $1 break;',
        f'    proxy_pass http://127.0.0.1:{port};','}']
    body=('\n'.join(lines)+'\n').encode('ascii')
    return {'stage':stage,'body':body,'sha256':sha256(body).hexdigest(),'applied':False}
