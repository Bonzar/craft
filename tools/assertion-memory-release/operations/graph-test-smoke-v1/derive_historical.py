"""Offline derivation of the exact TEST smoke update for the one historical installed source."""
from hashlib import sha256

OLD='4cb99c05ed3fe7ba7f64c9d61843ab39612856929957a5fa3ec695e624c9f533'
CANONICAL='f41e1b1825f40c9e8649fb0cac9e2de90d8d1ac09f5cf474a052bf2340036085'
CANONICAL_FIXED='de30ff27dc4918ba263afc7b2310b029e85f6f4007d0a3f8a4cca7e363e71080'
EXPECTED='57d7e0cf47e85f7ecd52ca0cfc9d6acd03eefd9d05f42cdd31b34544b2a92476'

def digest(w):
    if not isinstance(w,bytes) or not 0<len(w)<16384:
        raise ValueError('Expected bounded source bytes')
    return sha256(w).hexdigest()

def get(data,left,right):
    i=data.index(left)
    return data[i:data.index(right,i)]

def derive(old,canonical,new):
    if (digest(old),digest(canonical),digest(new))!=(OLD,CANONICAL,CANONICAL_FIXED):
        raise ValueError('Unknown TEST helper input bytes')
    parts=[
        (get(canonical,b'STATE =',b'\nTOOLS ='),get(new,b'STATE =',b'\nTOOLS =')),
        (get(canonical,b'def graph(',b'\n\ndef smoke_mcp'),get(new,b'def graph(',b'\n\ndef smoke_mcp')),
        (b"httpx.Client(base_url='http://localhost:8891', trust_env=False, timeout=3)",
         b"httpx.Client(base_url=LOCAL_GRAPH,trust_env=False,timeout=3,follow_redirects=False)"),
    ]
    fixed=old
    for previous,replacement in parts:
        if fixed.count(previous)!=1:
            raise ValueError('Ambiguous historical TEST helper scope')
        fixed=fixed.replace(previous,replacement,1)
    if digest(fixed)!=EXPECTED:
        raise ValueError('Unapproved historical TEST helper output')
    inverse=fixed
    for previous,replacement in reversed(parts):
        if inverse.count(replacement)!=1:
            raise ValueError('Unexpected additional edit')
        inverse=inverse.replace(replacement,previous,1)
    if inverse!=old:
        raise ValueError('Historical transformation is not exactly reversible')
    compile(fixed,'exact-installed-test-health.py','exec')
    return fixed
