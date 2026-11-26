"""Shared queue owner operations behind separately validated transports."""
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import sqlite3

from . import queue_item_removal as items, queue_session_control as sessions
from .processing_control import Conflict
from .queue_rest import exact_database


def owner_operations(*, database, release_lease, identity, stage='test', fresh_only=False):
    if stage not in ('test','prod') or fresh_only and stage!='prod':
        raise ValueError('Invalid queue owner scope')
    path=Path(database)
    expected=identity()

    def perform(action,args,authorize=None):
        writing=action!='sessions_prepare'
        with release_lease():
            before=exact_database(path)
            if identity()!=expected:
                raise Conflict('Deployment release changed')
            with closing(sqlite3.connect(path.as_uri()+('?mode=rw' if writing else '?mode=ro'),
                                        uri=True,isolation_level=None,timeout=2)) as db:
                db.execute('PRAGMA foreign_keys=ON')
                if not writing:
                    db.execute('PRAGMA query_only=ON')
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
                    return result
                finally:
                    db.rollback()
    return perform
