"""Pure exact TEST direct-TLS alias shield, with a byte-reproducible inverse."""
from hashlib import sha256
import re

LIMIT=128*1024
PIN=re.compile(r'[a-f0-9]{64}\Z')
HEAD=re.compile(rb'^([ \t]+)location = /memory-api-test \{\n',re.M)
PROXY=re.compile(rb'^([ \t]+)proxy_pass http://127\.0\.0\.1:8893(?:/memory-api-test)?;[ \t]*$',re.M)
# Only the already reviewed production-shaped TEST alias can retain its one
# known, self-contained guard. Neither config bytes nor variables are logged.
PINNED_GUARD_FILE='96cfdf5b5788c869496f66afa25b2401f356044857b0938dcba7f00097325f72'
PINNED_GUARD_SHIELDED='eb8f95c477f2a02abc4f393dbb92cb136f0b47ae0e668c5e4777e95e4db89c97'
PINNED_GUARD_LINE='e751a965d469b579a745a4af355f5f543d0e30fe9525a242255ae6ff91ba9717'
DENIAL=b'return 404; # TEST qa-runner uses only the existing direct loopback API'

def digest(body):
    return sha256(body).hexdigest()

def exact_location(raw):
    if type(raw) is not bytes or not 0<len(raw)<=LIMIT or b'\r' in raw:
        raise ValueError('Invalid exact TEST Nginx baseline bytes')
    h=tuple(HEAD.finditer(raw))
    if len(h)!=1:
        raise ValueError('Expected sole canonical TEST machine location')
    start=h[0];c=re.search(rb'^'+re.escape(start[1])+rb'\}\n',raw[start.end():],re.M)
    if c is None:
        raise ValueError('TEST location boundary is not canonical')
    body=raw[start.end():start.end()+c.start()]
    proxies=tuple(PROXY.finditer(body))
    if len(proxies)!=1:
        raise ValueError('Unexpected TEST private forward')
    indent=proxies[0][1]
    if len(indent)<=len(start[1]):
        raise ValueError('Unexpected TEST body indentation')
    brace_lines=[line for line in body.splitlines(keepends=True) if b'{' in line or b'}' in line]
    if brace_lines:
        if (digest(raw) not in (PINNED_GUARD_FILE,PINNED_GUARD_SHIELDED)
                or len(brace_lines)!=1 or digest(brace_lines[0])!=PINNED_GUARD_LINE
                or body.count(b'{')!=1 or body.count(b'}')!=1
                or not re.fullmatch(re.escape(indent)+rb'if \([^\n{}]+\)[ \t]*\{[^\n{}]+\}[ \t]*\n',brace_lines[0])):
            raise ValueError('Unexpected TEST private forward')
    marker=indent+DENIAL+b'\n'
    return start.end(),body,marker

def derive(before, *, expected_sha256, registered_clients, gateway_paths_absent, alternate_forwards_absent):
    if (not PIN.fullmatch(expected_sha256 or '') or digest(before)!=expected_sha256
            or type(registered_clients) is not int or registered_clients!=0
            or gateway_paths_absent is not True or alternate_forwards_absent is not True):
        raise ValueError('Missing trusted TEST inventory or baseline drift')
    at,body,marker=exact_location(before)
    if marker in body or re.search(rb'^\s*(?:return|internal|rewrite|allow|deny)\b',body,re.M):
        raise ValueError('TEST edge authority already differs; no retry')
    candidate=before[:at]+marker+before[at:]
    return {'before_sha256':digest(before),'after_sha256':digest(candidate),'candidate':candidate,
            'modified_location':'/memory-api-test','writes_performed':False,'prod_bytes_identical':True}

def inverse(current, *, expected_after_sha256, expected_before_sha256):
    if (not PIN.fullmatch(expected_after_sha256 or '') or not PIN.fullmatch(expected_before_sha256 or '')
            or digest(current)!=expected_after_sha256):
        raise ValueError('Current TEST edge bytes differ')
    at,body,marker=exact_location(current)
    if not body.startswith(marker) or body.count(marker)!=1:
        raise ValueError('Expected exact reviewed TEST marker')
    before=current[:at]+current[at+len(marker):]
    expected=derive(before,expected_sha256=expected_before_sha256,registered_clients=0,
                    gateway_paths_absent=True,alternate_forwards_absent=True)
    if expected['candidate']!=current:
        raise ValueError('TEST edge inverse not reproducible')
    return before
