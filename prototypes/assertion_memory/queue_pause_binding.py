"""Default-dark Base-owned lane pause runtime. No Graph/MCP/effects binding."""
from contextlib import contextmanager, closing
from dataclasses import dataclass
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import pwd
import sqlite3
import stat

from .processing_control import Conflict, read_control
from .queue_pause_proof import apply_once, make_owner_verifier
from .queue_pause_rest import HEX, make_queue_pause_rest, unique
from .queue_rest import _checked_file, _identity, exact_database


@dataclass(frozen=True)
class Profile:
    lane: str
    root: Path
    authority: Path
    account: str
    port: int
    db: Path


TEST = Profile('test', Path('/srv/assertion-release/test'),
               Path('/etc/assertion-release/test'), 'assertion-test-deploy', 8893,
               Path('/srv/assertion-release/test/state/fictional.sqlite3'))
PROD = Profile('prod', Path('/srv/assertion-release/production'),
               Path('/etc/assertion-release/production'), 'assertion-production-deploy', 8903,
               Path('/var/lib/assertion-memory/memory.sqlite3'))
PROFILES = (TEST, PROD)


@contextmanager
def release_lease(profile, *, uid):
    lock = profile.root/'deploy.lock'
    fd = os.open(lock, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != uid
                or before.st_mode&0o077 or before.st_nlink != 1):
            raise ValueError('Untrusted pause release lease')
        fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
        if not os.path.samestat(before, lock.lstat()):
            raise ValueError('Pause release lease changed')
        yield
    finally:
        os.close(fd)


def callbacks(profile, *, uid, identity, established, clock=None):
    def check():
        if identity()!=established:
            raise Conflict('Deployment release changed')
        return exact_database(profile.db)

    def read():
        with release_lease(profile, uid=uid):
            before=check()
            with closing(sqlite3.connect(profile.db.as_uri()+'?mode=ro',uri=True,
                                         isolation_level=None,timeout=1)) as conn:
                conn.execute('PRAGMA query_only=ON')
                paused,revision=read_control(conn)
            if check()!=before:
                raise Conflict('Queue database changed')
            return dict(stage=profile.lane,paused=paused,revision=revision)

    def write(**args):
        with release_lease(profile, uid=uid):
            before=check()
            with closing(sqlite3.connect(profile.db.as_uri()+'?mode=rw',uri=True,
                                         isolation_level=None,timeout=2)) as conn:
                conn.execute('BEGIN IMMEDIATE')
                try:
                    if check()!=before:
                        raise Conflict('Queue database changed')
                    result=apply_once(conn, **args, **({'clock':clock} if clock is not None else {}))
                    if check()!=before:
                        raise Conflict('Queue database changed')
                    conn.commit()
                    return result
                except BaseException:
                    conn.rollback()
                    raise
    return read,write


def _policy(profile):
    marker=profile.authority/'queue-pause-rest-v1.json'
    try:
        marker.lstat()
    except FileNotFoundError:
        return None
    if marker.resolve(strict=True)!=marker:
        raise ValueError('Untrusted pause policy')
    meta=marker.parent.lstat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid!=0 or meta.st_mode&0o022:
        raise ValueError('Untrusted pause policy')
    policy=json.loads(_checked_file(marker,1024,owner=0),object_pairs_hook=unique)
    required={'version','lane','read_sha256','write_sha256','owner_sha256','signer_sha256'}
    fingerprints=('read_sha256','write_sha256','owner_sha256','signer_sha256')
    if (type(policy) is not dict or set(policy)!=required
            or type(policy['version']) is not int or policy['version']!=1
            or policy['lane']!=profile.lane
            or any(type(policy[k]) is not str or not HEX.fullmatch(policy[k]) for k in fingerprints)):
        raise ValueError('Invalid pause authority')
    return policy


