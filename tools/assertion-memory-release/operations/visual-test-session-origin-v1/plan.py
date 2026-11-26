"""Offline TEST-only plan/inverse for one session Origin header. Never writes."""
import argparse
import hashlib
import sys

BEFORE = 'a65840b837282a3dc23d8b7e12d8ac63cd910efdd466de7144fef690cdde0515'
AFTER = 'b29a1fb8d880fce8f53ff6ca7e57d7eea435c8dd8e2a2f028adbe6e64f5e5103'
PATH = b'location = /__mvt/api/v1/session {'
OLD = b'proxy_set_header Origin $mvt_graph_origin;'
NEW = b'proxy_set_header Origin $http_origin;'
MAX = 128 * 1024

def digest(data):
    return hashlib.sha256(data).hexdigest()

def delta(data, rollback=False):
    if data.count(PATH) != 1:
        raise ValueError('session_location_count')
    start = data.index(PATH)
    end = data.index(b'\n}', start)
    region = data[start:end]
    source, target = (NEW, OLD) if rollback else (OLD, NEW)
    if region.count(source) != 1 or target in region:
        raise ValueError('session_origin_directive_mismatch')
    if b'proxy_pass_request_headers off;' not in region or b'127.0.0.1:8891/api/v1/session' not in region:
        raise ValueError('session_proxy_binding_mismatch')
    return data[:start] + region.replace(source, target, 1) + data[end:]

def checked(data, rollback=False):
    src, dst = (AFTER, BEFORE) if rollback else (BEFORE, AFTER)
    if not data or len(data) > MAX or digest(data) != src:
        raise ValueError('test_routes_pin_mismatch')
    candidate = delta(data, rollback)
    if digest(candidate) != dst or delta(candidate, not rollback) != data:
        raise ValueError('test_candidate_pin_mismatch')
    return candidate

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--rollback', action='store_true')
    p.add_argument('--emit', action='store_true', help='Emit derived bytes to caller-controlled private stdout')
    args=p.parse_args()
    try:
        value=checked(sys.stdin.buffer.read(MAX+1),args.rollback)
    except ValueError as e:
        print(str(e),file=sys.stderr)
        return 2
    if args.emit:
        sys.stdout.buffer.write(value)
    else:
        print(digest(value))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
