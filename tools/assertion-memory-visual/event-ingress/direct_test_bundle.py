"""Offline plan/inverse for the two-plane TEST-only direct SSE operator bundle."""
from copy import deepcopy
from hashlib import sha256
import re

from gateway_test_apply import fingerprint
from render_direct_test import (
    DIRECT, ISSUER, PREFIX, PROOF, gateway, nginx, proof_gateway, proof_nginx,
)

def render_routes(before, policy, origin, reverse=False):
    def apply(source):
        return proof_nginx(nginx(source),policy,origin)
    if not reverse:
        return apply(before)
    original=before
    for path in (ISSUER,DIRECT,PROOF):
        pattern=r'^([ \t]*)location = '+re.escape(PREFIX+path)+r' \{\n[\s\S]*?^\1\}\n'
        original,n=re.subn(pattern,'',original,flags=re.M)
        if n!=1:
            raise ValueError('expected each exact TEST-only ingress route once')
    if apply(original)!=before:
        raise ValueError('reviewed TEST routes differ; refusing inverse')
    return original

def render_gateway(before, reverse=False):
    def apply(spec):
        return proof_gateway(gateway(spec))
    if not reverse:
        return apply(before)
    original=deepcopy(before)
    for path in (ISSUER,PROOF):
        if path not in original.get('paths',{}):
            raise ValueError('required TEST-only gateway route is missing')
        del original['paths'][path]
    if apply(original)!=before:
        raise ValueError('reviewed TEST gateway routes differ; refusing inverse')
    return original

def validate_page(page, policy):
    """Bind the one executable block to the independently reviewed CSP."""
    import base64
    match=re.fullmatch(r'[\s\S]*?<script>([\s\S]+)</script></html>\n',page)
    if match is None or page.count('<script>')!=1 or page.count('</script>')!=1:
        raise ValueError('expected only one proof script')
    if len(page.encode())>32768:
        raise ValueError('oversize proof page')
    expected=base64.b64encode(sha256(match[1].encode()).digest()).decode()
    if policy.split('; ')[1]!=f"script-src 'sha256-{expected}'":
        raise ValueError('unreviewed proof script')
    return sha256(page.encode()).hexdigest()

def plans(routes,spec,page,policy,origin,*,reverse=False):
    html_sha=validate_page(page,policy)
    next_routes=render_routes(routes,policy,origin,reverse)
    next_spec=render_gateway(spec,reverse)
    return next_routes,next_spec,{
        'test_nginx_before_sha256':sha256(routes.encode()).hexdigest(),
        'test_nginx_after_sha256':sha256(next_routes.encode()).hexdigest(),
        'test_gateway_before_sha256':fingerprint(spec),
        'test_gateway_after_sha256':fingerprint(next_spec),
        'test_proof_html_sha256':html_sha,
    }
