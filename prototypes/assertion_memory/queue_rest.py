"""Lane-scoped read-only queue reads on the existing Base HTTP listener."""
from __future__ import annotations

import asyncio
from contextlib import closing
from hashlib import sha256
import hmac
import json
import logging
import os
from pathlib import Path
import pwd
import re
import sqlite3
import stat
import time

from .dashboard_projection import release_generation
from .queue_status import build_queue_status

PATH='/api/v1/queue-status'
TEST_ROOT=Path('/srv/assertion-release/test')
TEST_CONFIG=Path('/etc/assertion-release/test')
TEST_ACCOUNT='assertion-test-deploy'
PROD_ROOT=Path('/srv/assertion-release/production')
PROD_CONFIG=Path('/etc/assertion-release/production')
PROD_ACCOUNT='assertion-production-deploy'
PROD_DATABASE=Path('/var/lib/assertion-memory/memory.sqlite3')
PROD_HTTP_CONFIG=PROD_ROOT/'state'/'http.json'
PROD_PORT=8903
POLICY='queue-read-rest-v1.json'
HEX=re.compile(r'[a-f0-9]{64}\Z')
RELEASE=re.compile(r'([a-f0-9]{40})-([a-f0-9]{64})\Z')
TAG=re.compile(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]{1,30})?\Z')
PAGE=re.compile(rb'(limit|offset)=(0|[1-9][0-9]{0,6})\Z')
LEGACY_RESPONSE_BYTES=131_072
# The standalone Visual queue client already permits 2.4 MB. A full batch
# page can contain 50 * 128 event IDs and their preview revisions; preview
# text is fetched separately. Do not truncate complete cards or change offsets.
BATCH_CARD_RESPONSE_BYTES=2_400_000
HEADERS=[(b'content-type',b'application/json'),(b'cache-control',b'private, no-store'),
         (b'x-content-type-options',b'nosniff')]


def page(raw):
    values={b'limit':50,b'offset':0}
    seen=set()
    if len(raw)>64:
        raise ValueError('Invalid queue page')
    for pair in raw.split(b'&') if raw else ():
        match=PAGE.fullmatch(pair)
        if match is None or match[1] in seen:
            raise ValueError('Invalid queue page')
        name,number=match.groups()
        seen.add(name)
        values[name]=int(number)
    limit,offset=values[b'limit'],values[b'offset']
    if not 1<=limit<=50 or offset>1_000_000:
        raise ValueError('Invalid queue page')
    return limit,offset


def exact_database(db):
    info=db.lstat()
    if db.resolve(strict=True)!=db or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
        raise ValueError('Unverified TEST database')
    return info.st_dev,info.st_ino


def snapshot(config, *, identity, database, interactive_enabled, limit, offset, lane='test',can_change=False,session_control=False,batch_cards=False,clear_ready=False):
    tag,generation=identity()
    before=exact_database(database)
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,isolation_level=None,timeout=1)) as db:
        db.execute('PRAGMA query_only=ON')
        deadline=time.monotonic()+2
        db.set_progress_handler(lambda:time.monotonic()>=deadline,2000)
        db.execute('BEGIN')
        try:
            selected=config.processing_session_ids
            if session_control and not getattr(config,"auto_process_fresh_mac",False):
                from .queue_session_control import read
                selected,_=read(db,selected)
            data=build_queue_status(db,stage=lane,release_version=tag,
                 processing_session_ids=selected,
                 model_configured=config.provider!='none',interactive_enabled=interactive_enabled,
                 auto_process_fresh_mac=(lane=='prod' and config.auto_process_fresh_mac),limit=limit,offset=offset,batch_cards=batch_cards)
            if lane=='test' or session_control:
                from .queue_session_control import annotate_source, annotate_maintenance
                annotate_source(db,data,selected,fresh_only=getattr(config,"auto_process_fresh_mac",False))
                annotate_maintenance(db,data,selected)
            if (can_change() if callable(can_change) else can_change):
                from .queue_item_removal import annotate
                annotate(db,data)
                if clear_ready:
                    from .database_clear import describe
                    clear=describe(db,stage=lane,binding=[before,(tag,generation)])
                    data['database_clear']={k:clear[k] for k in ('revision','can_clear','scope','capture_rearm')}
        finally:
            db.set_progress_handler(None,0)
            db.rollback()
    if exact_database(database)!=before or identity()!=(tag,generation):
        raise ValueError('TEST release or database changed')
    data['release_generation']=generation
    if (can_change() if callable(can_change) else can_change):
        data['processing_control']['can_change']=True
    return data


