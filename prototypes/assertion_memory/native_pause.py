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


def make_native_pause(*, actor, token, prepare, change):
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
                    if len(data)>1024:
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
            if action=='prepare' and not args:
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
            if (type(result) is not dict or set(result)!={'stage','paused','revision'}
                    or result['stage']!='test' or type(result['paused']) is not bool
                    or type(result['revision']) is not str or not REVISION.fullmatch(result['revision'])):
                raise ValueError()
        except Conflict:
            return await reply(409,{'error':'queue_control_changed'})
        except Exception:
            return await reply(503,{'error':'pause_unavailable'})
        return await reply(200,result)
    return app
