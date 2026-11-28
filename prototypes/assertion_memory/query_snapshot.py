"""One short read boundary shared by planning, a view DAG and its reviewers.

The source tables are append-only through the store API. Frozen maps cover graph
reads; the assertion insertion watermark also bounds indexed FTS before LIMIT.
No SQLite transaction remains open while a model or embedder runs.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone

from . import query_timing as timing
from .storage import _timestamp, _visibility, _decode
from .question_routes import dependency_definitions


class FrozenRelationFrontierChanged(ValueError):
    """Mutable proposal receipts no longer reconstruct an unpublished snapshot."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


@dataclass(frozen=True)
class QuerySnapshot:
    source_version: int
    captured_at: str
    assertion_watermark: int
    edge_watermark: int
    review_watermark: int
    requested_valid_as_of: str | None
    requested_recorded_as_of: str | None
    effective_valid_as_of: str
    effective_recorded_as_of: str
    assertions: dict
    edges: list
    reviews: tuple
    source_revision_watermark: int | None = None
    source_revision_evidence: tuple = ()
    raw_event_watermark: int | None = None
    source_processing_evidence: tuple = ()
    relation_processing_frontier: str | None = None
    relation_processing_evidence: tuple = ()
    raw_store_absent: bool | None = None

    @property
    def limits(self):
        return dict(valid_as_of=self.effective_valid_as_of,
                    recorded_as_of=self.effective_recorded_as_of,
                    include_unknown_valid=self.requested_valid_as_of is None,
                    source_revision_watermark=self.source_revision_watermark)

    def descriptor(self):
        return {field.name:getattr(self,field.name) for field in fields(self)
                if field.name not in ('assertions','edges','reviews','source_revision_evidence','source_processing_evidence','relation_processing_evidence')}

    def source_proof_resolver(self, engine):
        from .overview_source_proof import SourceProofResolver
        return SourceProofResolver(engine.store,self)

    def source_gaps(self, scopes=()):
        from .answer_source_gaps import inventory
        return inventory(self.source_processing_evidence,scopes)

    @timing.timed('relation_frontier')
    def relation_gaps(self, scopes=()):
        from .answer_relation_gaps import inventory
        value=inventory(self.relation_processing_evidence,scopes,self.assertions)
        value['source_frontier_sha256']=self.source_gaps(scopes)['frontier_sha256']
        return value

    def _corpus_parts(self, engine, spec, *, dependency_reviews=True):
        """All authorized eligible sources, including unseen candidates/restrictions.

        Conservatively include navigable cross-scope components and accepted
        branches. Equality is not inferred from selected top-k results.
        """
        definitions=dependency_definitions(engine,spec)
        roots={key for row in definitions for key in row['definition']['root_ids']}
        visited={row['view_id'] for row in definitions}
        review_views=visited if dependency_reviews else {spec.id}
        reviews=[row for row in self.reviews if row.view_id in review_views and row.decision=='accepted'
                 and row.assertion_id in self.assertions]
        roots.update(row.assertion_id for row in reviews)
        ids={key for key,row in self.assertions.items() if not spec.scopes or row['scope'] in spec.scopes}
        ids.update(roots & self.assertions.keys())
        # All possible navigation/required sources, rather than this invocation's
        # chosen candidates. This may conservatively require another review.
        from .engine import _EVIDENCE_RELATIONS
        adjacent={}
        for edge in self.edges:
            if edge['kind'] in _EVIDENCE_RELATIONS:
                adjacent.setdefault(edge['from_id'],[]).append(edge['to_id'])
                adjacent.setdefault(edge['to_id'],[]).append(edge['from_id'])
        pending=list(ids)
        while pending:
            for neighbour in adjacent.get(pending.pop(),()):
                if neighbour not in ids:
                    ids.add(neighbour)
                    pending.append(neighbour)
        links=[edge for edge in self.edges if {edge['from_id'],edge['to_id']} <= ids]
        return ids,links,reviews

    def coverage(self, engine, spec):
        ids,links,reviews=self._corpus_parts(engine,spec,dependency_reviews=False)
        return len(ids)+len(links)+len(reviews)

    def corpus(self, engine, spec):
        ids,links,reviews=self._corpus_parts(engine,spec)
        body=[[self.assertions[key] for key in sorted(ids)],links,[asdict(row) for row in reviews],
              dependency_definitions(engine,spec),self.source_revision_evidence,
              self.source_gaps(spec.scopes)['frontier_sha256'],
              self.relation_gaps(spec.scopes)['freshness_sha256'],
              _recorded_history_empty(engine,spec,self)]
        return hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _project_source_revalidation(assertions, evidence):
    """Annotate read rows from eligible ledger receipts, preserving creation proof.

    The latest source processing outcome and the last positive claim attestation
    are separate audit facts. Neither supplies a new domain observation/date.
    """
    latest, positive = {}, {}
    for event_id, run_id, checked_at, revision in evidence:
        latest[event_id] = (run_id, checked_at, revision)
        for item in revision['reconciliation']:
            if item['decision'] in ('retained','introduced'):
                positive[item['assertion_id']] = (event_id, run_id, revision['status'])
    for key, (event_id, positive_run, positive_status) in positive.items():
        if key not in assertions:
            continue
        run_id, checked_at, revision = latest[event_id]
        assertions[key]['current_source_revalidation'] = {
            'event_id':event_id, 'source_run_id':run_id, 'source_status':revision['status'],
            'protocol':revision['protocol'], 'checked_at':checked_at,
            'last_positive_claim_run_id':positive_run, 'last_positive_source_status':positive_status}


