"""Only self-owned derived Graph cache files, never live source data or exports."""
from hashlib import sha256
import os
from pathlib import Path
import re
import shutil
import stat

from .cache_retention import CACHE_FILES

GRAPH_FILES=CACHE_FILES|{'semantic.json.tmp','projection.sqlite3-shm','projection.sqlite3-wal'}
IR_FILES={'source.sqlite3','source.sqlite3-wal','source.sqlite3-shm'}
IR_DIR=r'[a-f0-9]{64}|\.building-[a-z0-9_]{8}'


def _private_dir(path):
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
        raise ValueError('unsafe graph derived cache scope')


def _optional_dir(path):
    try:
        _private_dir(path)
        return True
    except FileNotFoundError:
        return False


def _remove(path,allowed):
    _private_dir(path)
    for node in path.iterdir():
        f=node.lstat()
        if (node.name not in allowed or not stat.S_ISREG(f.st_mode)
                or f.st_uid!=os.geteuid() or f.st_nlink!=1 or f.st_mode&0o022):
            raise ValueError('unexpected derived cache file')
    shutil.rmtree(path)


def _scope(root,pattern,allowed,active):
    if not _optional_dir(root):
        return
    for entry in root.iterdir():
        if entry.name!=active and re.fullmatch(pattern,entry.name):
            _remove(entry,allowed)


def _epochs(root,generation_pattern,allowed,active):
    if not _optional_dir(root):
        return
    for entry in root.iterdir():
        if entry.name==active or not re.fullmatch(r'[a-f0-9]{64}',entry.name):
            continue
        _scope(entry,generation_pattern,allowed,None)
        # Refuse unexpected contents; never recursive-delete namespace roots.
        entry.rmdir()


def after_clear(cache, *, provider_id, active):
    if not re.fullmatch(r'[a-f0-9]{64}',active):
        raise ValueError('invalid active Graph epoch')
    cache=Path(cache)
    if not _optional_dir(cache.parent):
        return
    _scope(cache,r'[a-f0-9]{32}',GRAPH_FILES,None)
    _epochs(cache/'database-epochs',r'[a-f0-9]{32}',GRAPH_FILES,active)
    if not _optional_dir(cache.parent/'source-ir'):
        return
    ir=cache.parent/'source-ir'/sha256(provider_id.encode('utf-8')).hexdigest()
    _scope(ir,IR_DIR,IR_FILES,None)
    _epochs(ir/'database-epochs',IR_DIR,IR_FILES,active)
