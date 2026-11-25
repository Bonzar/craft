"""Read-only infrastructure for the hosted owner Visual status route."""
import fcntl
import json
import os
from contextlib import closing, contextmanager
from hashlib import sha256
from pathlib import Path
import sqlite3
import stat

from admin_operations import AdminError, OwnerContext, fields
from visual_admin_service import make_visual_admin, HEX


def configured(config_dir, root, *, read_regular, reserved):
    """No fallbacks or environment guesses; keep the optional route dark."""
    path = config_dir / 'visual-status.json'
    if not os.path.lexists(path):
        return None
    data = json.loads(read_regular(path, 4096, owner=0))
    fields(data, ('owner_hash', 'token_file'))
    owner_hash = data['owner_hash']
    token_file = root / 'state' / 'visual-status-token'
    if (type(owner_hash) is not str or not HEX.fullmatch(owner_hash)
            or data['token_file'] != str(token_file)):
        raise ValueError('Invalid visual status configuration')
    token = read_regular(token_file, 130, owner=os.getuid(), private=True).decode('ascii').strip()
    if not HEX.fullmatch(token) or sha256(token.encode('ascii')).hexdigest() in reserved:
        raise ValueError('Visual status credential must be distinct')
    return owner_hash, token


@contextmanager
def snapshot_fence(path):
    """Exactly the shared Base release lease, never create or change the file."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                st.st_mode & 0o077 or st.st_nlink != 1):
            raise ValueError('Untrusted release lease')
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        if not os.path.samestat(st, os.stat(path, follow_symlinks=False)):
            raise ValueError('Changed release lease')
        yield
    finally:
        os.close(fd)


def snapshot(db, **settings):
    from prototypes.assertion_memory.queue_status import build_queue_status

    path = Path(db)
    if path.resolve(strict=True) != path:
        raise ValueError('Wrong database')
    entry = path.lstat()
    if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1:
        raise ValueError('Wrong database')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True,
                                 isolation_level=None, timeout=1)) as connection:
        if not os.path.samestat(entry, path.lstat()):
            raise ValueError('Changed database')
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        try:
            return build_queue_status(connection, **settings)
        finally:
            connection.rollback()


def mount_visual_status(*, config, stage, owner_hash, token, deploy_lock,
                        live_identity, interactive_enabled):
    def live_context():
        tag, generation = live_identity()
        return OwnerContext(owner_hash, stage, tag, generation)

    def read_status(context, *, limit, offset):
        return snapshot(config.db, release_version=context.release, stage=stage,
                        processing_session_ids=config.processing_session_ids,
                        model_configured=config.provider != 'none',
                        auto_process_fresh_mac=config.auto_process_fresh_mac,
                        interactive_enabled=interactive_enabled, limit=limit, offset=offset)

    def activation_guard(action):
        if action != 'status':
            raise AdminError('admin_unavailable')
        return snapshot_fence(deploy_lock)

    return make_visual_admin(stage=stage, owner_hash=owner_hash, token=token,
                             live_context=live_context, read_status=read_status,
                             operations=None, activation_guard=activation_guard)