def make_queue_rest(*, fingerprint, port, project):
    if not isinstance(fingerprint,str) or not HEX.fullmatch(fingerprint) or type(port) is not int or not 1024<=port<=65535:
        raise ValueError('Invalid Base queue REST configuration')
    expected_host=f'127.0.0.1:{port}'.encode()
    gate=asyncio.Semaphore(4)

    async def app(scope,receive,send):
        async def reply(status,body,*,max_bytes=LEGACY_RESPONSE_BYTES):
            wire=json.dumps(body,separators=(',',':'),ensure_ascii=True).encode('ascii')
            if len(wire)>max_bytes:
                status,wire=503,b'{"error":"unavailable"}'
            await send({'type':'http.response.start','status':status,'headers':
                        [*HEADERS,(b'content-length',str(len(wire)).encode('ascii'))]})
            await send({'type':'http.response.body','body':wire})
        if (scope['type']!='http' or scope['path']!=PATH or
                scope.get('raw_path',PATH.encode())!=PATH.encode()):
            return await reply(404,{'error':'not_found'})
        if scope['method']!='GET':
            return await reply(405,{'error':'method_not_allowed'})
        headers=[(key.lower(),value) for key,value in scope.get('headers',())]
        def values(k):
            return [value for key,value in headers if key==k]
        peer=scope.get('client')
        # Real observed transport identity only; do not accept a proxy claim,
        # even if a loopback proxy is mistakenly configured to forward here.
        if (not peer or peer[0]!='127.0.0.1' or scope.get('server')!=('127.0.0.1',port)
                or values(b'host')!=[expected_host] or any(
                    key==b'forwarded' or key.startswith(b'x-forwarded-') or key in
                    {b'x-real-ip',b'x-memory-environment',b'x-memory-lane'} for key,_ in headers)):
            return await reply(403,{'error':'forbidden'})
        auth=values(b'authorization')
        if (len(auth)!=1 or not re.fullmatch(rb'Bearer [a-f0-9]{64}',auth[0])
                or not hmac.compare_digest(sha256(auth[0][7:]).hexdigest(),fingerprint)):
            return await reply(401,{'error':'unauthorized'})
        try:
            limit,offset=page(scope.get('query_string',b''))
            card_format=values(b'x-memory-queue-format')
            from .database_clear import RESET_HEADER,RESET_FORMAT
            old_reset=values(b'x-memory-queue-reset')
            reset_format=values(RESET_HEADER)
            if (card_format not in ([],[b'batch-cards-v1']) or old_reset not in ([],[b'ready-v1'])
                    or reset_format not in ([],[RESET_FORMAT]) or old_reset and reset_format):
                raise ValueError()
            if values(b'content-length') not in ([],[b'0']) or values(b'transfer-encoding'):
                raise ValueError()
            event=await asyncio.wait_for(receive(),1)
            if event.get('type')!='http.request' or event.get('body') or event.get('more_body'):
                raise ValueError()
        except (ValueError,TimeoutError):
            return await reply(400,{'error':'invalid_request'})
        if gate.locked():
            return await reply(503,{'error':'unavailable'})
        try:
            async with gate:
                result=await asyncio.to_thread(project,limit=limit,offset=offset,
                    **({'batch_cards':True} if card_format else {}),
                    **({'clear_ready':True} if reset_format else {}))
        except (ValueError,OSError,sqlite3.Error):
            return await reply(503,{'error':'unavailable'})
        return await reply(200,result,max_bytes=BATCH_CARD_RESPONSE_BYTES if card_format else LEGACY_RESPONSE_BYTES)

    return app


def _checked_file(path,limit,*,owner=None):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&0o022
                or owner is not None and info.st_uid!=owner):
            raise ValueError('Unverified scoped queue REST file')
        data=stream.read(limit+1)
    if len(data)>limit:
        raise ValueError('Oversize scoped queue REST file')
    return data


