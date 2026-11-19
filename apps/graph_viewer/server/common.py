"""Validated public values and bounded cursor transport."""
from __future__ import annotations
import base64
import hashlib
import json
from datetime import datetime, timezone


class APIError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')
    except (ValueError, TypeError, AttributeError):
        raise APIError('invalid_time', 'A valid ISO-8601 instant is required') from None


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def bounded(value, default, maximum, minimum=0):
    try:
        parsed = default if value is None else int(value)
    except (TypeError, ValueError):
        raise APIError('invalid_integer', 'Invalid pagination value') from None
    if parsed < minimum or parsed > maximum:
        raise APIError('invalid_integer', 'Pagination value is outside allowed range')
    return parsed


def cursor_encode(binding, offset):
    return base64.urlsafe_b64encode(json.dumps([binding, offset]).encode()).decode().rstrip('=')


def cursor_decode(value, binding):
    if not value:
        return 0
    try:
        key, offset = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
        if key != binding or type(offset) is not int or offset < 0:
            raise ValueError()
        return offset
    except (ValueError, TypeError, UnicodeError):
        raise APIError('invalid_cursor', 'Cursor does not belong to this revision and query') from None


def selector(params):
    mode = params.get('mode', 'current')
    undated = params.get('undated', 'separate')
    if mode not in {'current', 'known_then', 'known_now'} or undated not in {'separate', 'include', 'only'}:
        raise APIError('invalid_selector', 'Unknown historical perspective or undated selection')
    at = timestamp(params.get('at')) if mode != 'current' else None
    session=params.get('session_id')
    if session is not None and (not isinstance(session,str) or not session.startswith(('native:','logical:')) or len(session)>2048 or len(session.split(':',1)[1])==0 or any(ord(char)<32 for char in session)):
        raise APIError('invalid_selector','Invalid source session selector')
    run=params.get('run_id');snapshot=params.get('output_snapshot_id')
    if (run is not None or snapshot is not None) and session is None:raise APIError('invalid_selector','Run selection requires a source session')
    if run is not None and (not isinstance(run,str) or not run.strip() or len(run)>128):raise APIError('invalid_selector','Invalid run identity')
    if snapshot is not None:
        if type(snapshot) not in (int,str) or isinstance(snapshot,str) and not snapshot.isdecimal():raise APIError('invalid_selector','Invalid output watermark')
        snapshot=bounded(snapshot,0,9007199254740991)
    return {'mode': mode, 'at': at, 'history': params.get('history', '0') in {'1','true'}, 'undated': undated, **({'session_id':session} if session is not None else {}),**({'run_id':run} if run is not None else {}),**({'output_snapshot_id':snapshot} if snapshot is not None else {})}
