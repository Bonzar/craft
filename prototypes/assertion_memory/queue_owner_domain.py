"""Shared queue owner operations behind separately validated transports."""
from contextlib import closing, ExitStack
from datetime import datetime,timezone
from pathlib import Path
import sqlite3

from . import queue_item_removal as items, queue_session_control as sessions
from .processing_control import Conflict
from .queue_rest import exact_database


def owner_operations(*, database, release_lease, identity, stage='test', fresh_only=False, baseline=None):
    if stage not in ('test','prod') or fresh_only and stage!='prod':
        raise ValueError('Invalid queue owner scope')
    path=Path(database)
    expected=identity()

    def perform(action,args,authorize=None):
        writing=action not in ('sessions_prepare','policy_prepare','clear_prepare')
        from . import database_clear
        with ExitStack() as leases:
            leases.enter_context(release_lease())
            if action=='clear_commit':
                leases.enter_context(database_clear.database_lease(path,exclusive=True))
                leases.enter_context(database_clear.database_lease(path,exclusive=True,worker=True))
            before=exact_database(path)
            if identity()!=expected:
                raise Conflict('Deployment release changed')
            with closing(sqlite3.connect(path.as_uri()+('?mode=rw' if writing else '?mode=ro'),
                                        uri=True,isolation_level=None,timeout=2)) as db:
                db.execute('PRAGMA foreign_keys=ON')
                if not writing:
                    db.execute('PRAGMA query_only=ON')
                if action=='clear_commit':
                    db.execute('PRAGMA secure_delete=ON')
                db.execute('BEGIN IMMEDIATE' if writing else 'BEGIN')
                try:
                    if identity()!=expected or exact_database(path)!=before:
                        raise Conflict('Queue database changed')
                    if authorize is not None:
                        authorize(db)
                    if action=='sessions_prepare':
                        result=sessions.status(db,stage=stage,fresh_only=fresh_only)
                    elif action=='sessions_set':
                        result=sessions.update(db,stage=stage,fresh_only=fresh_only,**args)
                    elif action in ('policy_prepare','policy_set'):
                        from . import queue_processing_policy as policy
                        run=policy.describe if action=='policy_prepare' else policy.update
                        result=run(db,stage=stage,baseline=baseline,fresh_only=fresh_only,**args)
                    elif action=='clear_prepare':
                        result=database_clear.describe(db,stage=stage,binding=[before,expected])
                    elif action=='clear_commit':
                        result=database_clear.clear(db,stage=stage,binding=[before,expected],**args)
                    elif action=='item_remove':
                        result=items.remove(db,stage=stage,**args,
                            now=datetime.now(timezone.utc).isoformat(timespec='microseconds'))
                    elif action=='item_preview':
                        result=items.source_preview(db,stage=stage,**args)
                    else:
                        raise ValueError('Unknown queue action')
                    if identity()!=expected or exact_database(path)!=before:
                        raise Conflict('Queue database changed')
                    if writing:
                        db.commit()
                    if action=='clear_commit':
                        # Best effort truncation; the reset is already durable,
                        # and the response may not misrepresent a WAL reader as rollback.
                        try:
                            db.execute('PRAGMA busy_timeout=0')
                            db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
                        except sqlite3.Error:
                            pass
                    return result
                finally:
                    db.rollback()
    return perform
