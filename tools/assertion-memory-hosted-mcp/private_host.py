"""Loopback-only launcher for one verified Base release's private JSON operations."""
from __future__ import annotations

import fcntl
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat

SCOPES = {
    'test': ('assertion-test-deploy', Path('/srv/assertion-release/test')),
    'prod': ('assertion-production-deploy', Path('/srv/assertion-release/production')),
}
HEX = re.compile(r'[a-f0-9]{64}\Z')
TAG = re.compile(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]{1,30})?\Z')
PROTECTED = ('BASE_RELEASE_TAG','REVISION','mcp-tool-schema.json',
             'prototypes/assertion_memory/operation_registry.py',
             'prototypes/assertion_memory/mcp_server.py')


def read_regular(path, limit, *, owner=None, private=False):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as stream:
        entry=os.fstat(stream.fileno())
        if (not stat.S_ISREG(entry.st_mode) or entry.st_mode & 0o022
                or owner is not None and entry.st_uid!=owner
                or private and entry.st_mode & 0o077):
            raise ValueError('Untrusted scoped configuration file')
        data=stream.read(limit+1)
        if len(data)>limit:
            raise ValueError('Scoped file exceeds maximum size')
        return data


def active(root):
    root=Path(root)
    destination=(root/'current').resolve(strict=True)
    if (root.resolve(strict=True)!=root or (root/'releases').is_symlink()
            or destination.parent!=root/'releases'):
        raise ValueError('Wrong active Base release path')
    match=re.fullmatch(r'([a-f0-9]{40})-([a-f0-9]{64})',destination.name)
    if not match:
        raise ValueError('Wrong active Base release identity')
    return destination,match[1],match[2]


def manifest_files(release):
    manifest=read_regular(release/'SHA256SUMS',5*1024**2).decode('ascii')
    pins={}
    for line in manifest.splitlines():
        digest,sep,name=line.partition('  ')
        name=name.removeprefix('./')
        if not sep or not HEX.fullmatch(digest) or name in pins:
            raise ValueError('Invalid Base release manifest')
        pins[name]=digest
    result={}
    for name in PROTECTED:
        data=read_regular(release/name,100_000)
        if sha256(data).hexdigest()!=pins.get(name):
            raise ValueError('Base release catalog or tag bytes changed')
        result[name]=data
    return result


def catalog_pins(revision, files, source):
    from policy import digest
    modules={}
    for name in ('operation_registry','mcp_server'):
        modules['prototypes.assertion_memory.'+name]=sha256(
            files['prototypes/assertion_memory/'+name+'.py']).hexdigest()
    return {'revision':revision,'module_sha256':digest(modules),
            'schema_sha256':source['schema_sha256'],'registry':'base-typed-operations-v1'}


def configured(stage,root,config_dir):
    raw=read_regular(config_dir/'private-operations.json',12_000,owner=0)
    value=json.loads(raw)
    required={'capabilities','read_token_file','effect_token_file'}
    if type(value) is not dict or set(value)!=required:
        raise ValueError('Unexpected private operations configuration')
    caps=value['capabilities']
    if caps not in [['baseline_read'],['baseline_read','intake_process'],
                    ['baseline_read','intake_process','generate']]:
        raise ValueError('Unsupported private capability scope')
    def token(val, filename):
        candidate=root/'state'/filename
        if val!=str(candidate):
            raise ValueError('Credential path outside scoped state')
        result=read_regular(candidate,256,owner=os.getuid(),private=True).decode('ascii').strip()
        if not HEX.fullmatch(result):
            raise ValueError('Invalid scoped token')
        return result
    read=token(value['read_token_file'],'private-operations-read-token')
    effect=token(value['effect_token_file'],'private-operations-effects-token') if 'intake_process' in caps else None
    if (effect is not None and effect==read
            or effect is None and value['effect_token_file'] is not None):
        raise ValueError('Invalid separation of private credentials')
    return caps,read,effect


def effect_lock(app,root,stage,effect_token):
    """Writes may never straddle activation. Catalog/health never takes this lock."""
    async def locked(scope,receive,send):
        if scope.get('type')!='http' or scope.get('path')!=f'/memory-mcp-{stage}-effects':
            return await app(scope,receive,send)
        headers=[value for key,value in scope.get('headers',()) if key.lower()==b'authorization']
        if (effect_token is None or len(headers)!=1 or not hmac.compare_digest(
                headers[0],('Bearer '+effect_token).encode('ascii'))):
            return await app(scope,receive,send)
        fd=None
        try:
            fd=os.open(root/'deploy.lock',os.O_RDONLY|os.O_NOFOLLOW)
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077 or info.st_uid!=os.getuid():
                raise ValueError('Untrusted release lock')
            fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except (OSError,ValueError):
            if fd is not None:
                os.close(fd)
            await send({'type':'http.response.start','status':503,'headers':[
                (b'content-type',b'application/json'),(b'cache-control',b'no-store')]})
            await send({'type':'http.response.body','body':b'{"error":"release_transition"}'})
            return
        try:
            return await app(scope,receive,send)
        finally:
            os.close(fd)
    return locked


def factory(stage,root,config_dir,legacy_token):
    from compile_functional_release import compile_release
    from policy import digest
    from vm_relay import make_app
    from prototypes.assertion_memory.operation_registry import create_operations
    release,revision,archive=active(root)
    if Path(__file__).resolve().parent!=release/'tools/assertion-memory-hosted-mcp':
        raise ValueError('Launcher is not the current release source')
    files=manifest_files(release)
    tag=files['BASE_RELEASE_TAG'].decode('ascii').strip()
    if not TAG.fullmatch(tag) or files['REVISION'].decode('ascii').strip()!=revision:
        raise ValueError('Tag/source pointer mismatch')
    source=json.loads(files['mcp-tool-schema.json'])
    pins=catalog_pins(revision,files,source)
    caps,read,effect=configured(stage,root,config_dir)
    if legacy_token in (read,effect):
        raise ValueError('Legacy credential cannot authorize private operations')
    catalog=compile_release(source,pins,effects=effect is not None)
    profile={'lane':stage,'base_release':tag,'enabled_capabilities':caps,
             'catalog_sha256':catalog['catalog_sha256'],'require_catalog_identity':True}
    if effect is not None:
        without=compile_release(source,pins,effects=False)
        profile['read_catalog_sha256']=without['catalog_sha256']
    generation=digest({'revision':revision,'archive':archive,'tag':tag,'lane':stage})

    def live_identity():
        if active(root)!=(release,revision,archive):
            raise ValueError('Private process is no longer the active release')
        return tag,generation

    paths={f'/memory-mcp-{stage}-tools'}
    if effect is not None:
        paths.add(f'/memory-mcp-{stage}-effects')

    def mount(config,store,service,background):
        db=Path(config.db)
        if db.parent!=root/'state' or db.resolve(strict=True)!=db:
            raise ValueError('Private database not the canonical lane database')
        operations=create_operations(config,store,background=background,service=service)
        app=make_app(profile,catalog,read,live_identity,effect_token=effect,operations=operations)
        return frozenset(paths),effect_lock(app,root,stage,effect)
    return mount
