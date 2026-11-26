"""Fixed, additive PROD owner-graph-card ingress.

Run: python add_graph_card_routes.py gateway-source.yaml gateway-next.yaml nginx-next.conf
Use only the trusted current gateway spec (it can contain unrelated secrets).
Validate nginx in a private mount namespace against the actual include graph and
verify the gateway's live spec still equals the input before applying either.
TEST uses the existing generic owner Visual ingress; do not replace it.
"""
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit
import sys
import yaml

ANCHOR = '/api/v1/graph/{revision}/{component}'
INTEGRATION = 'x-yc-apigateway-integration'
PREFIX = '/__visual-owner-graph-card'
ROUTES = {
    '/api/v1/graph/{revision}/nodes/{ordinal}': ('revision', 'ordinal'),
    '/api/v1/graph/{revision}/nodes/{ordinal}/edges': ('revision', 'ordinal'),
    '/api/v1/graph/{revision}/edges/{ordinal}': ('revision', 'ordinal'),
    **{f'/api/v1/{category}/{{id}}': ('id',) for category in ('nodes', 'edges', 'sources', 'overviews')},
}

NGINX = r'''
# Visual graph card reads. Existing owner cookie and upstream, no write routes.
location ~ "^/__visual-owner-graph-card/api/v1/(?:graph/[A-Za-z0-9:_-]{1,160}/(?:nodes/[0-9]{1,12}(?:/edges)?|edges/[0-9]{1,12})|(?:nodes|edges|sources|overviews)/[A-Za-z0-9:_@.-]{1,256})$" {
    if ($request_method != GET) { return 405; }
    auth_request /__visual-owner-auth-check;
    rewrite ^/__visual-owner-graph-card(/api/v1/.*)$ $1 break;
    proxy_pass_request_headers off;
    proxy_set_header Host 127.0.0.1:8901;
    proxy_set_header Cookie $http_cookie;
    proxy_cache off;
    access_log off;
    proxy_pass http://127.0.0.1:8901;
}
'''


def add_gateway_routes(raw):
    spec = yaml.safe_load(raw)
    paths = spec['paths']
    original = paths[ANCHOR]['get']
    source = urlsplit(original[INTEGRATION]['url'])
    if (source.scheme != 'https' or not source.hostname or source.username or source.password
            or source.query or source.fragment or source.path != '/__visual-owner-graph'+ANCHOR
            or original[INTEGRATION]['headers'] != {'Cookie': '{Cookie}'}):
        raise ValueError('Unexpected source ingress or owner-cookie mapping')
    if set(ROUTES).intersection(paths) or raw.count('\npaths:\n') != 1:
        raise ValueError('Route collision or unsupported gateway formatting')
    additions = {}
    for route, variables in ROUTES.items():
        operation = deepcopy(original)
        operation['parameters'] = [p for p in operation['parameters'] if p['in'] != 'path']
        operation['parameters'] += [
            {'name': n, 'in': 'path', 'required': True, 'schema': {'type': 'string'}} for n in variables
        ]
        target = operation[INTEGRATION]
        if 'id' in variables:
            operation['parameters'].append(
                {'name': 'revision', 'in': 'query', 'required': False, 'schema': {'type': 'string'}}
            )
            target['query']['revision'] = '{revision}'
        target['url'] = source._replace(path=PREFIX+route).geturl()
        additions[route] = {'get': operation}
    indented = '\n'.join('  '+line for line in yaml.safe_dump(additions, sort_keys=False).splitlines())
    candidate = raw.replace('\npaths:\n', '\npaths:\n'+indented+'\n', 1)
    # Ensure serialization keeps every unrelated route and the rest of the spec unchanged.
    check = yaml.safe_load(candidate)
    if not all(check['paths'].pop(k, None) == v for k,v in additions.items()) or check != spec:
        raise ValueError('Non-additive result')
    return candidate


if __name__ == '__main__':
    base, gateway, nginx = map(Path, sys.argv[1:])
    if len({p.resolve() for p in (base, gateway, nginx)}) != 3:
        raise ValueError('Never overwrite source or reuse output paths')
    candidate = add_gateway_routes(base.read_text())
    gateway.write_text(candidate)
    nginx.write_text(NGINX)