def capture(engine, valid_as_of=None, recorded_as_of=None, *, captured_at=None):
    """Atomically freeze source membership and effective temporal boundaries."""
    store=engine.store
    with store.transaction() as connection:
        source_version=store.version  # Establish SQLite's read snapshot first.
        now=captured_at or _now()
        valid=_timestamp(valid_as_of) if valid_as_of is not None else now
        recorded=_timestamp(recorded_as_of) if recorded_as_of is not None else now
        limits=dict(valid_as_of=valid,recorded_as_of=recorded,include_unknown_valid=valid_as_of is None)
        marks=[connection.execute(f'SELECT coalesce(max(rowid),0) FROM {table}').fetchone()[0]
               for table in ('memory_assertions','memory_edges','memory_branch_reviews')]
        from .source_revision import watermark, selection
        revision_mark=watermark(connection)
        raw_exists=connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_raw_events'").fetchone()
        raw_mark=connection.execute('SELECT coalesce(max(rowid),0) FROM memory_raw_events').fetchone()[0] if raw_exists else None
        from .answer_source_gaps import freeze
        processing_evidence=freeze(connection,recorded,revision_mark,raw_mark)
        from .answer_relation_gaps import freeze as freeze_relations, digest
        relation_evidence=freeze_relations(connection,recorded)
        selected=selection(connection,recorded,revision_mark)
        limits['source_revision_watermark']=revision_mark
        assertions={row.assertion_id:{**asdict(row),'id':row.assertion_id}
                    for row in store.list_assertions(**limits)}
        edges=[{**asdict(row),'id':row.edge_id} for row in store.list_edges(**limits)]
        reviews=tuple(engine.list_branch_reviews(recorded_as_of=recorded))
    _project_source_revalidation(assertions,selected.evidence)
    return QuerySnapshot(source_version,now,*marks,valid_as_of,recorded_as_of,valid,recorded,assertions,edges,reviews,revision_mark,selected.evidence,raw_mark,processing_evidence,digest(relation_evidence),relation_evidence,not bool(raw_exists))


