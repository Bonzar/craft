"""Dedicated default-dark TEST native Sites pause capability (not a Graph proxy)."""
import asyncio
import hmac
import json
import os
from pathlib import Path
import re

from .processing_control import Conflict, REVISION

PATH = '/memory-visual-test-pause'
HEX = re.compile(r'[0-9a-f]{64}\Z')


EFFECTS_CARRIER = '/memory-mcp-test-effects'


def via_test_effects(existing, pause, *, stage, pause_token, effect_token):
    """Share only the existing TEST transport; never share authorization."""
    if (stage != 'test' or type(pause_token) is not str or type(effect_token) is not str
            or not HEX.fullmatch(pause_token) or not HEX.fullmatch(effect_token)
            or hmac.compare_digest(pause_token,effect_token)):
        return existing
    expected = b'Bearer ' + pause_token.encode('ascii')

    async def carrier(scope, receive, send):
        if scope.get('type')=='http' and scope.get('path')==EFFECTS_CARRIER:
            # Read normalized ASGI header names, not a payload marker. The pause
            # app independently requires exactly one Authorization header; a
            # duplicate containing our credential must not fall into effects.
            messages = [value for name,value in scope.get('headers',()) if name.lower()==b'authorization']
            if any(hmac.compare_digest(value,expected) for value in messages):
                return await pause({**scope,'path':PATH,'raw_path':PATH.encode('ascii')},receive,send)
        return await existing(scope,receive,send)

    return carrier


def configured(stage, root, config_dir, *, read_regular, reserved):
    # Explicit current TEST only. Neither missing nor invalid data affects MCP.
    source=config_dir/'native-pause.json'
    if stage!='test' or not os.path.lexists(source):
        return None
    data=json.loads(read_regular(source,4096,owner=0))
    token_path=root/'state'/'native-pause-token'
    if (type(data) is not dict or set(data)!={'owner_sha256','token_file'}
            or type(data['owner_sha256']) is not str or not HEX.fullmatch(data['owner_sha256'])
            or data['token_file']!=str(token_path)):
        raise ValueError('Bad native pause authority')
    token=read_regular(token_path,130,owner=os.getuid(),private=True).decode('ascii').strip()
    from hashlib import sha256
    if not HEX.fullmatch(token) or sha256(token.encode('ascii')).hexdigest() in reserved:
        raise ValueError('Reused native pause authority')
    return data['owner_sha256'],token


def _unique(pairs):
    value={}
    for k,v in pairs:
        if k in value:
            raise ValueError('Duplicate request member')
        value[k]=v
    return value


def make_native_pause(*, actor, token, prepare, change, queue_operations=None, queue_sessions=False):
    if not HEX.fullmatch(actor) or not HEX.fullmatch(token):
        raise ValueError('Bad native pause authority')
    expected=b'Bearer '+token.encode('ascii')
    async def app(scope,receive,send):
        from starlette.responses import JSONResponse
        async def reply(status,data):
            r=JSONResponse(data,status_code=status,headers={'cache-control':'private, no-store'})
            await r(scope,receive,send)
        if (scope.get('type')!='http' or scope.get('method')!='POST'
                or scope.get('path')!=PATH or scope.get('query_string')):
            return await reply(404,{'error':'not_found'})
        headers=scope.get('headers',())
        auth=[v for k,v in headers if k.lower()==b'authorization']
        if len(auth)!=1 or not hmac.compare_digest(auth[0],expected):
            return await reply(403,{'error':'owner_required'})
        if [v for k,v in headers if k.lower()==b'content-type']!=[b'application/json']:
            return await reply(415,{'error':'invalid_request'})
        try:
            data=bytearray()
            async with asyncio.timeout(3):
                while True:
                    event=await receive()
                    if event.get('type')!='http.request':
                        raise ValueError()
                    data.extend(event.get('body',b''))
                    if len(data)>(16_384 if queue_operations is not None else 1024):
                        raise ValueError()
                    if not event.get('more_body'):
                        break
            req=json.loads(data,object_pairs_hook=_unique)
            if type(req) is not dict or set(req)!={'stage','action','actor','arguments'} or req['stage']!='test':
                raise ValueError()
            action, who, args = (req[v] for v in ('action','actor','arguments'))
            if type(who) is not str or not HEX.fullmatch(who) or not hmac.compare_digest(who,actor):
                return await reply(403,{'error':'owner_required'})
            if type(args) is not dict:
                raise ValueError()
            owner_action=queue_operations is not None and action in (
                'sessions_prepare','sessions_set','item_preview','item_remove')
            if not owner_action and len(data)>1024:
                raise ValueError()
            if owner_action:
                if action.startswith('sessions_'):
                    if not queue_sessions:
                        raise ValueError()
                    if action=='sessions_prepare' and args:
                        raise ValueError()
                    if action=='sessions_set':
                        from .queue_session_control import normalize
                        if set(args)!={'selected_session_ids','expected_revision'}:
                            raise ValueError()
                        normalize(args['selected_session_ids'])
                else:
                    from .queue_item_removal import check_request
                    if set(args)!={'queue_id','item_id','expected_revision'}:
                        raise ValueError()
                    check_request(args['queue_id'],args['item_id'])
                    if action=='item_preview' and args['queue_id']!='source':
                        raise ValueError()
                if action!='sessions_prepare' and (
                    type(args['expected_revision']) is not str or not REVISION.fullmatch(args['expected_revision'])):
                    raise ValueError()
            if owner_action:
                run=lambda **accepted: queue_operations(action,accepted)
            elif action=='prepare' and not args:
                run=prepare
            elif (action=='set' and set(args)=={'stage','paused','expected_revision'} and args['stage']=='test'
                    and type(args['paused']) is bool and type(args['expected_revision']) is str
                    and REVISION.fullmatch(args['expected_revision'])):
                run=change
            else:
                raise ValueError()
        except (ValueError,TimeoutError,UnicodeError):
            return await reply(400,{'error':'invalid_request'})
        try:
            result=await asyncio.to_thread(run,**args)
            required=({'stage','paused','revision'} if not owner_action else
                      {'stage','revision','selected_session_ids','sessions'} if action.startswith('sessions_') else
                      {'stage','queue_id','item_id','source_preview','source_role'} if action=='item_preview' else
                      {'stage','queue_id','item_id','removed','effect'})
            if (type(result) is not dict or set(result)!=required or result['stage']!='test'
                    or not owner_action and (type(result['paused']) is not bool or type(result['revision']) is not str
                                             or not REVISION.fullmatch(result['revision']))):
                raise ValueError()
        except Conflict:
            return await reply(409,{'error':'queue_control_changed'})
        except ValueError:
            return await reply(400 if owner_action else 503,{'error':'invalid_request' if owner_action else 'pause_unavailable'})
        except Exception:
            return await reply(503,{'error':'pause_unavailable'})
        return await reply(200,result)
    return app
