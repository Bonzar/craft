"""Exact one-time PROD baseline policy; does not grant or invoke root."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import stat
import tempfile
from runtime_switch import move, sync_directory

from runtime_switch import DIGEST

USED='.legacy-bootstrap-used'
SHA=re.compile(r'[a-f0-9]{40}')


@dataclass(frozen=True)
class Bootstrap:
    legacy_static: str
    legacy_runtime: str
    legacy_entry_sha: str
    new_revision: str
    new_static_sha: str
    new_runtime_sha: str
    health: object
    legacy_source: Path | None = None

    def __post_init__(self):
        if (not all(type(x) is str and DIGEST.fullmatch(x) for x in
             (self.legacy_static,self.legacy_runtime,self.legacy_entry_sha,self.new_static_sha,self.new_runtime_sha))
             or not isinstance(self.new_revision,str) or not SHA.fullmatch(self.new_revision)
             or self.new_runtime_sha==self.legacy_runtime or not callable(self.health)):
            raise ValueError('Invalid exact PROD bootstrap policy')

    @property
    def before(self):
        return {'static':'releases/'+self.legacy_static,'runtime':'versions/'+self.legacy_runtime}

    def authorize(self, expected, runtime_sha):
        if (expected['revision'],expected['static_sha'],runtime_sha)!=(
                self.new_revision,self.new_static_sha,self.new_runtime_sha):
            raise ValueError('Signed release is not approved for this bootstrapping operation')

    def installed_entry(self):
        if self.legacy_source is None:
            raise ValueError('Missing installed legacy Visual source')
        with os.fdopen(os.open(self.legacy_source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as src:
            info=os.fstat(src.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022
                    or info.st_nlink!=1 or not 0<info.st_size<=64_000):
                raise ValueError('Untrusted installed legacy Visual source')
            raw=src.read(64_001)
        if sha256(raw).hexdigest()!=self.legacy_entry_sha:
            raise ValueError('Installed legacy Visual source is not approved')
        return raw

    def seed_old_entry(self, runtime):
        if self.legacy_source is None or self.legacy_runtime!=self.legacy_entry_sha:
            raise ValueError('No exact legacy Visual seed available')
        versions=runtime/'versions'
        if versions.resolve(strict=True)!=versions:
            raise ValueError('Untrusted bootstrap versions root')
        destination=versions/self.legacy_runtime
        if not os.path.lexists(destination):
            raw=self.installed_entry()
            with tempfile.TemporaryDirectory(dir=versions,prefix='.legacy-seed-') as directory:
                stage=Path(directory)/'seed'
                stage.mkdir(mode=0o750)
                with (stage/'asset-origin.py').open('xb') as file:
                    os.fchmod(file.fileno(),0o640)
                    file.write(raw)
                    file.flush()
                    os.fsync(file.fileno())
                sync_directory(stage)
                os.rename(stage,destination)
                sync_directory(versions)
        if destination.resolve(strict=True)!=destination:
            raise ValueError('Untrusted expected legacy seed path')
        self.old_verified(runtime,self.before)
        move(runtime,self.before['runtime'])

    def old_verified(self, runtime, pair):
        if pair!=self.before:
            raise ValueError('Expected exact pinned legacy PROD visual pair')
        file=runtime/pair['runtime']/'asset-origin.py'
        with os.fdopen(os.open(file,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as src:
            info=os.fstat(src.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=64_000:
                raise ValueError('Untrusted baseline runtime entry')
            if sha256(src.read(64_001)).hexdigest()!=self.legacy_entry_sha:
                raise ValueError('Legacy runtime entry hash does not match approved baseline')
        return pair


def read_policy(path, health, *, source):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as src:
        info=os.fstat(src.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022
                or info.st_nlink!=1 or not 0<info.st_size<=4096):
            raise ValueError('Untrusted PROD bootstrap authority')
        def unique(pairs):
            result=dict(pairs)
            if len(result)!=len(pairs):
                raise ValueError('Ambiguous PROD bootstrap authority')
            return result
        fields=json.loads(src.read(4097),object_pairs_hook=unique)
    result=Bootstrap(health=health,legacy_source=source,**fields)
    result.installed_entry()
    return result