def _restore_frozen(engine, descriptor):
    """Hydrate an immutable generation from bounded row references after restart.

    Both insertion watermarks and original effective time limits are necessary:
    a later insertion can carry an earlier recorded date. Never recapture using
    the current clock/version or admit those backdated rows into an old proof.
    """
    from .engine import _load_branch_review
    names = {field.name for field in fields(QuerySnapshot)} - {'assertions','edges','reviews','source_revision_evidence','source_processing_evidence','relation_processing_evidence'}
    descriptor = dict(descriptor)
    if set(descriptor) <= names and names-set(descriptor) <= {'source_revision_watermark','raw_event_watermark','relation_processing_frontier','raw_store_absent'}:
        descriptor.setdefault('raw_store_absent',None)
        descriptor.setdefault('relation_processing_frontier',None)
        descriptor.setdefault('raw_event_watermark',None)
        descriptor.setdefault('source_revision_watermark',None)  # Legacy visibility.
    if set(descriptor) != names:
        raise ValueError('frozen snapshot requires its exact descriptor')
    limits = dict(valid_as_of=descriptor['effective_valid_as_of'],
                  recorded_as_of=descriptor['effective_recorded_as_of'], scopes=None,
                  include_unknown_valid=descriptor['requested_valid_as_of'] is None)
    clauses, values = _visibility(('a',), **limits)
    eligible = 'SELECT a.assertion_id FROM memory_assertions a WHERE a.rowid<=? AND ' + ' AND '.join(clauses)
    from .source_revision import exclusion, selection
    extra,arguments=exclusion(engine.store._connection,'a',descriptor['effective_recorded_as_of'],descriptor['source_revision_watermark'])
    if extra:
        eligible += ' AND ' + ' AND '.join(extra)
    parameters = (descriptor['assertion_watermark'], *values, *arguments)
    with engine.store.transaction() as connection:
        rows = connection.execute('SELECT a.* FROM memory_assertions a WHERE a.assertion_id IN ('+eligible+') '
            'ORDER BY a.valid_at IS NULL,a.valid_at,a.recorded_at,a.assertion_id',parameters).fetchall()
        assertions = {row['assertion_id']:{**_decode(row),'id':row['assertion_id']} for row in rows}
        rows = connection.execute('WITH eligible(id) AS ('+eligible+') '
            'SELECT e.* FROM memory_edges e JOIN eligible a ON a.id=e.from_id JOIN eligible b ON b.id=e.to_id '
            'WHERE e.rowid<=? AND e.recorded_at<=? ORDER BY e.recorded_at,e.edge_id',
            (*parameters,descriptor['edge_watermark'],descriptor['effective_recorded_as_of'])).fetchall()
        edges = [{**_decode(row),'id':row['edge_id']} for row in rows]
        rows = connection.execute('SELECT * FROM memory_branch_reviews WHERE rowid<=? AND recorded_at<=? '
            'ORDER BY recorded_at,view_id,assertion_id',
            (descriptor['review_watermark'],descriptor['effective_recorded_as_of'])).fetchall()
        reviews = tuple(_load_branch_review(row) for row in rows)
    selected = selection(engine.store._connection,descriptor['effective_recorded_as_of'],descriptor['source_revision_watermark']) if descriptor['source_revision_watermark'] is not None else None
    evidence = selected.evidence if selected else ()
    from .answer_source_gaps import freeze
    processing_evidence=freeze(engine.store._connection,descriptor['effective_recorded_as_of'],
        descriptor['source_revision_watermark'],descriptor['raw_event_watermark'])
    from .answer_relation_gaps import freeze as freeze_relations, digest
    relation_evidence=(freeze_relations(engine.store._connection,descriptor['effective_recorded_as_of'])
        if descriptor['relation_processing_frontier'] is not None else ())
    if descriptor['relation_processing_frontier'] is not None and digest(relation_evidence)!=descriptor['relation_processing_frontier']:
        raise FrozenRelationFrontierChanged('frozen relation proposal frontier changed; recapture before regeneration')
    _project_source_revalidation(assertions,evidence)
    return QuerySnapshot(**descriptor,assertions=assertions,edges=edges,reviews=reviews,
                         source_revision_evidence=evidence,source_processing_evidence=processing_evidence,
                         relation_processing_evidence=relation_evidence)


def freshness(engine, spec, snapshot):
    """Called inside the short publication write transaction, never across LLMs."""
    observed=engine.capture_snapshot(valid_as_of=snapshot.requested_valid_as_of,
                                     recorded_as_of=snapshot.requested_recorded_as_of)
    covered=snapshot.corpus(engine,spec)
    latest=observed.corpus(engine,spec)
    changed=covered!=latest
    return {'status':'new_eligible_evidence_unchecked' if changed else 'unchanged_at_check',
            'refresh_required':changed,'newer_eligible_evidence_unchecked':changed,
            'latest_observed_version':observed.source_version,'checked_at':observed.captured_at,
            'covered_corpus_revision':covered,'observed_corpus_revision':latest,
            'observed_effective_valid_as_of':observed.effective_valid_as_of,
            'observed_effective_recorded_as_of':observed.effective_recorded_as_of}


def receipt(engine, check_id):
    from .engine import _load_check
    with engine.store.transaction() as connection:
        row=connection.execute('SELECT * FROM memory_view_checks WHERE id=?',(check_id,)).fetchone()
    return _load_check(row)


def published_winner(connection, view_id, fingerprint):
    """An identical-input winner retains its own exact reviewed output and audit."""
    from .engine import _load_version
    row=connection.execute(
        "SELECT v.id,v.snapshot,c.id AS check_id,c.overview_review FROM memory_view_versions v "
        "LEFT JOIN memory_view_checks c ON c.view_id=v.view_id AND c.version_id=v.id "
        "AND coalesce(json_extract(c.overview_review,'$.certificate.kind'),'') != 'answer_applicability' AND c.input_fingerprint=v.fingerprint WHERE v.view_id=? AND v.fingerprint=? "
        "AND coalesce(json_extract(c.overview_review,'$.status'),'') != 'unresolved' "
        "ORDER BY c.id DESC LIMIT 1",(view_id,fingerprint)).fetchone()
    if row is None:
        return None
    version=_load_version(row)
    review=json.loads(row['overview_review']) if row['overview_review'] else version.overview_review
    return version,review,row['check_id']


def recorded_history_inventory(engine,spec,snapshot):
    from .recorded_history import inventory
    return inventory(engine,spec,snapshot)


def recorded_history_certificate(engine,spec,snapshot):
    from .recorded_history import certificate
    return certificate(engine,spec,snapshot)


def _recorded_history_empty(engine,spec,snapshot):
    value=recorded_history_inventory(engine,spec,snapshot)
    return (None if value is None else not (value['raw_known_count']
            or value['canonical_known_count_before_validity_filter']))
