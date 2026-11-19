"""Read-only editorial names on the caller's already pinned SQLite snapshot.

The normal schema reuses the core resolver through a narrow read facade, never a
MemoryEngine/MemoryStore constructor (both initialize writable schemas). Frozen IR
and old sources have no journal. Names-only transitional stores use the same core
definition hash and temporal guards without inventing an operational work table.
"""
import json
from contextlib import contextmanager
from ..common import now, timestamp


class ReadOnlyNames:
    def __init__(self, connection):
        self.connection=connection
        self.store=self

    @contextmanager
    def transaction(self, *, write=False):
        if write:
            raise ValueError('Editorial presentation is read-only')
        # Ownership, BEGIN/COMMIT and query_only belong to the source adapter.
        yield self.connection

    def _spec(self, view_id):
        from prototypes.assertion_memory.engine import ViewSpec
        row=self.connection.execute('SELECT definition FROM memory_views WHERE id=?',(view_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown presentation view')
        return ViewSpec(**json.loads(row['definition']))


def name_watermark(connection, tables):
    if 'memory_view_names' not in tables:
        return 0
    return connection.execute('SELECT coalesce(max(id),0) FROM memory_view_names').fetchone()[0]


def capture_names(connection, tables, *, snapshot=None, captured_at=None, recorded_as_of=None, published_at=None):
    pinned=dict(snapshot) if snapshot is not None else {
        'presentation_cutoff':captured_at or now(),
        'name_watermark':name_watermark(connection,tables)}
    if type(pinned['name_watermark']) is not int or pinned['name_watermark']<0:
        raise ValueError('Name watermark must be a nonnegative integer')
    cutoffs=[timestamp(pinned['presentation_cutoff'])]
    cutoffs.extend(timestamp(value) for value in (recorded_as_of,published_at) if value is not None)
    pinned['presentation_cutoff']=min(cutoffs)
    return pinned


def fallback_name(definition, *, created_with_name=False):
    title=definition.get('title') or definition.get('query') or ''
    pending=title==definition.get('query') or created_with_name
    return {'display_title':None if pending else title,
            'naming_status':'naming_pending' if pending else 'legacy_unreviewed',
            'name_record_id':None,'name_recorded_at':None}


def resolve_name(connection, tables, view_id, definition, snapshot, *, version_id=None, allow_legacy=False):
    if 'memory_view_names' not in tables:
        return fallback_name(definition)
    engine=ReadOnlyNames(connection)
    columns={row[1] for row in connection.execute('PRAGMA table_info(memory_view_names)')}
    if 'memory_view_name_work' in tables and 'version_id' in columns:
        from prototypes.assertion_memory.view_names import presentation
        resolved,_=presentation(engine,view_id,snapshot=snapshot,version_id=version_id,allow_legacy=allow_legacy)
        return {key:resolved.get(key) for key in (
            'display_title','naming_status','name_record_id','name_recorded_at','name_version_id')}
    return transitional_name(engine,view_id,definition,snapshot)


def transitional_name(engine, view_id, definition, snapshot):
    # Legacy sources have no version binding. Reuse core's reviewed-record
    # checks, and keep the historical display fallback explicit.
    from prototypes.assertion_memory.view_names import PROTOCOL,_definition_sha,_normal_record,_prior_name
    spec=engine._spec(view_id); c=engine.connection
    row=_prior_name(c,spec,cutoff=snapshot['presentation_cutoff'],watermark=snapshot['name_watermark'])
    if row is not None:
        return {'display_title':row['title'],'naming_status':'accepted',
                'name_record_id':row['id'],'name_recorded_at':row['recorded_at']}
    sha=_definition_sha(spec)
    records=c.execute('SELECT * FROM memory_view_names WHERE view_id=? '
        'AND definition_sha256=? AND protocol=? ORDER BY id',(view_id,sha,PROTOCOL))
    first=next((name for row in records if (name:=_normal_record(row,spec.query))),None)
    created=bool(first and first['provenance'].get('input_definition_sha256')!=sha)
    return fallback_name(definition,created_with_name=created)
