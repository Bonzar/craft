"""Read-only SQLite source; a persistent connection detects WAL/view updates."""
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from urllib.parse import quote


class AssertionMemorySource:
    def __init__(self, path, provider_id='assertion-memory'):
        self.path = Path(path).resolve(strict=True)
        self.provider_id = provider_id
        self._watch = self.connect(check_same_thread=False)
        stat=self.path.stat(); self._identity=(stat.st_dev,stat.st_ino)
        self._lock = threading.Lock()
        self._last_data_version = None
        self._last_revision = None

    def connect(self, **kwargs):
        uri = 'file:' + quote(str(self.path),safe='/') + '?mode=ro'
        c = sqlite3.connect(uri, uri=True, timeout=5, **kwargs)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=5000')
        return c

    def close(self):
        with self._lock:
            self._watch.close()

    def revision(self):
        with self._lock:
            stat=self.path.stat(); identity=(stat.st_dev,stat.st_ino)
            if identity!=self._identity:
                replacement=self.connect(check_same_thread=False)
                self._watch.close();self._watch=replacement
                self._identity=identity;self._last_data_version=None;self._last_revision=None
            dirty=self._watch.execute('PRAGMA data_version').fetchone()[0]
            if dirty==self._last_data_version and self._last_revision is not None:
                return self._last_revision
            # data_version is only a dirtiness hint: worker leases/heartbeats
            # cannot invalidate an immutable graph. Hash actual viewer inputs.
            from ..incremental import watermarks,row_watermark
            self._watch.execute('BEGIN')
            try:
                marks=watermarks(self._watch)
                tables={row[0] for row in self._watch.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in ('memory_extraction_runs','memory_event_positions'):
                    marks[table]=row_watermark(self._watch,table) if table in tables else None
                marks['raw_count']=self._watch.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0] if 'memory_raw_events' in tables else 0
                causal=self._watch.execute('SELECT coalesce(max(id),0) FROM memory_run_events').fetchone()[0] if 'memory_run_events' in tables else 0
                marks['causal']=causal
                encoded=json.dumps({'file':identity,'marks':marks},sort_keys=True,separators=(',',':')).encode()
                fingerprint=hashlib.sha256(encoded).hexdigest()
                revision=f"{marks['state']}:{fingerprint}:names:{marks['presentation']}:runs:{causal}"
                self._watch.commit()
            except BaseException:
                self._watch.rollback();raise
            self._last_data_version=dirty;self._last_revision=revision
            return revision

    def assertion_membership(self, connection, recorded_cutoff):
        from .base import SOURCE_MEMBERSHIP_POLICY
        exists=connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_extraction_runs'").fetchone()
        if not exists:
            return frozenset(), [SOURCE_MEMBERSHIP_POLICY, 'legacy-unrestricted']
        columns={r[1] for r in connection.execute('PRAGMA table_info(memory_extraction_runs)')}
        if not {'id','event_id','result','completed_at'} <= columns:
            raise ValueError('Invalid source interpretation ledger schema')
        # Only this provider knows the source contract. Reuse its read-only validator
        # lazily, with ownership and cutoff from the caller's already-pinned snapshot.
        from prototypes.assertion_memory.source_revision import watermark, selection
        maximum=watermark(connection)
        selected=selection(connection,recorded_cutoff,maximum)
        return selected.inactive_ids, [SOURCE_MEMBERSHIP_POLICY, maximum, selected.fingerprint]

    def has_object(self,kind,id):
        with self.connect() as c:
            if kind=='node':
                return c.execute('SELECT 1 FROM memory_assertions WHERE assertion_id=?',(id,)).fetchone() is not None
            if kind=='topic':
                from .version_identity import parse_topic_version_id
                pair=parse_topic_version_id(id)
                if pair is not None:
                    if c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_view_versions'").fetchone() is None:return False
                    return c.execute('SELECT 1 FROM memory_view_versions WHERE view_id=? AND id=?',pair).fetchone() is not None
            if kind=='topic' and id.startswith('topic:'):
                exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_views'").fetchone()
                return bool(exists and c.execute('SELECT 1 FROM memory_views WHERE id=?',(id[6:],)).fetchone())
        return False

    def capabilities(self):
        with self.connect() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        views = 'memory_views' in tables and 'memory_view_versions' in tables
        return {'provider_id':self.provider_id,'capabilities':{
            'full_graph': True,'topics':views,'topic_hierarchy':views and 'memory_question_route_versions' in tables,
            'source_context':'memory_raw_events' in tables,'whole_saved_source':'memory_raw_events' in tables,
            'keyword_search':True,'semantic_search':False,'history':True,'live':True},
            'readiness':[{'code':'semantic_unavailable','message':'Semantic index/query model not yet configured'},
                         {'code':'global_cancellation','message':'Only explicit published viewer_status cancellation is supported'}],
            'limits':{'incident_page':2000,'text_page':64000,'cancellation':'explicit_published_only'}}