def _configure(config_path, port, legacy_token):
    uid=os.getuid()
    if uid==0 or uid!=os.geteuid():
        return None
    name=pwd.getpwuid(uid).pw_name
    matching=[p for p in PROFILES if p.account==name and p.port==port
              and Path(config_path)==p.root/'state'/'http.json']
    if len(matching)!=1:
        return None
    p=matching[0]
    policy=_policy(p)
    if policy is None:
        return None
    read_fp,write_fp,owner,signer=(policy[k] for k in
                          ('read_sha256','write_sha256','owner_sha256','signer_sha256'))
    capabilities={read_fp,write_fp,signer}
    if len(capabilities)!=3:
        raise ValueError('Reused pause authority')
    def check_authorities():
        if _policy(p)!=policy:
            raise ValueError('Rotated pause authority')
        for other in PROFILES:
            if other.lane==p.lane:
                continue
            opposite=_policy(other)
            if opposite is not None and capabilities & {opposite[k] for k in ('read_sha256','write_sha256','signer_sha256')}:
                raise ValueError('Reused pause authority across lanes')
    check_authorities()
    # Exactly one deliberately provisioned owner-verifier key per lane.
    # The signer may be native Sites or a successor verified owner ingress;
    # ordinary proxy/client code must not possess the signer key.
    secret_path=p.authority/'queue-pause-owner-authority-v1.hex'
    if secret_path.resolve(strict=True)!=secret_path:
        raise ValueError('Untrusted pause signer')
    fd=os.open(secret_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        # Host-managed secret readable only by root and the Base lane group.
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_gid!=os.getegid()
                or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o640):
            raise ValueError('Untrusted pause signer')
        encoded=stream.read(66)
    key=encoded.removesuffix(b'\n').decode('ascii')
    if not HEX.fullmatch(key):
        raise ValueError('Invalid pause signer')
    bearer=hashlib.sha256(legacy_token.encode('ascii')).hexdigest()
    signer_as_bearer=hashlib.sha256(key.encode('ascii')).hexdigest()
    if signer != signer_as_bearer or len({read_fp,write_fp,owner,bearer,signer}) != 5:
        raise ValueError('Reused pause authority')
    verifier=make_owner_verifier(bytes.fromhex(key),owner,lane=p.lane)

    current=(p.root/'current').resolve(strict=True)
    established=_identity(p.root,p.lane)
    manifest=_checked_file(current/'SHA256SUMS',5*1024*1024).decode('ascii').splitlines()
    parent=Path(__file__).resolve().parent
    if parent!=current/'prototypes/assertion_memory':
        raise ValueError('Untrusted pause release')
    for name in ('queue_pause_binding.py','queue_pause_rest.py','queue_pause_proof.py'):
        raw=_checked_file(parent/name,65536)
        if manifest.count(hashlib.sha256(raw).hexdigest()+'  ./prototypes/assertion_memory/'+name)!=1:
            raise ValueError('Untrusted pause release manifest')

    def identity():
        check_authorities()
        if (p.root/'current').resolve(strict=True)!=current:
            raise Conflict('Deployment release changed')
        return established

    read,write=callbacks(p,uid=uid,identity=identity,established=established)

    def bind(config):
        if Path(config.db)!=p.db or p.lane=='test' and config.auto_process_fresh_mac:
            return None
        handler=make_queue_pause_rest(stage=p.lane,enabled=True,read_sha256=read_fp,
                    write_sha256=write_fp,actor_sha256=owner,verify_owner=verifier,
                    prepare=read,change=write,port=port)
        def ready():
            try:
                with release_lease(p,uid=uid):
                    return identity()==established and exact_database(p.db) is not None
            except (ValueError,OSError):
                return False
        handler.queue_pause_ready=ready
        if p.lane=='test':
            from . import queue_item_removal as items, queue_session_control as sessions
            from .queue_owner_domain import owner_operations
            from .queue_pause_proof import reserve_once
            perform=owner_operations(database=p.db,release_lease=lambda:release_lease(p,uid=uid),
                                     identity=identity)

            def handle(action,path,*,stage,proof,**args):
                return perform(action,args,authorize=lambda db:reserve_once(
                    db,stage=stage,expected_revision=args['expected_revision'],proof=proof,path=path))

            def route(path,action,read_action=None):
                return make_queue_pause_rest(stage='test',enabled=True,read_sha256=read_fp,
                    write_sha256=write_fp,actor_sha256=owner,
                    verify_owner=make_owner_verifier(bytes.fromhex(key),owner,lane='test',path=path),
                    prepare=lambda:perform(read_action,{}),
                    change=lambda **args:handle(action,path,**args),port=port,path=path)

            if config.processing_session_ids==():
                handler.queue_session_route=route(sessions.PATH,'sessions_set','sessions_prepare')
                from .mcp_server import WorkerLauncher
                handler.queue_session_lifespan=lambda:sessions.follow_settings(
                    config,lambda selected:WorkerLauncher(selected).start() if ready() else {})
            handler.queue_item_routes={
                items.PATH:route(items.PATH,'item_remove'),
                items.PREVIEW_PATH:route(items.PREVIEW_PATH,'item_preview')}
        return handler
    return bind


def configured_queue_pause(config_path, port, legacy_token):
    try:
        return _configure(config_path,port,legacy_token)
    except (OSError,ValueError,UnicodeError):
        logging.getLogger(__name__).warning('queue_pause_rest_unavailable')
        return None

