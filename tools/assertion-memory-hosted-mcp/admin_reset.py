"""Offline, inode-preserving core for the supervised owner reset.

Never call this from a live DB serving process. A separate release-managed
supervisor is responsible for exclusive quiescence and restarting the services.
"""
from __future__ import annotations

import os
import sqlite3
import stat
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from admin_operations import AdminError, OwnerContext

_EMPTY = ('memory_raw_events', 'memory_assertions', 'memory_thoughts', 'memory_thought_versions',
          'memory_capture_jobs', 'memory_thought_work', 'memory_artifacts')
_LAZY = ('memory_interactive_queries',)


@dataclass(frozen=True)
class OfflineLease:
    """Only constructed by the trusted supervisor after fencing every DB service."""
    context: OwnerContext
    database: Path
    device: int
    inode: int


def _checked(path, uid, gid):
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise AdminError('reset_wrong_database')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1
                or (s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)) != (uid, gid, 0o600)):
            raise AdminError('reset_wrong_database')
        return s.st_dev, s.st_ino
    finally:
        os.close(fd)


def _sidecars(path, uid, gid):
    for suffix in ('-wal', '-shm', '-journal'):
        f = Path(str(path) + suffix)
        if os.path.lexists(f):
            _checked(f, uid, gid)


def _verify_empty(c):
    if [tuple(r) for r in c.execute('PRAGMA integrity_check')] != [('ok',)]:
        raise AdminError('reset_integrity_failed')
    if c.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise AdminError('reset_integrity_failed')
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not names.issuperset(_EMPTY):
        raise AdminError('reset_integrity_failed')
    for table in _EMPTY + _LAZY:
        if table in names and c.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() is not None:
            raise AdminError('reset_integrity_failed')


class CanonicalReset:
    def __init__(self, *, database, uid, gid, quiesce, clear_auxiliary, ready):
        # These three hooks are deployment-owned and mandatory. In particular,
        # quiesce cannot just take the SQLite writer lock: a running worker can
        # publish old results later, and a graph can cache the previous snapshot.
        if not all(callable(f) for f in (quiesce, clear_auxiliary, ready)):
            raise AdminError('reset_unavailable')
        self.database = Path(database)
        self.uid, self.gid = uid, gid
        self.quiesce = quiesce
        self.clear_auxiliary = clear_auxiliary
        self.ready = ready

    def __call__(self, context):
        from prototypes.assertion_memory.storage import MemoryStore
        from prototypes.assertion_memory.thought_service import ThoughtService
        p = self.database
        initial = _checked(p, self.uid, self.gid)
        # The only copy made is the empty *schema*, in RAM. Never clone the
        # populated database, even temporarily. This is not a hardware erase.
        with MemoryStore(':memory:') as template:
            ThoughtService(template, None)
            with self.quiesce(context) as lease:
                if (type(lease) is not OfflineLease or lease.context != context or lease.database != p
                        or initial != (lease.device, lease.inode)
                        or _checked(p, self.uid, self.gid) != initial):
                    raise AdminError('reset_lease_invalid')
                _sidecars(p, self.uid, self.gid)
                # Sidecars outside SQLite (graph projections, previous model
                # artefacts, caches) are handled by a known lane-specific plan;
                # operator/device state and the other lane are never touched.
                if self.clear_auxiliary(lease) is not True:
                    raise AdminError('reset_auxiliary_unavailable')
                end = time.monotonic() + 20

                def progress(*_):
                    if time.monotonic() >= end:
                        raise AdminError('reset_outcome_unconfirmed')

                with closing(sqlite3.connect(p.as_uri() + '?mode=rw', uri=True, timeout=2,
                                             isolation_level=None)) as target:
                    target.execute('PRAGMA synchronous=FULL')
                    with template.transaction() as fresh:
                        _verify_empty(fresh)
                        fresh.backup(target, pages=32, progress=progress, sleep=.02)
                    # Read-only graph adapters must be restarted after the
                    # writable Base process recreates any WAL/SHM sidecars.
                    if target.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
                        raise AdminError('reset_outcome_unconfirmed')
                    _verify_empty(target)
                if _checked(p, self.uid, self.gid) != initial:
                    raise AdminError('reset_outcome_unconfirmed')
        return self.ready(context, initial) is True
