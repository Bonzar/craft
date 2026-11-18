"""Durable coalesced scope work, independent of source jobs and model caches.

Only the frozen snapshot descriptor is persisted; canonical bodies stay in their
append-only source tables. Hydration is cached while a generation runs and is
repeated once after restart. CPU work remains a transitional full scan per
generation/recovery, rather than an end-to-end delta-only implementation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .query_snapshot import QuerySnapshot, capture, _restore_frozen, FrozenRelationFrontierChanged


class DerivedYield(BaseException):
    """Yield between provider calls without discarding durable generation work."""


@dataclass(frozen=True)
class DerivedClaim:
    scope: str
    token: str
    frontier: dict
    snapshot: QuerySnapshot
    checkpoint: dict
    refresh_fairness: bool = False


@dataclass(frozen=True)
class DerivedTask:
    """Private worker result, never represented as a captured source job."""
    scope: str
    status: str
    kind: str = 'derived_overview'
    error: str | None = None


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


class DerivedWorkQueue:
    def __init__(self, store, *, clock=None, lease_seconds=300):
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lease_seconds = lease_seconds
        self._snapshots = {}
        if lease_seconds <= 0:
            raise ValueError('lease seconds must be positive')
        self._review_trigger()

    def _time(self, after=0):
        return (self.clock()+timedelta(seconds=after)).astimezone(timezone.utc).isoformat(timespec='microseconds')

    def _review_trigger(self):
        with self.store.transaction(write=True) as connection:
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_branch_reviews'").fetchone()
            if not exists:
                return
            connection.execute("DROP TRIGGER IF EXISTS memory_derived_review_insert")
            connection.execute("""CREATE TRIGGER memory_derived_review_insert
                AFTER INSERT ON memory_branch_reviews WHEN new.decision='accepted' BEGIN
                INSERT INTO memory_derived_work(scope,desired_frontier,updated_at,available_at)
                SELECT value,json_object('generation',1,'review_watermark',new.rowid),
                       new.recorded_at,'1970-01-01T00:00:00.000000+00:00'
                FROM memory_views,json_each(memory_views.definition,'$.scopes') WHERE memory_views.id=new.view_id
                UNION SELECT scope,json_object('generation',1,'review_watermark',new.rowid),
                             new.recorded_at,'1970-01-01T00:00:00.000000+00:00'
                      FROM memory_assertions WHERE assertion_id=new.assertion_id
                ON CONFLICT(scope) DO UPDATE SET desired_frontier=json_set(desired_frontier,'$.generation',
                    coalesce(json_extract(desired_frontier,'$.generation'),0)+1,'$.review_watermark',new.rowid),
                    status=CASE WHEN status='running' THEN status ELSE 'pending' END,
                    error=NULL,error_count=0,available_at=excluded.available_at;
                END""")

    def status(self, scope=None, offset=0, limit=100):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('invalid derived status page')
        with self.store.transaction() as connection:
            rows = connection.execute("SELECT * FROM memory_derived_work WHERE (? IS NULL OR scope=?) "
                "ORDER BY scope LIMIT ? OFFSET ?", (scope,scope,limit,offset)).fetchall()
        return [self._descriptor(row) for row in rows]

    def count(self, scope=None):
        with self.store.transaction() as connection:
            return connection.execute('SELECT count(*) FROM memory_derived_work WHERE (? IS NULL OR scope=?)',
                                      (scope,scope)).fetchone()[0]

    def _descriptor(self, row):
        result = json.loads(row['result'])
        state=json.loads(row['checkpoint']).get('overview',{})
        coverage=state.get('navigation_source_coverage',{})
        early=state.get('ready_groups',{})
        groups={group['view_id'] for level in early.get('hierarchy',[]) for group in level.get('groups',[])}
        early_metadata={'state':early.get('stage','not_started'),
            'group_count':len(groups),
            'accepted_count':len(groups & early.get('accepted',{}).keys()),
            'failure_type':(early.get('failure') or {}).get('type')}
        fields = ('root_view_id','version_id','check_id','registry_id','registry_protocol','route_revision','accepted_children','semantic_coverage','manifest_id','unassigned_count',
                  'navigation_coverage','required_map_count','published_map_count')
        visible={key:result.get(key) for key in fields}
        visible['navigation_uncovered_count']=0 if result.get('navigation_coverage')=='complete' else None
        if coverage:
            # Bounded diagnostics only. Exact residual IDs remain in the saved
            # discovery registry/checkpoint, not multiplied into status pages.
            for key in ('navigation_coverage','navigation_uncovered_count','registry_id','route_revision',
                        'required_map_count','published_map_count','source_count','covered_source_count',
                        'uncovered_source_ids_sha256'):
                visible[key]=coverage.get(key)
            # The prior result's children belong to its completed frontier.
            # Current publications remain discoverable through the catalog.
            visible.pop('accepted_children',None)
        return {'scope':row['scope'],'state':row['status'],'status':row['status'],
                'desired_frontier':json.loads(row['desired_frontier']),
                'completed_frontier':json.loads(row['completed_frontier']) if row['completed_frontier'] else None,
                'current_version_id':result.get('version_id'),'current_check_id':result.get('check_id'),
                'published_theme_count':len(result.get('theme_ids') or []),
                'error':row['error'],'updated_at':row['updated_at'],'next_wake_at':row['next_wake_at'],
                'early_assemblies':early_metadata,
                **visible}

    def legacy_coverage(self, engine, scope=None):
        """Read-only completed-policy counts; no scheduling or proof promotion."""
        with self.store.transaction() as connection:
            row=connection.execute("SELECT count(*),coalesce(sum("
                "completed_frontier IS NOT NULL AND "
                "coalesce(json_extract(completed_frontier,'$.pipeline_key'),'')!=?),0), "
                "coalesce(sum(completed_frontier IS NOT NULL AND "
                "json_extract(completed_frontier,'$.pipeline_key')=?),0) "
                "FROM memory_derived_work WHERE (? IS NULL OR scope=?)",
                (engine.pipeline_key,engine.pipeline_key,scope,scope)).fetchone()
        return {'navigation_policy':engine.pipeline_key,'scope_count':row[0],
                'legacy_scope_count':row[1],'current_policy_scope_count':row[2],
                'unprocessed_scope_count':row[0]-row[1]-row[2],'policy_current':row[0]==row[2],
                'historical_reprocessing':'explicit_component_scope_range'}

    def _prepare(self, connection, engine):
        now = self._time()
        # A pipeline change invalidates proof compatibility, but is not itself
        # admission for a historical scope rebuild. Idle completed results retain
        # their old policy. Real pending generations, explicit policy_work and
        # temporal boundaries may adopt this engine without relabeling old checks.
        connection.execute("""UPDATE memory_derived_work SET desired_frontier=json_set(desired_frontier,
            '$.pipeline_key',?),status=CASE WHEN status='running' THEN status ELSE 'pending' END,
            available_at=?,error=NULL,error_count=0 WHERE coalesce(json_extract(desired_frontier,'$.pipeline_key'),'') != ?
            AND (status!='idle' OR next_wake_at<=?)""",
            (engine.pipeline_key,now,engine.pipeline_key,now))
        connection.execute("""UPDATE memory_derived_work SET desired_frontier=json_set(desired_frontier,
            '$.temporal_at',next_wake_at), status=CASE WHEN status='running' THEN status ELSE 'pending' END,
            available_at=?,next_wake_at=NULL,error_count=0 WHERE next_wake_at <= ?""", (now,now))

    def claim(self, engine, *, with_model, refresh_fairness=False):
        with self.store.transaction(write=True) as connection:
            self._prepare(connection,engine)
            now = self._time()
            if not with_model:
                connection.execute("UPDATE memory_derived_work SET status='waiting_model' "
                    "WHERE status IN ('pending','waiting_model') OR (status='running' AND lease_until<=?)",(now,))
                return None
            row = connection.execute("""SELECT * FROM memory_derived_work
                WHERE ((status IN ('pending','waiting_model') OR (status='failed' AND error_count<3
                        AND coalesce(json_extract(checkpoint,'$.overview.failure.terminal_discovery'),0)=0)) AND available_at<=?)
                   OR (status='running' AND lease_until<=?) ORDER BY turn,updated_at,scope LIMIT 1""",(now,now)).fetchone()
            if row is None:
                return None
            checkpoint = json.loads(row['checkpoint'])
            frozen = checkpoint.get('_scheduler')
            prior_run = frozen.get('provenance_run_id') if isinstance(frozen,dict) else None
            if frozen:
                owner = frozen.get('frontier') if isinstance(frozen,dict) else None
                if not isinstance(owner,dict) or owner.get('pipeline_key') != engine.pipeline_key:
                    # Only claimable work reaches here. Retire unpublished old
                    # protocol progress before strict raw-snapshot hydration;
                    # accepted results/history and active leases remain intact.
                    checkpoint, frozen = {}, None
                    connection.execute("UPDATE memory_derived_work SET managed_ids='[]' WHERE scope=?",(row['scope'],))
            if (frozen and checkpoint.get('overview', {}).get('failure', {}).get('terminal_discovery')
                    and frozen['frontier'] != json.loads(row['desired_frontier'])):
                # The old generation is conclusively incomplete. New corpus or
                # protocol gets its own frozen attempt, without completing old work.
                checkpoint, frozen = {}, None
            if frozen:
                # Compact any pre-fix checkpoint when first claimed again.
                descriptor = {key:value for key,value in frozen['snapshot'].items()
                              if key not in ('assertions','edges','reviews')}
                frozen['snapshot'] = descriptor
                try:
                    snapshot = self._frozen_snapshot(engine,row['scope'],descriptor)
                except FrozenRelationFrontierChanged:
                    # A mutable proposal receipt cannot reconstruct this unfinished
                    # generation. Retain published history; start a fresh snapshot.
                    checkpoint, frozen = {}, None
                    connection.execute("UPDATE memory_derived_work SET managed_ids='[]' WHERE scope=?",(row['scope'],))
                else:
                    frontier = frozen['frontier']
            if not frozen:
                snapshot = capture(engine,captured_at=now)
                frontier = json.loads(row['desired_frontier'])
                checkpoint = {'_scheduler':{'snapshot':snapshot.descriptor(),'frontier':frontier}}
                self._snapshots[row['scope']] = (_json(snapshot.descriptor()),snapshot)
            from .run_provenance import begin_derived, outcome
            if 'provenance_run_id' not in checkpoint['_scheduler']:
                run_id = begin_derived(connection,row['scope'],frontier,
                    json.loads(row['completed_frontier']) if row['completed_frontier'] else None,
                    snapshot.descriptor(),now=now)
                checkpoint['_scheduler']['provenance_run_id'] = run_id
                if prior_run:outcome(connection,prior_run,'superseded',recorded_at=now)
            else:
                outcome(connection,checkpoint['_scheduler']['provenance_run_id'],'resumed',recorded_at=now)
            token = uuid4().hex
            connection.execute("""UPDATE memory_derived_work SET status='running',lease_token=?,lease_until=?,
                checkpoint=?,updated_at=?,error=NULL,turn=(SELECT coalesce(max(turn),0)+1 FROM memory_derived_work)
                WHERE scope=?""",
                (token,self._time(self.lease_seconds),_json(checkpoint),now,row['scope']))
            return DerivedClaim(row['scope'],token,frontier,snapshot,checkpoint,refresh_fairness)

    def recover_abandoned(self):
        """Only call after acquiring the supervisor's exclusive database flock.

        That lock proves the previous automatic worker exited. Recovery may then
        revoke its token immediately rather than waiting out a provider lease.
        """
        with self.store.transaction(write=True) as connection:
            connection.execute("UPDATE memory_derived_work SET lease_until=?,lease_token=NULL "
                               "WHERE status='running'",(self._time(),))

    def _frozen_snapshot(self, engine, scope, descriptor):
        key = _json(descriptor)
        cached = self._snapshots.get(scope)
        if cached is None or cached[0] != key:
            cached = self._snapshots[scope] = (key,_restore_frozen(engine,descriptor))
        return cached[1]

    def _owned(self, connection, claim):
        if not connection.execute('SELECT 1 FROM memory_derived_work WHERE scope=? AND lease_token=? AND lease_until>?',
                                  (claim.scope,claim.token,self._time())).fetchone():
            raise RuntimeError('derived work lease expired')

    def renew(self, claim):
        with self.store.transaction(write=True) as connection:
            self._owned(connection,claim)
            connection.execute('UPDATE memory_derived_work SET lease_until=? WHERE scope=?',
                               (self._time(self.lease_seconds),claim.scope))

    def save_checkpoint(self, claim, checkpoint):
        with self.store.transaction(write=True) as connection:
            self._owned(connection,claim)
            previous = json.loads(connection.execute('SELECT checkpoint FROM memory_derived_work WHERE scope=?',
                                                     (claim.scope,)).fetchone()[0])
            checkpoint = {**checkpoint,'_scheduler':previous['_scheduler']}
            connection.execute('UPDATE memory_derived_work SET checkpoint=?,managed_ids=?,lease_until=? WHERE scope=?',
                               (_json(checkpoint),_json(self._managed_ids(checkpoint)),
                                self._time(self.lease_seconds),claim.scope))

    @staticmethod
    def _managed_ids(checkpoint):
        state = checkpoint.get('overview', {})
        discovery = state.get('discovery', {})
        keys = set(state.get('accepted', {}))
        keys.update(discovery.get('leaf_ids', []))
        keys.update(discovery.get('definitions', []))
        keys.update(state.get(name) for name in ('building_view_id',))
        return sorted(key for key in keys if isinstance(key,str) and key)

    def pending_view_ids(self):
        # This small registry deliberately lives outside the frozen source-body
        # checkpoint, avoiding a full JSON corpus parse for every source refresh.
        with self.store.transaction() as connection:
            rows = connection.execute("SELECT managed_ids FROM memory_derived_work WHERE managed_ids != '[]'").fetchall()
        return {key for row in rows for key in json.loads(row[0])}

    def save_response(self, claim, key, response):
        with self.store.transaction(write=True) as connection:
            self._owned(connection,claim)
            connection.execute('INSERT OR IGNORE INTO memory_refresh_generations VALUES (?,?)',(key,response))

    def release(self, claim, *, error=None, waiting_model=False):
        with self.store.transaction(write=True) as connection:
            self._owned(connection,claim)
            terminal = bool(getattr(error, 'terminal_discovery', False))
            state = 'waiting_model' if waiting_model else ('failed' if error else 'pending')
            if terminal:
                desired = json.loads(connection.execute('SELECT desired_frontier FROM memory_derived_work WHERE scope=?',
                                                        (claim.scope,)).fetchone()[0])
                if desired != claim.frontier:
                    state = 'pending'
            connection.execute("""UPDATE memory_derived_work SET status=?,lease_token=NULL,lease_until=NULL,
                error=?,available_at=?,updated_at=?,error_count=error_count+? WHERE scope=?""",
                (state,str(error)[:1000] if error else None,self._time(2 if error and state == 'failed' and not terminal else 0),
                 self._time(),1 if error else 0,claim.scope))
            from .run_provenance import outcome
            run_id = claim.checkpoint.get('_scheduler',{}).get('provenance_run_id')
            if run_id:outcome(connection,run_id,'waiting_model' if waiting_model else 'failed' if error else 'yielded',recorded_at=self._time())
            return DerivedTask(claim.scope,state,error=str(error)[:1000] if error else None)

    def recapture_source_revision(self,claim,error):
        """Retire only unpublishable work; preserve all published versions/checks."""
        from .overview_source_proof import StaleSourceRevision
        from .source_revision import watermark
        if not isinstance(error,StaleSourceRevision):raise ValueError('source drift proof required')
        with self.store.transaction(write=True) as connection:
            self._owned(connection,claim)
            desired=json.loads(connection.execute('SELECT desired_frontier FROM memory_derived_work WHERE scope=?',
                (claim.scope,)).fetchone()[0])
            desired.update(generation=desired.get('generation',0)+1,
                source_revision_watermark=watermark(connection),
                source_context_advanced_to=error.current_revision)
            connection.execute("UPDATE memory_derived_work SET desired_frontier=?,status='pending',"
                "checkpoint='{}',managed_ids='[]',lease_token=NULL,lease_until=NULL,error=NULL,error_count=0,"
                "updated_at=?,available_at=? WHERE scope=?",(_json(desired),self._time(),self._time(),claim.scope))
            from .run_provenance import outcome
            run_id = claim.checkpoint.get('_scheduler',{}).get('provenance_run_id')
            if run_id:outcome(connection,run_id,'superseded',recorded_at=self._time())
            self._snapshots.pop(claim.scope,None)
            return DerivedTask(claim.scope,'pending')

    def finish(self, claim, result):
        with self.store.transaction(write=True) as connection:
            self._owned(connection,claim)
            desired = json.loads(connection.execute('SELECT desired_frontier FROM memory_derived_work WHERE scope=?',
                                                    (claim.scope,)).fetchone()[0])
            state = 'idle' if desired == claim.frontier else 'pending'
            wake = self._next_boundary(connection,claim.scope)
            connection.execute("""UPDATE memory_derived_work SET completed_frontier=?,status=?,checkpoint='{}',managed_ids='[]',
                result=?,lease_token=NULL,lease_until=NULL,error=NULL,error_count=0,updated_at=?,available_at=?,next_wake_at=?
                WHERE scope=?""",(_json(claim.frontier),state,_json(result),self._time(),self._time(),wake,claim.scope))
            from .run_provenance import outcome
            run_id = claim.checkpoint.get('_scheduler',{}).get('provenance_run_id')
            if run_id:outcome(connection,run_id,'completed',recorded_at=self._time())
            self._snapshots.pop(claim.scope,None)
            return DerivedTask(claim.scope,state)

    def _next_boundary(self, connection, scope):
        # Conservatively wake the entire connected evidence component, including
        # a future prerequisite outside the root's own scope. Scheduling confers
        # no retrieval permission; the builder still applies normal authorization.
        return connection.execute("""WITH RECURSIVE related(id) AS (
            SELECT assertion_id FROM memory_assertions WHERE scope=?
            UNION SELECT r.assertion_id FROM memory_branch_reviews r JOIN memory_views v ON v.id=r.view_id,
                json_each(v.definition,'$.scopes') s WHERE r.decision='accepted' AND s.value=?
            UNION SELECT CASE WHEN e.from_id=r.id THEN e.to_id ELSE e.from_id END
                FROM related r JOIN memory_edges e ON e.from_id=r.id OR e.to_id=r.id
            ), boundaries(boundary) AS (
            SELECT a.valid_at FROM memory_assertions a JOIN related r ON r.id=a.assertion_id
            UNION ALL SELECT a.recorded_at FROM memory_assertions a JOIN related r ON r.id=a.assertion_id
            UNION ALL SELECT json_extract(a.metadata,'$.interpretation.interpreted_at')
                FROM memory_assertions a JOIN related r ON r.id=a.assertion_id
            UNION ALL SELECT e.recorded_at FROM memory_edges e JOIN related r
                ON e.from_id=r.id OR e.to_id=r.id
            ) SELECT min(boundary) FROM boundaries WHERE boundary>?""",(scope,scope,self._time())).fetchone()[0]

    def seconds_until_work(self, *, with_model):
        if not with_model:
            return None
        with self.store.transaction() as connection:
            row = connection.execute("""SELECT min(deadline) FROM (
                SELECT CASE WHEN status='running' THEN lease_until ELSE available_at END deadline
                    FROM memory_derived_work WHERE status IN ('pending','waiting_model','running')
                        OR (status='failed' AND error_count<3
                        AND coalesce(json_extract(checkpoint,'$.overview.failure.terminal_discovery'),0)=0)
                UNION ALL SELECT next_wake_at FROM memory_derived_work WHERE next_wake_at IS NOT NULL)""").fetchone()
        return max(0,(datetime.fromisoformat(row[0])-datetime.fromisoformat(self._time())).total_seconds()) if row[0] else None
