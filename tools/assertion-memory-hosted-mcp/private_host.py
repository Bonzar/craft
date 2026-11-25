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
    if type(value) is not dict or set(value) not in (required,required|{'machine_clients'}):
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
    clients=value.get('machine_clients',{})
    if (type(clients) is not dict or len(clients)>16
            or any(not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,31}',n) or
                   type(digest) is not str or not HEX.fullmatch(digest) for n,digest in clients.items())):
        raise ValueError('Invalid machine client registry')
    machine=tuple(clients.values())
    if len(set(machine))!=len(machine):
        raise ValueError('Machine credentials must be distinct')
    return caps,read,effect,machine,clients.get("personal-mac")


def effect_lock(app,root,stage,effect_token,machine_digests=()):
    """Writes may never straddle activation. Catalog/health never takes this lock."""
    async def locked(scope,receive,send):
        path=scope.get('path')
        if scope.get('type')!='http' or path not in (f'/memory-mcp-{stage}-effects',f'/memory-api-{stage}'):
            return await app(scope,receive,send)
        headers=[value for key,value in scope.get('headers',()) if key.lower()==b'authorization']
        if path==f'/memory-api-{stage}':
            from vm_relay import machine_authorized
            authorized=len(headers)==1 and machine_authorized(headers[0],machine_digests)
        else:
            authorized=(effect_token is not None and len(headers)==1 and
                        hmac.compare_digest(headers[0],('Bearer '+effect_token).encode('ascii')))
        if not authorized:
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


def require_scoped_database(stage,root,value):
    expected={
        'test':root/'state'/'fictional.sqlite3',
        'prod':Path('/var/lib/assertion-memory/memory.sqlite3'),
    }.get(stage)
    if expected is None or type(value) is not str or value!=str(expected):
        raise ValueError('Private database not the canonical lane database')
    if expected.resolve(strict=True)!=expected or not expected.is_file():
        raise ValueError('Canonical lane database must be an exact regular file')


def private_sites_profile(stage, tag, caps, catalog):
    # A provisioned owner-only Sites lane already has both dedicated route
    # credentials when intake_process is enabled. Activation is release-owned:
    # no persistent config rewrite, and rolling back restores the old catalog.
    return {'lane':stage,'base_release':tag,'enabled_capabilities':caps,
            'catalog_sha256':catalog['catalog_sha256'],'require_catalog_identity':True,
            'sites_full_catalog':'intake_process' in caps}

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
    caps,read,effect,machine,personal_mac=configured(stage,root,config_dir)
    if legacy_token in (read,effect) or sha256(legacy_token.encode('ascii')).hexdigest() in machine:
        raise ValueError('Legacy credential cannot authorize private operations')
    catalog=compile_release(source,pins,effects=effect is not None)
    profile=private_sites_profile(stage,tag,caps,catalog)
    profile['durable_query_jobs'] = profile['sites_full_catalog']
    if effect is not None:
        without=compile_release(source,pins,effects=False)
        profile['read_catalog_sha256']=without['catalog_sha256']
    from prototypes.assertion_memory.dashboard_projection import release_generation
    generation=release_generation(revision,archive,tag,stage)

    def live_identity():
        if active(root)!=(release,revision,archive):
            raise ValueError('Private process is no longer the active release')
        return tag,generation

    from prototypes.assertion_memory import native_pause
    try:
        native_pause_config=native_pause.configured(stage,root,config_dir,read_regular=read_regular,
            reserved=set(machine)|{sha256(v.encode('ascii')).hexdigest() for v in (legacy_token,read,effect) if v is not None})
    except (OSError,ValueError,UnicodeError):
        native_pause_config=None
    paths={f'/memory-mcp-{stage}-tools'}
    if native_pause_config is not None:
        paths.add(native_pause.PATH)
    if machine:
        paths.add(f'/memory-api-{stage}')
    if effect is not None:
        paths.add(f'/memory-mcp-{stage}-effects')

    def mount(config,store,service,background):
        require_scoped_database(stage,root,config.db)
        if config.auto_process_fresh_mac and (stage != 'prod' or personal_mac is None):
            raise ValueError('fresh Mac processing requires the configured production Mac credential')
        from prototypes.assertion_memory import genai_traces, query_timing as timing
        genai_traces.configure(stage)
        from prototypes.assertion_memory.mcp_server import WorkerLauncher, _query_with_connection
        from prototypes.assertion_memory.query_jobs import QueryJobs
        from prototypes.assertion_memory.storage import MemoryStore

        def query_locked(**args):
            # The HTTP connection no longer owns this lock while generation runs.
            live_identity()
            fd = os.open(root/'deploy.lock', os.O_RDONLY | os.O_NOFOLLOW)
            try:
                with timing.span('release_lease'):
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                        raise ValueError('Untrusted release lock')
                    fcntl.flock(fd,fcntl.LOCK_SH | fcntl.LOCK_NB)
                    live_identity()
                try:
                    return _query_with_connection(config,**args)
                finally:
                    if background:
                        WorkerLauncher(config).start()
            finally:
                os.close(fd)

        def ready_query(**args):
            live_identity()
            with MemoryStore(config.db) as own:
                return config.service(own).ready_query(**args)

        jobs = QueryJobs(config.db,query_locked,ready_query) if profile['durable_query_jobs'] else None
        operations=create_operations(config,store,background=background,service=service,query_jobs=jobs)
        app=make_app(profile,catalog,read,live_identity,effect_token=effect,operations=operations,machine_digests=machine,
            fresh_mac_digest=personal_mac if config.auto_process_fresh_mac else None)
        handler = effect_lock(app,root,stage,effect,machine)
        if native_pause_config is not None:
            from apps.graph_viewer.server.queue_dashboard import HostedQueueDashboard
            from prototypes.assertion_memory.dashboard_projection import projection_path
            def active_pause_release():
                live_identity()
                return revision,archive
            control=HostedQueueDashboard(stage,projection_path(stage),config.db,
                                         activation=active_pause_release,control_lock=root/'deploy.lock')
            owner, token = native_pause_config
            pause_app=native_pause.make_native_pause(actor=owner,token=token,
                                                   prepare=control.read_pause,change=control.set_pause)
            private_handler=handler
            async def handler(scope,receive,send):
                if scope.get('type')=='http' and scope.get('path')==native_pause.PATH:
                    return await pause_app(scope,receive,send)
                return await private_handler(scope,receive,send)
        handler.query_jobs = jobs
        from prototypes.assertion_memory.dashboard_projection import publish
        handler.queue_dashboard_projection = lambda: publish(
            stage=stage,config=config,release=tag,generation=generation,
            interactive_enabled=jobs is not None)
        return frozenset(paths),handler
    return mount
