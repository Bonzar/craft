"""Inert local REST queue owner endpoint; mounting requires separately verified owner ingress."""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import re

from .processing_control import Conflict, REVISION
from .queue_pause_proof import PATH, ProofRejected, ProvenOwner

HEX = re.compile(r'[0-9a-f]{64}\Z')


def unique(items):
    out = {}
    for key, value in items:
        if key in out:
            raise ValueError('duplicate')
        out[key] = value
    return out


def make_queue_pause_rest(*, stage, enabled=False, read_sha256=None,
                          write_sha256=None, actor_sha256=None, verify_owner=None, prepare=None,
                          change=None, port=None, path=PATH):
    from .queue_session_control import PATH as SESSIONS, normalize
    from .queue_item_removal import PATH as REMOVE, PREVIEW_PATH, check_request
    from .queue_processing_policy import PATH as POLICY, validate
    from .database_clear import PATH as CLEAR
    clear_mode=path==CLEAR
    policy_mode=path==POLICY
    session_mode=path==SESSIONS or policy_mode
    item_mode=path in (REMOVE,PREVIEW_PATH)
    if path!=PATH and not (clear_mode or session_mode or item_mode):
        return None
    if stage not in ('test','prod') or enabled is not True:
        return None
    if (type(port) is not int or not 1 <= port <= 65535 or not callable(prepare)
            or not callable(change) or not callable(verify_owner) or any(type(x) is not str or not HEX.fullmatch(x)
               for x in (read_sha256, write_sha256, actor_sha256))
            or len({read_sha256, write_sha256, actor_sha256}) != 3):
        raise ValueError('Invalid local TEST pause configuration')
    hosts = (f'127.0.0.1:{port}'.encode(), f'[::1]:{port}'.encode())

    async def handler(scope, receive, send):
        async def reply(status, data):
            wire = json.dumps(data, separators=(',', ':')).encode('utf-8')
            await send({'type': 'http.response.start', 'status': status, 'headers': [
                (b'content-type', b'application/json'),
                (b'content-length', str(len(wire)).encode('ascii')),
                (b'cache-control', b'private, no-store')]})
            await send({'type': 'http.response.body', 'body': wire})


        if (scope.get('type') != 'http' or scope.get('path') != path
                or scope.get('raw_path', path.encode()) != path.encode() or scope.get('query_string')):
            return await reply(404, {'error': 'not_found'})
        method = scope.get('method')
        if method not in (('PUT',) if item_mode else ('GET', 'PUT')):
            return await reply(405, {'error': 'method_not_allowed'})
        try:
            peer = ipaddress.ip_address(scope['client'][0])
        except (TypeError, KeyError, ValueError, IndexError):
            return await reply(403, {'error': 'local_only'})
        headers = scope.get('headers', ())

        def values(name):
            return [value for key, value in headers if key.lower() == name]

        if (not peer.is_loopback or scope.get('server')!=('127.0.0.1',port)
                or any(k.lower()==b'forwarded' or k.lower().startswith(b'x-forwarded-')
                       or k.lower()==b'x-real-ip' for k,_ in headers) or len(values(b'host')) != 1
                or values(b'host')[0] not in hosts):
            return await reply(403, {'error': 'local_only'})
        credentials = values(b'authorization')
        target = read_sha256 if method == 'GET' else write_sha256
        if (len(credentials) != 1 or not re.fullmatch(rb'Bearer [0-9a-f]{64}', credentials[0])
                or not hmac.compare_digest(hashlib.sha256(credentials[0][7:]).hexdigest(), target)):
            return await reply(403, {'error': 'not_authorized'})
        args = {}
        if method == 'PUT':
            if values(b'content-type') != [b'application/json']:
                return await reply(415, {'error': 'invalid_request'})
            try:
                body = bytearray()
                async with asyncio.timeout(3):
                    while True:
                        event = await receive()
                        if event.get('type') != 'http.request' or len(body)+len(event.get('body', b'')) > (16_384 if session_mode else 1024):
                            raise ValueError()
                        body.extend(event.get('body', b''))
                        if not event.get('more_body'):
                            break
                request = json.loads(body, object_pairs_hook=unique)
                field='policy' if policy_mode else 'selected_session_ids' if session_mode else 'paused'
                fields=({'expected_revision'} if clear_mode else
                        {'queue_id','item_id','expected_revision'} if item_mode else {field,'expected_revision'})
                if (type(request) is not dict or set(request) != fields
                        or type(request['expected_revision']) is not str
                        or not REVISION.fullmatch(request['expected_revision'])):
                    raise ValueError()
                if item_mode:
                    check_request(request['queue_id'],request['item_id'])
                elif policy_mode:
                    validate(request[field])
                elif session_mode:
                    normalize(request[field])
                elif not clear_mode and type(request[field]) is not bool:
                    raise ValueError()
                args = {'stage': stage, **request}
            except (TimeoutError, ValueError, UnicodeError):
                return await reply(400, {'error': 'invalid_request'})
            # Mandatory verified service authority, not a forwarded identity.
            # A configured verifier attests this exact request and returns the
            # existing authenticated owner digest (or None). No startup
            # default is provided.
            try:
                async with asyncio.timeout(3):
                    proof = await verify_owner(scope, bytes(body), request['expected_revision'])
            except Exception:
                return await reply(403, {'error': 'owner_required'})
            if (type(proof) is not ProvenOwner
                    or proof.path!=path or proof.lane!=stage or not hmac.compare_digest(proof.owner, actor_sha256)):
                return await reply(403, {'error': 'owner_required'})
            args['proof'] = proof
        try:
            state = await asyncio.to_thread(prepare if method == 'GET' else change, **args)
            required={'stage','revision','selected_session_ids','sessions'} if session_mode else {'stage','revision','paused'}
            if policy_mode:
                required={'stage','revision','policy','sessions'}
            if item_mode:
                required=({'stage','queue_id','item_id','removed','effect'} if path==REMOVE
                          else {'stage','queue_id','item_id','source_preview','source_role'})
            if clear_mode:
                field='can_clear' if method=='GET' else 'cleared'
                required={'stage','revision',field}
            if (type(state) is not dict or set(state) != required or state['stage'] != stage
                    or not item_mode and (type(state['revision']) is not str or not REVISION.fullmatch(state['revision']))
                    or not (session_mode or item_mode or clear_mode) and type(state['paused']) is not bool
                    or clear_mode and (type(state[field]) is not bool or method=='PUT' and state[field] is not True)):
                raise ValueError()
        except ProofRejected:
            return await reply(403, {'error': 'owner_required'})
        except Conflict:
            return await reply(409, {'error': 'queue_control_changed'})
        except ValueError:
            return await reply(400 if session_mode else 503, {'error':'invalid_request' if session_mode else
                'database_clear_unavailable' if clear_mode else 'pause_unavailable'})
        except Exception:
            return await reply(503, {'error':'database_clear_unavailable' if clear_mode else 'pause_unavailable'})
        return await reply(200, state)

    return handler

