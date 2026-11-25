"""Minimal lane-local metadata, never a copy of daemon config or credentials."""
from __future__ import annotations
from contextlib import ExitStack, contextmanager
import fcntl
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
import stat
import tempfile

RUN = Path('/run/assertion-memory-dashboard')
TAG = re.compile(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]{1,30})?\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
LIMIT = 32 * 1024
KEYS = frozenset(('stage','release_version','generation','device','inode','processing_session_ids',
                  'auto_process_fresh_mac','model_configured','interactive_enabled'))


def release_generation(revision, archive, tag, stage):
    data={'revision':revision,'archive':archive,'tag':tag,'lane':stage}
    return sha256(json.dumps(data,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode('ascii')).hexdigest()


def projection_path(stage):
    if stage not in ('test','prod'):
        raise ValueError('Invalid dashboard scope')
    return RUN/stage/'scheduler.json'


def check_directory(path, uid):
    if path.resolve(strict=True) != path:
        raise ValueError('Untrusted dashboard directory')
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid
            or stat.S_IMODE(info.st_mode) & 0o777 != 0o750):
        raise ValueError('Untrusted dashboard directory')
    return info.st_gid


def _validate(data, stage, database):
    if (type(data) is not dict or set(data)!=KEYS or data['stage'] != stage
            or type(data['release_version']) is not str or not TAG.fullmatch(data['release_version'])
            or type(data['generation']) is not str or not HEX.fullmatch(data['generation'])
            or any(type(data[k]) is not bool for k in (
                'auto_process_fresh_mac','model_configured','interactive_enabled'))
            or any(type(data[k]) is not int or data[k]<0 for k in ('device','inode'))):
        raise ValueError('Invalid scheduler projection')
    selectors = data['processing_session_ids']
    if selectors is not None and (type(selectors) is not list or len(selectors)>256
            or any(type(v) is not str or not 0<len(v)<=256 for v in selectors)):
        raise ValueError('Invalid scheduler selectors')
    if data['auto_process_fresh_mac'] and (stage!='prod' or selectors != []):
        raise ValueError('Invalid fresh Mac scheduler selector')
    if database.resolve(strict=True)!=database:
        raise ValueError('Noncanonical dashboard database')
    info = database.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1
            or (info.st_dev,info.st_ino)!=(data['device'],data['inode'])):
        raise ValueError('Dashboard scheduler database identity changed')


@contextmanager
def publish(*, stage, config, release, generation, interactive_enabled):
    """A running Base owns the exclusive projection lease until lifespan exit.

    Never creates a directory; provisioning must grant only the isolated
    projection path. Optional failures leave other Base operations running.
    """
    path = projection_path(stage)
    with ExitStack() as stack:
        outcome = False
        if not os.path.lexists(path.parent):
            yield False
            return
        try:
            group = check_directory(path.parent,os.getuid())
            lease = os.open(path.parent/'publisher.lock',
                os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CREAT,0o600)
            stack.callback(os.close,lease)
            f = os.fstat(lease)
            if (not stat.S_ISREG(f.st_mode) or f.st_uid!=os.getuid() or f.st_nlink!=1
                    or stat.S_IMODE(f.st_mode)!=0o600 or f.st_size!=0):
                raise ValueError('Invalid projection publisher lease')
            fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            database=Path(config.db)
            identity=database.lstat()
            data=dict(stage=stage,release_version=release,generation=generation,
                      device=identity.st_dev,inode=identity.st_ino,
                      processing_session_ids=(list(config.processing_session_ids)
                        if config.processing_session_ids is not None else None),
                      auto_process_fresh_mac=config.auto_process_fresh_mac,
                      model_configured=config.provider!='none',interactive_enabled=interactive_enabled)
            _validate(data,stage,database)
            raw=json.dumps(data,ensure_ascii=True,separators=(',',':')).encode('ascii')
            if len(raw)>LIMIT:
                raise ValueError('Oversize scheduler projection')
            fd,tmp=tempfile.mkstemp(prefix='.scheduler-',dir=path.parent)
            stack.callback(os.close,fd)
            try:
                os.fchown(fd,-1,group)
                os.fchmod(fd,0o640)
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with os.fdopen(os.dup(fd),'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp,path)
            finally:
                if os.path.lexists(tmp):
                    os.unlink(tmp)
            outcome = True
        except (OSError, ValueError):
            logging.getLogger(__name__).warning('dashboard_projection_unavailable lane=%s',stage)
        yield outcome


def read(*, stage, database, uid):
    """The graph service needs read/traverse only on this projection directory."""
    path = projection_path(stage)
    group = check_directory(path.parent,uid)
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_gid!=group
                or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o640):
            raise ValueError('Invalid scheduler projection ownership')
        def held():
            try:
                fcntl.flock(stream,fcntl.LOCK_SH|fcntl.LOCK_NB)
            except BlockingIOError:
                return
            raise ValueError('No live scheduler publisher')
        held()
        raw=stream.read(LIMIT+1)
        held()
        if not os.path.samestat(info,path.lstat()):
            raise ValueError('Scheduler projection changed')
    if len(raw)>LIMIT:
        raise ValueError('Oversize scheduler projection')
    result=json.loads(raw)
    _validate(result,stage,database)
    return result
