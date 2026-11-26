"""Pure exact TEST direct-TLS alias shield, with a byte-reproducible inverse."""
from hashlib import sha256
import re

LIMIT=128*1024
PIN=re.compile(r'[a-f0-9]{64}\Z')
HEAD=re.compile(rb'^([ \t]+)location = /memory-api-test \{\n',re.M)
PROXY=re.compile(rb'^([ \t]+)proxy_pass http://127\.0\.0\.1:8893(?:/memory-api-test)?;[ \t]*$',re.M)
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
    # Existing probe block is flat; stop for any dynamic routing or nested config.
    if b'{' in body or b'}' in body or len(tuple(PROXY.finditer(body)))!=1:
        raise ValueError('Unexpected TEST private forward')
    indent=PROXY.search(body)[1]
    if len(indent)<=len(start[1]):
        raise ValueError('Unexpected TEST body indentation')
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