def _identity(root, lane='test'):
    current=root/'current'
    path=current.resolve(strict=True)
    match=RELEASE.fullmatch(path.name)
    if (root.resolve(strict=True)!=root or path.parent!=root/'releases' or not current.is_symlink()
            or os.readlink(current)!=str(path) or not match or
            Path(__file__).resolve()!=path/'prototypes/assertion_memory/queue_rest.py'):
        raise ValueError('Unverified current TEST release')
    rev,archive=match.groups()
    tag=_checked_file(path/'BASE_RELEASE_TAG',128)
    revision=_checked_file(path/'REVISION',128)
    tag_text=tag.decode('ascii').strip()
    if not TAG.fullmatch(tag_text) or revision.decode('ascii').strip()!=rev:
        raise ValueError('Unverified TEST release tag')
    manifest=_checked_file(path/'SHA256SUMS',5*1024*1024).decode('ascii').splitlines()
    for name,raw in (('BASE_RELEASE_TAG',tag),('REVISION',revision),
                     ('prototypes/assertion_memory/queue_rest.py',_checked_file(Path(__file__).resolve(),32768))):
        if manifest.count(sha256(raw).hexdigest()+'  ./'+name)!=1:
            raise ValueError('Unverified TEST release manifest')
    return (tag_text,release_generation(rev,archive,tag_text,lane))


def _policy(file, lane, *, strict=False):
    if strict:
        try:
            file.lstat()
        except FileNotFoundError:
            return None
    elif not os.path.lexists(file):
        return None
    parent=file.parent.lstat()
    if (file.resolve(strict=True)!=file or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid!=0 or parent.st_mode&0o022):
        raise ValueError('Unverified lane queue REST switch')
    policy=json.loads(_checked_file(file,512,owner=0))
    if (type(policy) is not dict or set(policy)!={'version','lane','key_sha256'}
            or type(policy['version']) is not int or policy['version']!=1 or policy['lane']!=lane
            or type(policy['key_sha256']) is not str or not HEX.fullmatch(policy['key_sha256'])):
        raise ValueError('Invalid lane queue REST policy')
    return policy['key_sha256']


def _configured_queue_rest(config_path, port, legacy_token):
    if os.getuid()==0 or os.getuid()!=os.geteuid():
        return None
    user=pwd.getpwuid(os.getuid()).pw_name
    if user==TEST_ACCOUNT and Path(config_path)==TEST_ROOT/'state'/'http.json':
        lane, root, config_dir, db = 'test', TEST_ROOT, TEST_CONFIG, TEST_ROOT/'state'/'fictional.sqlite3'
    elif user==PROD_ACCOUNT and port==PROD_PORT and Path(config_path)==PROD_HTTP_CONFIG:
        lane, root, config_dir, db = 'prod', PROD_ROOT, PROD_CONFIG, PROD_DATABASE
    else:
        return None
    file=config_dir/POLICY
    fingerprint=_policy(file,lane,strict=(lane=='prod'))
    if fingerprint is None:
        return None
    if hmac.compare_digest(fingerprint,sha256(legacy_token.encode('ascii')).hexdigest()):
        raise ValueError('Reused lane queue REST authority')
    established=_identity(root,lane)

    def identity():
        if (root/'current').resolve(strict=True)!=Path(__file__).resolve().parents[2]:
            raise ValueError('Queue REST release changed')
        if lane=='prod':
            # Reject an accidental shared key without giving PROD a TEST DB
            # path. Root-managed policy fingerprints are not bearer secrets.
            test_fingerprint=_policy(TEST_CONFIG/POLICY,'test',strict=True)
            if (_policy(file,lane,strict=True)!=fingerprint or test_fingerprint is not None
                    and hmac.compare_digest(fingerprint,test_fingerprint)):
                raise ValueError('Reused or rotated lane queue REST authority')
        return established

    if lane=='prod':
        identity()
    def bind(config, interactive_enabled, *, can_change=False,session_control=False):
        if Path(config.db)!=db or lane=='test' and config.auto_process_fresh_mac:
            logging.getLogger(__name__).warning('queue_rest_database_unavailable')
            return None
        return make_queue_rest(fingerprint=fingerprint,port=port,
            project=lambda **page_:snapshot(config,identity=identity,database=db,
                interactive_enabled=interactive_enabled,lane=lane,can_change=can_change,session_control=session_control,**page_))
    return bind


def configured_queue_rest(config_path, port, legacy_token):
    try:
        return _configured_queue_rest(config_path,port,legacy_token)
    except (OSError,ValueError,UnicodeError):
        # Invalid optional activation must not take the existing Base API down.
        logging.getLogger(__name__).warning('queue_rest_activation_unavailable')
        return None

