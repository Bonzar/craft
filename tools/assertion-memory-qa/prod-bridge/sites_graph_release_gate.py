"""Narrow candidate callback for the existing root PROD transition controller.

No direct CLI or installation. Host authorization and rollback stay in the
reviewed production controller. Never import release-owned code as root.
"""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import time

ROOT = Path('/srv/assertion-release/production')
DB = Path('/var/lib/assertion-memory/memory.sqlite3')
UNIT = 'assertion-memory-prod-site-graph.service'
ACTOR = 'assertion-production-deploy'
WRAPPER = Path('/opt/assertion-memory-prod-site-graph/start.py')
FENCE = Path('/run/assertion-base-prod-transition/fence.lock')
ARCHIVE = re.compile(r'([a-f0-9]{40})-([a-f0-9]{64})\Z')
TAG = re.compile(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]{1,30})?\Z')
FILES = (
    ('BASE_RELEASE_TAG', 128),
    ('REVISION', 128),
    ('apps/graph_viewer/server/app.py', 100_000),
    ('apps/graph_viewer/server/queue_dashboard.py', 100_000),
    ('prototypes/assertion_memory/dashboard_projection.py', 100_000),
)


@dataclass(frozen=True)
class ProductionGraphIdentity:
    tag: str
    generation: str
    release: Path
    db_device: int
    db_inode: int


def drop_in():
    """Only nonsecret source binding; preserves ExecStart, guard and auth."""
    return ('[Service]\n'
            'Environment=PYTHONPATH=/srv/assertion-release/production/current\n'
            'Environment=ASSERTION_MEMORY_QUEUE_DASHBOARD_STAGE=prod\n'
            'Environment=ASSERTION_MEMORY_QUEUE_DASHBOARD_PROJECTION=/run/assertion-memory-dashboard/prod/scheduler.json\n')


def _read(path, limit):
    if path.resolve(strict=True)!=path:
        raise ValueError('Noncanonical release source')
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK),'rb') as stream:
        mode=os.fstat(stream.fileno())
        if not stat.S_ISREG(mode.st_mode) or mode.st_nlink!=1 or mode.st_mode&0o022:
            raise ValueError('Untrusted release source')
        data=stream.read(limit+1)
    if len(data)>limit:
        raise ValueError('Oversize release source')
    return data


def identity(release, *, root=ROOT, db=DB, require_current=False):
    """Compare the authorized candidate/rollback bytes without executing them."""
    release=Path(release)
    match=ARCHIVE.fullmatch(release.name)
    if (match is None or root.resolve(strict=True)!=root or (root/'releases').is_symlink()
            or release.resolve(strict=True)!=release or release.parent!=root/'releases'
            or require_current and (root/'current').resolve(strict=True)!=release):
        raise ValueError('Graph release not the exact scoped Base artifact')
    revision,archive=match.groups()
    manifest=_read(release/'SHA256SUMS',5*1024*1024).decode('ascii').splitlines()
    files={}
    for path,size in FILES:
        data=_read(release/path,size)
        if manifest.count(sha256(data).hexdigest()+'  ./'+path)!=1:
            raise ValueError('Graph release source checksum mismatch')
        files[path]=data
    tag=files['BASE_RELEASE_TAG'].decode('ascii').strip()
    if not TAG.fullmatch(tag) or files['REVISION'].decode('ascii').strip()!=revision:
        raise ValueError('Graph version does not match the authorized release')
    if db.resolve(strict=True)!=db:
        raise ValueError('Noncanonical PROD database')
    stat_db=db.lstat()
    if not stat.S_ISREG(stat_db.st_mode) or stat_db.st_nlink!=1:
        raise ValueError('Unexpected PROD database identity')
    fields=dict(revision=revision,archive=archive,tag=tag,lane='prod')
    gen=sha256(json.dumps(fields,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode('ascii')).hexdigest()
    return ProductionGraphIdentity(tag,gen,release,stat_db.st_dev,stat_db.st_ino)


def matches_status(result, expected):
    """Accept a narrow projection from only the existing scoped Graph ingress."""
    if type(result) is not dict:
        return False
    control=result.get('processing_control',{})
    return (type(control) is dict and result.get('stage')=='prod' and
            result.get('release_version')==expected.tag and
            result.get('release_generation')==expected.generation and
            result.get('global_order') is False and
            type(result.get('queues')) is list and
            len(result['queues'])==3 and all(type(q) is dict for q in result['queues']) and
            [q.get('id') for q in result['queues']]==['source','maintenance','interactive'] and
            control.get('can_change',False) is False)


class SitesGraphReleaseGate:
    """Trusted callbacks must be supplied by the existing root transition only.

    serialized() owns the existing rollout transaction, *not* an exclusive
    dashboard fence. settled(expected) independently checks root verified
    release/inode and that the dashboard fence has been released. verify_unit()
    checks fixed scope, root permissions and existing protection/ExecCondition;
    verify_import() verifies candidate dependencies as the serving UID (never
    imports candidate under the root controller).
    """
    def __init__(self, *, serialized, settled, verify_unit, verify_import, restart,
                 runtime_identity, own_gateway_probe, sleep=time.sleep, read_identity=identity):
        self.serialized=serialized
        self.settled=settled
        self.verify_unit=verify_unit
        self.verify_import=verify_import
        self.restart=restart
        self.runtime_identity=runtime_identity
        self.own_gateway_probe=own_gateway_probe
        self.sleep=sleep
        self.read_identity=read_identity

    def prepare(self, candidate, rollback):
        """Reversible pre-activation; no replace, restart, DB open or SQL."""
        latest=self.read_identity(candidate)
        old=self.read_identity(rollback,require_current=True)
        if (latest.db_device,latest.db_inode)!=(old.db_device,old.db_inode):
            raise ValueError('PROD graph storage identity changed')
        if self.verify_unit(UNIT,ACTOR,WRAPPER,drop_in()) is not True:
            raise ValueError('Existing PROD graph unit not verified')
        if self.verify_import(latest,ACTOR) is not True or self.verify_import(old,ACTOR) is not True:
            raise ValueError('Candidate or rollback Graph runtime unsupported')
        return latest,old

    def after_settle(self, expected):
        """Only the reviewed release/rollback transaction may call this."""
        if type(expected) is not ProductionGraphIdentity:
            raise ValueError('Expected checked PROD Graph identity')
        with self.serialized():
            if (self.read_identity(expected.release,require_current=True)!=expected or
                    self.settled(expected) is not True or
                    self.verify_unit(UNIT,ACTOR,WRAPPER,drop_in()) is not True):
                raise ValueError('PROD transition not cleanly settled')
            if self.restart(UNIT) is not True:
                raise ValueError('Exact PROD Graph restart not confirmed')
            for _ in range(5):
                try:
                    if (self.runtime_identity(UNIT,ACTOR,expected) is True and
                            matches_status(self.own_gateway_probe(),expected) and
                            self.read_identity(expected.release,require_current=True)==expected and
                            self.settled(expected) is True):
                        return True
                except (OSError,ValueError):
                    # The existing ingress may be warming; do not print
                    # HTTP bodies, upstream errors, argv, or credentials.
                    pass
                self.sleep(1)
        # Existing controller owns rollback, using the checked old identity and
        # the same callback once it has settled that old release.
        raise ValueError('PROD Sites Graph not verified on the activated release')
