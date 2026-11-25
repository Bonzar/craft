"""Dedicated owner-only admin HTTP adapter. Never mount in the MCP catalog."""
from __future__ import annotations

import asyncio
import hmac
import json
import re

from admin_operations import AdminError, OwnerContext, fields

HEX = re.compile(r'[0-9a-f]{64}\Z')
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
METHODS = {
    'enroll_device': 'enroll', 'prepare_reset': 'prepare_reset', 'reset_database': 'execute_reset'
}


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AdminError('invalid_request')
        result[key] = value
    return result


def _invalid_constant(_):
    raise AdminError('invalid_request')


def _invalid():
    raise AdminError('invalid_request')


async def _body(receive):
    raw = bytearray()
    async with asyncio.timeout(5):
        while True:
            event = await receive()
            if event.get('type') != 'http.request':
                _invalid()
            raw.extend(event.get('body', b''))
            if len(raw) > 4096:
                _invalid()
            if not event.get('more_body'):
                break
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_strict_object,
                      parse_constant=_invalid_constant)


def make_visual_admin(*, stage, token, owner_hash, live_context, read_status,
                      operations, activation_guard):
    """All supplied capabilities and scope originate in operator configuration."""
    if (stage not in ('test', 'prod') or type(token) is not str or not HEX.fullmatch(token)
            or type(owner_hash) is not str or not HEX.fullmatch(owner_hash)
            or any(not callable(x) for x in (live_context, read_status, activation_guard))):
        raise ValueError('Invalid scoped admin configuration')
    expected = b'Bearer ' + token.encode('ascii')
    allowed_path = f'/memory-visual-{stage}-admin'

    def execute(request):
        fields(request, ('request_id', 'environment', 'action', 'actor', 'arguments'))
        ident, action, arguments = (request[k] for k in ('request_id', 'action', 'arguments'))
        if (request['environment'] != stage or type(ident) is not str or not UUID.fullmatch(ident)
                or type(action) is not str or action not in {*METHODS, 'status'}
                or type(request['actor']) is not str or not HEX.fullmatch(request['actor'])):
            _invalid()
        if not hmac.compare_digest(request['actor'], owner_hash):
            raise AdminError('owner_required')
        # Mutations ride one activation lease. Transport must supply a reset
        # supervisor which does not stop this administration service itself.
        with activation_guard(action):
            ctx = live_context()
            if type(ctx) is not OwnerContext or ctx.stage != stage or ctx.owner != owner_hash:
                raise AdminError('context_changed')
            if action == 'status':
                fields(arguments, ('limit', 'offset'))
                limit, offset = (arguments[k] for k in ('limit', 'offset'))
                if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1000000:
                    _invalid()
                result = dict(read_status(ctx, limit=limit, offset=offset), capabilities={
                    'enroll_device': operations is not None,
                    'reset_database': operations is not None and operations.reset is not None,
                })
            else:
                if operations is None:
                    raise AdminError('admin_unavailable')
                result = getattr(operations, METHODS[action])(ctx, arguments)
            if live_context() != ctx:
                raise AdminError('context_changed')
        return dict(request_id=ident, environment=stage, release_version=ctx.release, result=result)

    async def app(scope, receive, send):
        if scope.get('type') == 'lifespan':
            while True:
                what = (await receive())['type']
                if what == 'lifespan.startup':
                    await send({'type': 'lifespan.startup.complete'})
                if what == 'lifespan.shutdown':
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
        if scope.get('type') != 'http':
            return

        async def reply(status, data):
            b = json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii')
            if len(b) > 125000:
                status, b = 503, b'{"error":"admin_unavailable"}'
            await send({'type': 'http.response.start', 'status': status, 'headers': [
                (b'content-type', b'application/json'), (b'cache-control', b'private, no-store')]})
            await send({'type': 'http.response.body', 'body': b})
        if scope.get('path') != allowed_path or scope.get('method') != 'POST' or scope.get('query_string'):
            return await reply(404, {'error': 'not_found'})
        headers = scope.get('headers', ())
        auth = [v for k, v in headers if k.lower() == b'authorization']
        if len(auth) != 1 or not hmac.compare_digest(auth[0], expected):
            return await reply(403, {'error': 'owner_required'})
        if [v for k, v in headers if k.lower() == b'content-type'] != [b'application/json']:
            return await reply(415, {'error': 'invalid_request'})
        try:
            p = await _body(receive)
            result = await asyncio.to_thread(execute, p)
            await reply(200, result)
        except AdminError as err:
            code = err.code
            if code == 'owner_required':
                await reply(403, {'error': code})
            elif code in ('invalid_request', 'invalid_device_name', 'invalid_device_fingerprint'):
                await reply(400, {'error': 'invalid_request'})
            elif code in ('context_changed', 'reset_busy', 'confirmation_invalid',
                          'device_already_exists', 'device_limit'):
                await reply(409, {'error': 'state_changed'})
            else:
                await reply(503, {'error': 'admin_unavailable'})
        except (UnicodeError, json.JSONDecodeError, TimeoutError):
            await reply(400, {'error': 'invalid_request'})
        except Exception:
            await reply(503, {'error': 'admin_unavailable'})
    return app
