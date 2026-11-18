"""Harness-neutral application service. MCP and optional adapters share this store."""
from __future__ import annotations

import json
import hashlib
import sqlite3
import time
from contextlib import nullcontext
from dataclasses import asdict
from typing import Literal

from .engine import MemoryEngine
from .model import ModelError, SemanticUnavailable
from .queue import CaptureQueue
from .derived_work import DerivedWorkQueue
from . import derived_directory
from .storage import MemoryStore, _timestamp, background_write_retry
from .usage_context import current_query_trace, query_usage_trace

RESPONSE_CHARACTERS = 28_000
PAGE_CHARACTERS = 6_000
DIAGNOSTIC_CHARACTERS = 2_000
QUERY_USAGE_RESERVE_CHARACTERS = 4_000


class MemoryService:
    def __init__(self, store: MemoryStore, engine: MemoryEngine | None = None) -> None:
        self.store = store
        self.queue = CaptureQueue(store)
        self.engine = engine or MemoryEngine(store)
        self.derived = DerivedWorkQueue(store)
        with store.transaction(write=True) as connection:
            from .source_reprocessing import initialize_admission
            initialize_admission(connection)
            from .artifacts import schema as artifact_schema
            artifact_schema(connection)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_message_receipts (
                    session_id TEXT NOT NULL, message_id TEXT NOT NULL,
                    payload TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES memory_raw_events(id),
                    PRIMARY KEY (session_id, message_id)
                )
            """)

    def capture_message(
        self, session_id: str, message_id: str, actor_id: str,
        role: Literal['user', 'assistant', 'tool'], text: str, position: int | None,
        source: str, scope: str = 'default',
        *, source_message_at: str | None = None, source_message_clock_basis: dict | None = None,
        timezone: str | None = None, delivery: dict | None = None, artifact_ids: list[str] | None = None,
    ) -> dict:
        fields = (session_id, message_id, actor_id, text, source, scope)
        if not all(isinstance(value, str) and value.strip() for value in fields):
            raise ValueError('session/message/actor IDs, text, source and scope must be nonempty')
        if role not in ('user', 'assistant', 'tool') or (role == 'user' and actor_id in ('assistant', 'tool')):
            raise ValueError('role must be user, assistant or tool, with a consistent actor_id')
        if position is not None and (type(position) is not int or position < 0):
            raise ValueError('position must be a nonnegative producer transcript ordinal or None when unknown')
        identifiers = (session_id, message_id, actor_id, source, scope)
        if len(text) > 1_000_000 or any(len(value) > 2048 for value in identifiers):
            raise ValueError('message exceeds supported size')
        from .artifacts import normalize_ids, read as read_artifact
        artifact_ids = normalize_ids(artifact_ids)
        payload = json.dumps([*fields, role, position], ensure_ascii=False)
        fingerprint = _payload_fingerprint(payload)
        from .source_message_clock import normalize, replay, FIELDS
        metadata = {}
        if source_message_at is not None:
            basis = (source_message_clock_basis if source_message_clock_basis is not None else
                {'kind': 'producer', 'field': 'source_message_at',
                 'value': source_message_at, 'source': source, 'message_id': message_id})
            if not isinstance(basis, dict) or basis.get('kind') != 'producer':
                raise ValueError('generic source message clock is a producer claim, not native authentication')
            if 'message_id' in basis and basis['message_id'] != message_id:
                raise ValueError('source message clock basis belongs to another message identity')
            metadata.update(source_message_at=source_message_at, source_message_clock_basis=basis)
        elif source_message_clock_basis is not None:
            raise ValueError('source message clock basis requires source_message_at')
        if timezone is not None:
            metadata['timezone'] = timezone
        metadata = normalize(metadata, source=source)
        def clock_fingerprint(value):
            return _payload_fingerprint(json.dumps({key:value[key] for key in (*FIELDS,'timezone','artifact_refs') if key in value},
                ensure_ascii=False, sort_keys=True))
        with self.store.transaction(write=True) as connection:
            if artifact_ids:
                metadata['artifact_refs'] = [read_artifact(connection,key) for key in artifact_ids]
            receipt_value = ('source-clock-v1:' + json.dumps({'base':fingerprint,'clock':clock_fingerprint(metadata)},
                             sort_keys=True) if metadata else fingerprint)
            previous = connection.execute('SELECT payload, event_id FROM memory_message_receipts '
                                          'WHERE session_id = ? AND message_id = ?',
                                          (session_id, message_id)).fetchone()
            if previous:
                order = connection.execute('SELECT position FROM memory_event_positions WHERE event_id=?',
                                           (previous['event_id'],)).fetchone()
                prior_position = order['position'] if order else None
                if position is not None and prior_position is not None and position != prior_position:
                    raise ValueError('message identity already has a different producer transcript ordinal')
                # Omitted order does not contradict or erase an existing claim.
                # Replaying an unknown-order receipt also never rewrites raw origin.
                replay_fingerprint = _payload_fingerprint(json.dumps([*fields, role, prior_position], ensure_ascii=False))
                clock_receipt = (json.loads(previous['payload'][len('source-clock-v1:'):])
                                 if previous['payload'].startswith('source-clock-v1:') else None)
                previous_fingerprint = (clock_receipt['base'] if clock_receipt else
                    previous['payload'] if previous['payload'].startswith('sha256:')
                    else _payload_fingerprint(previous['payload']))
                if previous_fingerprint != replay_fingerprint:
                    raise ValueError('message identity already has different immutable content or provenance')
                event = self.queue.get_event(previous['event_id'])
                if event.metadata.get('artifact_refs',[]) != metadata.get('artifact_refs',[]):
                    raise ValueError('message identity already has different immutable artifact references')
                replay(event.metadata, metadata, allow_omitted=True, source=source)
                if ('timezone' in metadata and 'timezone' in event.metadata
                        and metadata['timezone'] != event.metadata['timezone']):
                    raise ValueError('conflicting source speaker timezone')
                if clock_receipt and clock_receipt['clock'] != clock_fingerprint(event.metadata):
                    raise ValueError('source message clock receipt contradicts immutable raw metadata')
                if not clock_receipt and not previous['payload'].startswith('sha256:'):
                    connection.execute('UPDATE memory_message_receipts SET payload = ? '
                                       'WHERE session_id = ? AND message_id = ?',
                                       (replay_fingerprint, session_id, message_id))
            else:
                occupied = connection.execute(
                    'SELECT e.id FROM memory_raw_events e JOIN memory_event_positions p '
                    'ON p.event_id = e.id WHERE e.session_id = ? AND p.position = ?',
                    (session_id, position)).fetchone() if position is not None else None
                if occupied:
                    raise ValueError('session transcript position already belongs to a different message')
                event = self.queue.capture_event(
                    session_id, message_id, text, source=source, scope=scope, actor=actor_id,
                    message_id=message_id, position=position,
                    metadata={'role': role, 'message_id': message_id, **metadata},
                )
                connection.execute('INSERT INTO memory_message_receipts VALUES (?, ?, ?, ?)',
                                   (session_id, message_id, receipt_value, event.id))
            from .ingest_streams import record_delivery
            delivered = record_delivery(connection, delivery, event.id, session_id)
        return {'event': _event_descriptor(event), 'job': _job_descriptor(self.queue.get(event.id)),
                'pending_count': self._pending_count(), **({'delivery': delivered} if delivery is not None else {})}

    def store_artifact(self, data_base64, mime_type, sha256=None) -> dict:
        from .artifacts import store
        with self.store.transaction(write=True) as connection:
            return store(connection,data_base64,mime_type,sha256)

    def artifact(self, artifact_id, *, include_content=False) -> dict:
        from .artifacts import read
        with self.store.transaction() as connection:
            return read(connection,artifact_id,include_content=include_content)

    def ingest_register(self, producer_id, session_id, *, replace_epoch=None):
        from .ingest_streams import register
        with self.store.transaction(write=True) as connection:
            return register(connection, producer_id, session_id, replace_epoch=replace_epoch)

    def ingest_report(self, stream_id, epoch, report_seq, observed_seq, scan_state, gap_count, *, lifecycle='active'):
        from .ingest_streams import report
        with self.store.transaction(write=True) as connection:
            return report(connection, stream_id, epoch, report_seq, observed_seq, scan_state, gap_count,
                          lifecycle=lifecycle)

    def transport_status(self):
        from .ingest_streams import transport_status
        with self.store.transaction() as connection:
            return transport_status(connection)

    def _attach_query_transport(self, result):
        # This live invocation limit is separate from immutable source/view proof.
        # Never scope-filter or ask a model to waive undelivered unknown content.
        try:
            transport = self.transport_status()
        except (sqlite3.Error, OSError) as error:
            transport = {'coverage': 'unobserved', 'transport_limit': True,
                         'health_error': type(error).__name__}
        result['transport_status'] = transport
        result['transport_limit'] = bool(transport['transport_limit'])
        if result['transport_limit']:
            result['refresh_required'] = True
            result['completeness'] = 'incomplete'
            if result.get('processing_state') not in ('errors', 'unknown'):
                result['processing_state'] = 'pending'
        return result

    def sessions(self, *, offset=0, limit=50, snapshot_id=None) -> dict:
        from .session_api import SessionAPI
        with self.store.transaction() as connection:
            return SessionAPI(connection).sessions(offset=offset, limit=limit, snapshot_id=snapshot_id)

    def session_messages(self, session_id, *, turn_id=None, message_id=None,
                         offset=0, limit=50, snapshot_id=None) -> dict:
        from .session_api import SessionAPI
        with self.store.transaction() as connection:
            return SessionAPI(connection).messages(session_id, turn_id=turn_id, message_id=message_id,
                offset=offset, limit=limit, snapshot_id=snapshot_id)

    def provenance(self, record_ids) -> dict:
        from .session_api import SessionAPI
        with self.store.transaction() as connection:
            return SessionAPI(connection).provenance(record_ids)

    def session_outputs(self, session_id, *, offset=0, limit=50, snapshot_id=None) -> dict:
        from .run_provenance import session_outputs
        with self.store.transaction() as connection:
            return session_outputs(connection,session_id,offset=offset,limit=limit,snapshot_id=snapshot_id)

    def run_history(self, run_id, *, section='run', offset=0, limit=6000, snapshot_id=None) -> dict:
        from .run_provenance import run_page
        with self.store.transaction() as connection:
            return run_page(connection,run_id,section=section,offset=offset,limit=limit,snapshot_id=snapshot_id)

    def run_event(self, event_id, *, offset=0, limit=6000) -> dict:
        from .run_provenance import event_page
        with self.store.transaction() as connection:
            return event_page(connection,event_id,offset=offset,limit=limit)

    def query(
        self, query: str, scope: str | None = None, valid_as_of: str | None = None,
        recorded_as_of: str | None = None, *, request_session_id: str | None = None,
    ) -> dict:
        # Validate before execution() creates an operational run or any view intent.
        if not isinstance(query,str) or not query.strip() or len(query)>8000:
            raise ValueError('query must contain between 1 and 8000 characters')
        if scope is not None and (not isinstance(scope,str) or not scope.strip()):
            raise ValueError('scope must be nonempty or None for the connected database')
        for value in (valid_as_of,recorded_as_of):
            if value is not None:_timestamp(value)
        from .run_provenance import execution
        started = time.monotonic()
        # Admit each short write in place; never replay a query or hold a writer
        # transaction while its planner, writer or reviewer calls a provider.
        with background_write_retry(), query_usage_trace() as trace_id:
            run = (execution(self.engine,owner_id='memory_query',request_session_id=request_session_id)
                   if request_session_id is not None else nullcontext())
            with run:
                result = self._query(query,scope,valid_as_of,recorded_as_of)
        wall_ms = max(0, round((time.monotonic()-started)*1000))
        result = {**result, 'request_usage':_request_usage(self.store.path,trace_id,wall_ms)}
        if not _fits(result):
            raise ValueError('query response accounting exceeds reserved response budget')
        return result

    def _query(
        self, query: str, scope: str | None = None, valid_as_of: str | None = None,
        recorded_as_of: str | None = None,
    ) -> dict:
        # Resolve database-wide membership from metadata at the same read
        # boundary as the frozen corpus; never invent a literal default scope.
        with self.store.transaction() as connection:
            scopes = ((scope,) if scope is not None else tuple(row[0] for row in connection.execute(
                'SELECT scope FROM memory_assertions UNION SELECT scope FROM memory_raw_events ORDER BY scope')
                if isinstance(row[0], str) and row[0].strip()))
            snapshot = self.engine.capture_snapshot(valid_as_of=valid_as_of, recorded_as_of=recorded_as_of)
        receipts, route = {}, None
        try:
            route = self.engine.prepare_question(query, scopes=scopes,
                valid_as_of=valid_as_of, recorded_as_of=recorded_as_of, _snapshot=snapshot)
            requested=route['plan']['request']['question']
            routed_spec=self.engine._spec(route['view_id'])
            if routed_spec.query!=requested or routed_spec.scopes!=scopes:
                from .answer_applicability import attempt
                from .question_routes import materialize_after_reuse_rejection
                rejection='No eligible independently grounded immutable candidate for requested perspective.'
                try:
                    applicable=attempt(self.engine,route,snapshot)
                except SemanticUnavailable as applicability_error:
                    applicable=None; rejection=applicability_error
                if applicable is not None:
                    view,check=applicable
                    return self._query_result(view,route['plan'],self._query_health(scope,snapshot),check,scope,recorded_as_of)
                route=materialize_after_reuse_rejection(self.engine,route,rejection)
            view = self.engine.build_view(route['view_id'], valid_as_of=valid_as_of,
                recorded_as_of=recorded_as_of, _snapshot=snapshot, _receipts=receipts)
        except (ModelError, ValueError) as error:
            if not hasattr(error, 'acquisition'):
                raise
            return self._failed_query(error, route, snapshot, scope, recorded_as_of)
        return self._query_result(view, route['plan'], self._query_health(scope, snapshot),
                                  receipts[view.view_id], scope, recorded_as_of)

    def _query_health(self, scope, snapshot, unavailable=None):
        try:
            if unavailable is None:
                return {**self.status(scope=scope), 'policy_coverage': self.policy_coverage(scope)}
        except (sqlite3.Error, OSError) as error:
            unavailable = str(error)
        return {'health_error': str(unavailable)[:DIAGNOSTIC_CHARACTERS],
                'source_version': snapshot.source_version, 'pending_count': None,
                'error_count': None, 'unresolved_count': None}

    def _record_query_output(self, view, check):
        """Bind the exact returned pin even when the engine's cached path did not publish."""
        from .run_provenance import active, publication
        run_id=active(self.engine)
        if run_id is None:return
        with self.store.transaction(write=True) as connection:
            actual=connection.execute('SELECT view_id,version_id FROM memory_view_checks WHERE id=?',
                                      (check['id'],)).fetchone()
            if actual is None or actual['view_id']!=view.view_id or actual['version_id']!=view.id:
                raise ValueError('returned query check does not bind its immutable version')
            recorded=connection.execute("SELECT 1 FROM memory_run_events WHERE run_id=? AND kind='view' "
                'AND version_id=? AND check_id=? LIMIT 1',(run_id,view.id,check['id'])).fetchone()
            if recorded is None:
                # produced is reserved for the actual transactional _publish path.
                publication(self.engine,connection,view,check['id'],produced=False)

    def _query_result(self, view, plan, state, check, scope, recorded_as_of, generation_error=None):
        self._record_query_output(view,check)
        health_unknown = 'health_error' in state
        processing = ('errors' if generation_error or state.get('error_count') else
                      'unknown' if health_unknown else 'pending' if state['pending_count'] or any(
                          state.get('source_audit_request_counts',{}).get(key,0) for key in ('pending','scheduled','refresh')) else
                      'needs_review' if state['unresolved_count'] else 'idle')
        requested_question=plan.get('request',{}).get('question')
        processing_gaps,freshness_gaps=self._query_gap_assessment(scope,recorded_as_of,state,check,view=view,
            requested_question=requested_question)
        gaps = processing_gaps or view.missing_roots
        result = {'view': asdict(view), 'retrieval_plan': plan, **state, 'processing_state': processing,
                  'completeness': 'incomplete' if generation_error or gaps or health_unknown or processing_gaps is None else 'not_guaranteed',
                  'source_status': {'evidence_count': len(view.evidence), 'missing_roots': view.missing_roots,
                      'scope': scope, 'scopes':list(plan.get('request',{}).get('scopes',())),
                      'queue_counts_scope': scope,
                      'queue_counts_perspective': 'latest_scope_health',
                      'processing_gaps_apply_to_query': processing_gaps},
                  'notice': 'Evidence records are attributed reports. An idle queue does not establish '
                            'full coverage, settle contradictions or prove present reality.'}
        recovery=check.get('overview_review') or {}
        if recovery.get('source_omissions'):
            result['source_status']['interpretation_recovery']={'declared_omissions':len(recovery['source_omissions']),
                'admissions':recovery.get('source_audit_admissions',[])}
        from .view_names import presentation
        naming,naming_snapshot=presentation(self.engine,view.view_id,version_id=view.id,recorded_as_of=recorded_as_of)
        if naming['naming_status'] in ('accepted','fallback'):
            result['presentation']=naming
            result['presentation_snapshot']=naming_snapshot
        elif naming['naming_status']=='naming_pending':
            result['naming_status']='naming_pending'
        if generation_error:
            result['generation_error'] = generation_error
            result['notice'] = 'No reviewed answer was produced. Acquired evidence is available as unresolved proof.'
        result['freshness_check'] = _check_descriptor(check)
        result['query_snapshot'] = check.get('invocation_query_snapshot',check['query_snapshot'])
        if ((check.get('overview_review') or {}).get('certificate') or {}).get('kind')=='answer_applicability':
            result['original_question']=self.engine._spec(view.view_id).query
            result['answer_certificate']={'kind':'answer_applicability','check_id':check['id'],
                'request_id':check['overview_review']['certificate']['request_id']}
        result['answer_accepted'] = bool(not generation_error and
            self._answer_review_bound(check,view,requested_question=requested_question))
        from .recorded_history import from_audit, metadata as history_metadata
        try:
            history_proof=from_audit(self.engine,self.engine._spec(view.view_id),
                check.get('overview_review') or {},check['query_snapshot'])
        except (KeyError,TypeError,ValueError):
            history_proof=None
        if history_proof is not None:
            result['answer_metadata']=history_metadata(history_proof,notice=not result['answer_accepted'])
            result['completeness']='incomplete'
        if not result['answer_accepted']:
            reviewed_question=(check.get('overview_review') or {}).get('query_context',{}).get('question')
            result['answer_acceptance_reason']=(
                'The pinned review certifies a different stored question; the planner reused its answer as '
                'semantically equivalent. No new exact-question review was performed.'
                if requested_question is not None and reviewed_question is not None
                   and (check.get('overview_review') or {}).get('status')=='accepted'
                   and requested_question!=reviewed_question else
                'No valid independent review binds this invocation to the exact question and final answer text.')
        result['refresh_required'] = bool(generation_error or freshness_gaps or view.missing_roots or health_unknown or check['freshness']['refresh_required'])
        return self._query_response(self._attach_query_transport(result), view, scope)

    def _answer_review_bound(self,check,view=None,*,requested_question=None):
        """A receipt certifies one exact question and final text, not a similar draft."""
        from .overview_review import _digest, _hash_text
        review=check.get('overview_review') or {}
        if review.get('status')!='accepted': return False
        if view is None:
            view=next((row for row in self.engine.view_history(check['view_id'])
                       if row.id==check['version_id']),None)
        if view is None or view.id!=check['version_id'] or view.view_id!=check['view_id']:
            return False
        from .question_routes import routing_review
        from .background_overviews import thematic_navigation_context
        try:
            spec=self.engine._spec(check['view_id'])
        except ValueError:
            return False
        if (routing_review(review) or routing_review(view.overview_review) or
                thematic_navigation_context(self.engine, spec) is not None):
            return False
        if (review.get('certificate') or {}).get('kind')=='answer_applicability':
            from .answer_applicability import bound
            return bound(self.engine,check,view,requested_question)
        frame=review.get('query_context',{})
        if 'knowledge_at_cutoff' in frame:
            from .recorded_history import from_audit
            if from_audit(self.engine,spec,review,check['query_snapshot']) is None: return False
        return (review.get('final_text_sha256')==_hash_text(view.summary)
                and review.get('query_context_sha256')==_digest(frame)
                and frame.get('question')==spec.query and frame.get('scopes')==list(spec.scopes)
                and (requested_question is None or frame.get('question')==requested_question))

    def _query_gap_assessment(self,scope,recorded_as_of,state,check,*,view=None,requested_question=None):
        if 'health_error' in state: return None,True
        # One short read snapshot covers proposal revisions, source outcomes and
        # operational guards. No model work or external waits occurs here.
        with self.store.transaction():
            return self._query_gap_assessment_at_boundary(scope,recorded_as_of,state,check,view=view,
                requested_question=requested_question)

    def _query_gap_assessment_at_boundary(self,scope,recorded_as_of,state,check,*,view=None,requested_question=None):
        from .answer_source_gaps import freeze,inventory
        from .answer_relation_gaps import freeze as freeze_relations, inventory as relation_inventory, digest, _known_scope
        from .source_revision import watermark
        from .query_snapshot import _now
        audit=check.get('overview_review') or {}
        scopes=audit.get('query_context',{}).get('scopes')
        scopes=tuple(scopes) if scopes is not None else (scope,) if scope is not None else ()
        cutoff=_timestamp(recorded_as_of) if recorded_as_of is not None else _now()
        with self.store.transaction() as connection:
            raw_mark=connection.execute('SELECT coalesce(max(rowid),0) FROM memory_raw_events').fetchone()[0]
            sources=freeze(connection,cutoff,watermark(connection),raw_mark,include_raw=False)
            relations=freeze_relations(connection,cutoff)
        current_sources=inventory(sources,scopes)
        current_relations=relation_inventory(relations,scopes)
        current_relations['source_frontier_sha256']=current_sources['frontier_sha256']
        # Exclude only structurally terminal relation candidates from the
        # operational check. Their applicability is assessed separately below;
        # pending source/audit/model work can never use this exclusion.
        exclusions=[{'job_id':row['job_id'],'proposal_index':row['proposal_index']} for row in relations
                    if _known_scope(row) and row['terminal'] and (not scopes or row['scope'] in scopes)
                    and row.get('status') in ('pending','unresolved')]
        processing=self._query_processing_gaps(scope,recorded_as_of,state,relation_exclusions=exclusions)
        refresh=self._query_processing_gaps(scope,recorded_as_of,state,freshness_only=True,relation_exclusions=exclusions)
        if current_relations['operational_count']:
            refresh=True
        if refresh: processing=True
        review=audit.get('source_gap_review')
        if processing and not refresh:
            # An answer-specific receipt never rewrites queue health/history.
            if review:
                current=current_sources
                reviewed=review.get('inventory',{})
                if (current['frontier_sha256']==reviewed.get('frontier_sha256')
                        and self._answer_review_bound(check,view,requested_question=requested_question)
                        and review.get('final_text_sha256')==audit.get('final_text_sha256')):
                    processing=review.get('processing_gaps_apply_to_query')
                else:
                    processing=None; refresh=True
            else:
                processing=None
        relation_review=audit.get('relation_gap_review')
        if current_relations['total'] or relation_review:
            valid=bool(relation_review and self._answer_review_bound(check,view,requested_question=requested_question)
                and relation_review.get('final_text_sha256')==audit.get('final_text_sha256')
                and relation_review.get('query_context_sha256')==digest(audit.get('query_context',{}))
                and relation_review.get('inventory',{}).get('freshness_sha256')==current_relations['freshness_sha256']
                and relation_review.get('inventory',{}).get('source_frontier_sha256')==current_relations['source_frontier_sha256'])
            applies=relation_review.get('processing_gaps_apply_to_query') if valid else None
            if applies is False and not relation_review.get('inventory',{}).get('complete'):
                applies=None
            processing=(True if processing is True or applies is True else
                        None if processing is None or applies is None else False)
            # A changed/unreviewed/material relation remains a query-local
            # blocker. Extra audit pages alone cannot establish applicability.
            refresh=bool(refresh or applies is not False)
        if audit.get('source_omissions'):processing=True
        return processing,refresh

    def _failed_query(self, error, route, snapshot, scope, recorded_as_of):
        acquired = error.acquisition
        message = str(error)
        detail = {'message': message[:DIAGNOSTIC_CHARACTERS], 'message_complete': len(message) <= DIAGNOSTIC_CHARACTERS,
                  'phase': acquired.get('phase', 'unknown'), 'answer_accepted': False}
        state = self._query_health(scope, snapshot, acquired.get('persistence_error'))
        view, check = getattr(error, 'failure_version', None), getattr(error, 'failure_check', None)
        plan = route['plan'] if route else {'method': 'failed', 'reason': 'Planning did not complete.'}
        if view is None or check is None:
            return self._inline_failure(error, detail, snapshot, scope, state)
        return self._query_result(view, plan, state, check, scope, recorded_as_of, detail)

    def _inline_failure(self, error, detail, snapshot, scope, state):
        acquired = error.acquisition
        records = acquired.get('evidence', [])
        result = {**state, 'generation_error': detail, 'processing_state': 'errors',
                  'completeness': 'incomplete', 'refresh_required': True, 'response_complete': False,
                  'query_snapshot': snapshot.descriptor(), 'continuations': [],
                  'answer_accepted': False,'answer_payload_complete':False,
                  'answer_continuations':[],'proof_complete':False,
                  'persistence_error': str(acquired.get('persistence_error', 'Proof was not persisted.'))[:DIAGNOSTIC_CHARACTERS],
                  'source_status': {'evidence_count': len(records), 'inline_evidence_count': 0,
                      'omitted_evidence_count': len(records), 'scope': scope},
                  'view': {'id': None, 'view_id': getattr(error, 'failure_view_id', None),
                      'summary': '', 'evidence': [], 'edges': [],
                      'overview_review': {'status': 'unresolved', 'failure_proof': True}},
                  'notice': 'Proof persistence failed. Only bounded inline acquired evidence is returned; '
                            'omissions are counted and no durable continuation is promised.'}
        self._attach_query_transport(result)
        from .recorded_history import from_audit, metadata as history_metadata
        try:
            review=acquired.get('review') or {}
            spec=(self.engine._spec(getattr(error,'failure_view_id',None))
                  if review.get('query_context',{}).get('knowledge_at_cutoff') is not None else None)
            proof=from_audit(self.engine,spec,review,snapshot.descriptor()) if spec is not None else None
        except (KeyError,TypeError,ValueError,sqlite3.Error):
            proof=None
        if proof is not None: result['answer_metadata']=history_metadata(proof,notice=True)
        for record in records:
            proposed = [*result['view']['evidence'], record]
            candidate = {**result, 'view': {**result['view'], 'evidence': proposed}}
            if not _fits(candidate):
                continue
            result = candidate
        ids = {row['id'] for row in result['view']['evidence']}
        for edge in acquired.get('edges', []):
            if not {edge['from_id'], edge['to_id']} <= ids:
                continue
            candidate = {**result, 'view': {**result['view'], 'edges': [*result['view']['edges'], edge]}}
            if _fits(candidate):
                result = candidate
        return _describe_inline_omissions(result, records, ids)

    def source(
        self, record_id: str, start: int | None = None, end: int | None = None, *,
        section: str = 'overview', offset: int = 0, limit: int = PAGE_CHARACTERS,
        snapshot_id: int | None = None,
    ) -> dict:
        if (start is None) != (end is None):
            raise ValueError('source span requires both start and end')
        event = self.queue.get_event(record_id)
        assertion = None if event else self.store.get_assertion(record_id)
        if event is None and assertion is None:
            raise ValueError('unknown source event or assertion ID')
        if start is not None:
            return self._source_span(record_id,event,assertion,start,end)
        if section != 'overview':
            return self._source_section(record_id,event,assertion,section,offset,limit,snapshot_id)
        if event:
            response = {'event':asdict(event),'job':asdict(self.queue.get(event.id)),
                        'extraction_history':self.queue.extraction_history(event.id)}
        else:
            refs = assertion.metadata.get('evidence_refs',[])
            response = {'assertion':asdict(assertion),
                        'references':[self.queue.resolve_reference(ref) for ref in refs],
                        'edges':[asdict(edge) for edge in self.store.list_edges(record_id)]}
        if _fits(response):
            return {**response,'response_complete':True}
        return self._source_manifest(record_id,event,assertion)

    def _source_span(self,record_id,event,assertion,start,end):
        text = event.prompt if event else assertion.text
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise ValueError('source span is outside its source')
        if end-start > PAGE_CHARACTERS:
            raise ValueError('source spans are limited to 6000 characters; continue with subsequent spans')
        descriptor = _compact_event(event) if event else _assertion_descriptor(assertion)
        requested_end=end
        while True:
            span = {'record_id':record_id,'start':start,'end':end,'text':text[start:end],
                    'source':(event or assertion).source,'actor':(event or assertion).actor}
            if event:
                span['event_id']=event.id
            result={('event' if event else 'assertion'):descriptor,'span':span,
                'response_complete':end==requested_end,'source_text_complete':start==0 and end==len(text),
                'requested_end':requested_end,'remaining_characters':requested_end-end,
                'continuation':_continuation('memory_source',record_id=record_id,start=end,end=requested_end)
                               if end<requested_end else None}
            if _fits(result):
                return result
            if end<=start+1:
                raise ValueError('source metadata exceeds the response budget')
            end=start+(end-start)//2

    def _source_manifest(self,record_id,event,assertion):
        counts={}
        sections = ('metadata','extraction_history') if event else ('metadata','references','edges')
        cursors = [_continuation('memory_source',record_id=record_id,section='text',offset=0,limit=PAGE_CHARACTERS)]
        for section in sections:
            page = self._source_section(record_id,event,assertion,section,0,PAGE_CHARACTERS,None)
            counts[section]=page['item_count']
            cursors.append(_continuation('memory_source',record_id=record_id,section=section,
                offset=0,limit=PAGE_CHARACTERS,snapshot_id=page.get('snapshot_id')))
        if event:
            cursors.append(_continuation('memory_status',event_id=event.id,section='job',offset=0,limit=PAGE_CHARACTERS))
        return {('event' if event else 'assertion'):
                    _compact_event(event) if event else _assertion_descriptor(assertion),
                'reference_count':len(assertion.metadata.get('evidence_refs',[])) if assertion else 0,
                'edge_count':counts.get('edges',0),
                'extraction_history_count':counts.get('extraction_history',0),
                'response_complete':False,'continuations':cursors,
                'notice':'Source bodies, metadata and restrictions require the listed continuations.'}

    def _source_section(self,record_id,event,assertion,section,offset,limit,snapshot_id):
        if section == 'text':
            value=event.prompt if event else assertion.text
        elif section == 'metadata':
            value = event.metadata if event else assertion.metadata
        elif section == 'references' and assertion:
            value = assertion.metadata.get('evidence_refs',[])
        elif section == 'extraction_history' and event:
            history = self.queue.extraction_history(event.id)
            snapshot_id = max((row['id'] for row in history),default=0) if snapshot_id is None else snapshot_id
            value = [row for row in history if row['id'] <= snapshot_id]
        elif section == 'edges' and assertion:
            with self.store.transaction() as connection:
                maximum = connection.execute('SELECT coalesce(max(rowid),0) FROM memory_edges').fetchone()[0]
                snapshot_id = maximum if snapshot_id is None else snapshot_id
                ids = {row[0] for row in connection.execute('SELECT edge_id FROM memory_edges WHERE rowid <= ?',
                                                          (snapshot_id,)).fetchall()}
            value = [asdict(edge) for edge in self.store.list_edges(record_id) if edge.edge_id in ids]
        else:
            raise ValueError('unsupported section for this source record')
        args = {'record_id':record_id,'section':section,'snapshot_id':snapshot_id}
        return _text_page(value,'memory_source',args,offset,limit,plain=section=='text')

    def history(
        self, view_id: str, *, version_id: int | None = None, section: str = 'overview',
        offset: int = 0, limit: int = PAGE_CHARACTERS, snapshot_id: int | None = None,
        presentation_cutoff: str | None = None, name_watermark: int | None = None,
    ) -> dict:
        try:self.engine._spec(view_id)
        except ValueError:
            if section!='routes' or version_id is not None:raise
            value,snapshot_id=self._registry_revisions(view_id,snapshot_id)
            return _text_page(value,'memory_history',{'view_id':view_id,'version_id':None,'section':'routes','snapshot_id':snapshot_id},offset,limit,
                envelope={'current_transport_status':self.transport_status()})
        if (presentation_cutoff is None)!=(name_watermark is None):
            raise ValueError('presentation cutoff and name watermark must be supplied together')
        if section!='metadata' and presentation_cutoff is not None:
            raise ValueError('presentation pins are only supported for metadata history')
        if section=='metadata' and offset and presentation_cutoff is None:
            raise ValueError('metadata continuation requires presentation pins')
        pinned=None
        if section=='metadata':
            pinned=self.engine.capture_presentation_snapshot() if presentation_cutoff is None else {
                'presentation_cutoff':_timestamp(presentation_cutoff),'name_watermark':name_watermark}
        if section in ('summary','evidence','edges','metadata'):
            if version_id is None:
                raise ValueError('a pinned version_id is required for snapshot sections')
            version = self.engine.get_version(version_id)
            if version.view_id != view_id:
                raise ValueError('version_id does not belong to this view')
            value = asdict(version)
            if section == 'metadata':
                from .view_names import presentation
                name,naming_snapshot=presentation(self.engine,view_id,version_id=version.id,snapshot=pinned,allow_legacy=True,
                    published_at=version.created_at,recorded_as_of=version.recorded_as_of)
                current,_=presentation(self.engine,view_id,snapshot=pinned)
                if name['naming_status'] in ('accepted','fallback') or current['naming_status'] in ('accepted','fallback'):
                    value.update(presentation=name,presentation_snapshot=naming_snapshot)
                    if current.get('name_record_id') != name.get('name_record_id'):
                        value['current_presentation']=current
            value = ({k:v for k,v in value.items() if k not in ('summary','evidence','edges')}
                     if section == 'metadata' else value[section])
        elif section == 'checks':
            value,snapshot_id = self._check_revisions(view_id,snapshot_id,version_id)
        elif section == 'routes':
            value,snapshot_id = self._route_revisions(view_id,snapshot_id)
        else:
            versions = self.engine.view_history(view_id)
            version_id = max((v.id for v in versions),default=0) if version_id is None else version_id
            versions = [asdict(v) for v in versions if v.id <= version_id]
            if section == 'overview':
                return self._history_overview(view_id,versions,version_id)
            if section != 'versions':
                raise ValueError('unsupported history section')
            value = versions
        args = {'view_id':view_id,'version_id':version_id,'section':section,'snapshot_id':snapshot_id}
        if pinned is not None:args.update(pinned)
        return _text_page(value,'memory_history',args,offset,limit,plain=section=='summary',
            envelope={'current_transport_status':self.transport_status()})

    def _history_overview(self,view_id,versions,maximum):
        routes,route_maximum = self._route_revisions(view_id,None)
        route_descriptors = [_route_descriptor(row) for row in routes]
        checks,check_maximum = self._check_revisions(view_id,None,None)
        normal_checks=[row for row in checks if ((row.get('overview_review') or {}).get('certificate') or {}).get('kind')!='answer_applicability']
        latest=_check_descriptor(normal_checks[-1]) if normal_checks else None
        result = {'versions':versions,'state':self.engine.inspect_view(view_id),
                  'current_transport_status':self.transport_status(),
                  'check_count':len(checks),
                  'latest_check':latest,
                  'route_revisions':route_descriptors,'response_complete':not routes and not checks}
        if routes:
            result['continuations']=[_continuation('memory_history',view_id=view_id,section='routes',
                                                  snapshot_id=route_maximum,offset=0,limit=PAGE_CHARACTERS)]
        if checks:
            result.setdefault('continuations',[]).append(_continuation(
                'memory_history',view_id=view_id,section='checks',snapshot_id=check_maximum,
                offset=0,limit=PAGE_CHARACTERS))
        from .view_names import presentation
        pinned=self.engine.capture_presentation_snapshot()
        if versions:
            latest_version=versions[-1]
            name,naming_snapshot=presentation(self.engine,view_id,snapshot=pinned,
                published_at=latest_version['created_at'],recorded_as_of=latest_version['recorded_as_of'])
            current,_=presentation(self.engine,view_id,snapshot=pinned)
            if name['naming_status'] in ('accepted','fallback') or current['naming_status'] in ('accepted','fallback'):
                result.update(presentation=name,presentation_snapshot=naming_snapshot)
                if current.get('name_record_id') != name.get('name_record_id'):
                    result['current_presentation']=current
        names={key:result[key] for key in ('presentation','presentation_snapshot','current_presentation','current_transport_status') if key in result}
        if _fits(result):
            return result
        descriptors = [_version_descriptor(v) for v in versions]
        result = {**names,'versions':descriptors[:20],'version_count':len(versions),
                  'state':self.engine.inspect_view(view_id),'route_revision_count':len(routes),
                  'check_count':len(checks),
                  'latest_check':latest,
                  'route_revisions':route_descriptors[:20],'response_complete':False,
                  'continuations':[_continuation('memory_history',view_id=view_id,version_id=maximum,
                                     section='versions',offset=0,limit=PAGE_CHARACTERS),
                                   _continuation('memory_history',view_id=view_id,section='routes',
                                     snapshot_id=route_maximum,offset=0,limit=PAGE_CHARACTERS)]}
        if checks:
            result['continuations'].append(_continuation(
                'memory_history',view_id=view_id,section='checks',snapshot_id=check_maximum,
                offset=0,limit=PAGE_CHARACTERS))
        while not _fits(result) and result['versions']:
            result['versions'].pop()
        return result

    def _check_revisions(self,view_id,snapshot_id,version_id):
        checks = self.engine.view_checks(view_id,snapshot_id=snapshot_id)
        snapshot_id = max((row['id'] for row in checks),default=0) if snapshot_id is None else snapshot_id
        return [row for row in checks if version_id is None or row['version_id']==version_id],snapshot_id

    def _registry_revisions(self,registry_id,snapshot_id):
        from .background_overviews import registered_navigation_membership,REGISTRY_PROTOCOL
        with self.store.transaction() as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_question_route_versions'").fetchone():raise ValueError('unknown navigation registry')
            maximum=c.execute('SELECT coalesce(max(rowid),0) FROM memory_question_route_versions').fetchone()[0]
            pinned=maximum if snapshot_id is None else snapshot_id
            if type(pinned) is not int or not 0<=pinned<=maximum:raise ValueError('invalid registry watermark')
            rows=c.execute('SELECT *,rowid AS route_watermark FROM memory_question_route_versions WHERE view_id=? AND rowid<=? ORDER BY rowid',(registry_id,pinned)).fetchall()
            if not rows:raise ValueError('unknown navigation registry')
            result=[]
            for row in rows:
                registry=registered_navigation_membership(self.engine,row)
                if registry['registry_protocol']!=REGISTRY_PROTOCOL or registry['registry_id']!=registry_id:raise ValueError('invalid navigation registry')
                result.append({**{k:row[k] for k in row.keys() if k!='route_watermark'},'plan':json.loads(row['plan'])})
            return result,pinned

    def _route_revisions(self,view_id,snapshot_id):
        with self.store.transaction() as connection:
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                                        "name='memory_question_route_versions'").fetchone()
            if not exists:
                return [],0
            maximum = connection.execute('SELECT coalesce(max(rowid),0) FROM memory_question_route_versions').fetchone()[0]
            snapshot_id = maximum if snapshot_id is None else snapshot_id
            rows = connection.execute('SELECT request_id,revision,view_id,plan,recorded_at '
                'FROM memory_question_route_versions WHERE rowid <= ? AND request_id IN '
                '(SELECT request_id FROM memory_question_route_versions WHERE view_id=? AND rowid<=?) '
                'ORDER BY request_id,revision',(snapshot_id,view_id,snapshot_id)).fetchall()
        return [{**dict(row),'plan':json.loads(row['plan'])} for row in rows],snapshot_id

    def _query_response(self,result,version,scope):
        def delivery(response):
            inline=bool(response.get('view',{}).get('summary'))
            failed=bool(response.get('generation_error'))
            answer_pages=([_continuation('memory_history',view_id=version.view_id,version_id=version.id,
                section='summary',offset=0,limit=PAGE_CHARACTERS)] if not inline and version.summary and not failed else [])
            return {**response,'answer_payload_complete':bool(inline and not failed),
                    'answer_continuations':answer_pages,
                    'proof_complete':response.get('response_complete',True)}
        inline=delivery({**result,'response_complete':result.get('response_complete',True)})
        if _fits(inline): return inline
        if result.get('answer_metadata') and result.get('view',{}).get('overview_review'):
            # The machine certificate is already readable in answer_metadata.
            # Page the duplicate optional audit before paging answer/evidence.
            audit=(result.get('freshness_check') or {}).get('audit')
            if audit:
                cursors=list(result.get('continuations',[]))
                if audit not in cursors: cursors.append(audit)
                compact={key:value for key,value in result['view'].items() if key!='overview_review'}
                compact.update(overview_review_inline=False,overview_review_continuation=audit)
                proposed=delivery({**result,'view':compact,'response_complete':False,'continuations':cursors})
                if _fits(proposed): return proposed
        routes,route_maximum = self._route_revisions(version.view_id,None)
        counts = {key:value for key,value in result.items() if key not in (
            'view','retrieval_plan','pending_jobs','errors','unresolved','pagination','source_status','derived_overviews','source_audit_requests')}
        # Retrieval dependency records are immutable proof, not missing answer
        # content. Their exact check remains available through the pinned audit.
        check=counts.get('freshness_check')
        if check and len(json.dumps(check.get('freshness',{}).get('retrieval_dependencies',[]),ensure_ascii=False))>DIAGNOSTIC_CHARACTERS:
            counts['freshness_check']={**check,'freshness':{key:value for key,value in check['freshness'].items()
                if key!='retrieval_dependencies'},'retrieval_dependencies_inline':False}
        presentation_pins=result.get('presentation_snapshot') or self.engine.capture_presentation_snapshot()
        cursors = [_continuation('memory_history',view_id=version.view_id,version_id=version.id,
                    section=section,offset=0,limit=PAGE_CHARACTERS,
                    **(presentation_pins if section=='metadata' else {}))
                   for section in ('summary','evidence','edges','metadata')]
        if routes:
            cursors.append(_continuation('memory_history',view_id=version.view_id,section='routes',
                                         snapshot_id=route_maximum,offset=0,limit=PAGE_CHARACTERS))
        cursors.append(_continuation('memory_status',scope=scope,offset=0,limit=100))
        directory=result.get('derived_overviews')
        if directory:
            cursors.append(_continuation('memory_status',scope=scope,section='derived_overviews',offset=0,limit=100))
        response = {**counts,'view':{**_version_descriptor(asdict(version)), 'summary':version.summary},
                'retrieval_plan_inline':False,'response_complete':False,'continuations':cursors,
                'source_status':{'evidence_count':len(version.evidence),'edge_count':len(version.edges),
                                 'missing_root_count':len(version.missing_roots),'queue_counts_scope':scope,
                                 'queue_counts_perspective':'latest_scope_health',
                                 'processing_gaps_apply_to_query':result['source_status']['processing_gaps_apply_to_query']},
                'notice':'The answer content is inline; immutable evidence and audit are optional paged inspection. '
                         'Retain all answer qualifications, completeness and freshness limits.'}
        if 'interpretation_recovery' in result['source_status']:
            response['source_status']['interpretation_recovery']=result['source_status']['interpretation_recovery']
        if result.get('generation_error'):
            response['notice']='No reviewed answer was produced. Acquired evidence and failure audit are paged.'
        response=delivery(response)
        if not _fits(response):
            response['view'].pop('summary')
            if not result.get('generation_error'):
                response['notice']=('Answer content is paged. Follow every answer_continuations page, including all '
                                   'qualifications, before using the answer. Proof/audit pages are optional inspection.')
            response=delivery(response)
        return response

    def _query_processing_gaps(self,scope,recorded_as_of,state,*,freshness_only=False,relation_exclusions=()):
        # Latest queue health remains visible independently of this answer.
        # A refresh-only backlog cannot invalidate an independently reviewed
        # snapshot. Terminal parsed source limitations affect completeness; all
        # operational/unparsed work still affects freshness. branch_relevance
        # is only a routing hypothesis, not a new domain fact.
        cutoff = _timestamp(recorded_as_of) if recorded_as_of is not None else None
        with self.store.transaction() as connection:
            stable_ids=[]
            from .source_reprocessing import pending_context, historical_gaps
            from .query_snapshot import _now
            from .source_omission import pending as pending_omissions
            if pending_omissions(connection,scope,cutoff):return True
            if pending_context(connection,scope,cutoff):
                return True
            if freshness_only:
                from .source_revision import watermark
                from .answer_source_gaps import freeze
                raw_mark=connection.execute('SELECT coalesce(max(rowid),0) FROM memory_raw_events').fetchone()[0]
                sources=freeze(connection,cutoff or _now(),watermark(connection),raw_mark,include_raw=False)
                # Failed/unparsed/legacy unresolved runs remain operationally unsafe.
                if any((row['gap'] and not row['terminal_semantic']) or
                       (row['status']=='pending' and not row['context_only']) for row in sources
                       if scope is None or row['scope']==scope):
                    return True
                stable_ids=[row['event_id'] for row in sources if row['terminal_semantic']]
            elif historical_gaps(connection,scope,cutoff or _now()):
                return True
            return bool(connection.execute(
                "SELECT EXISTS(SELECT 1 FROM memory_capture_jobs j "
                "LEFT JOIN memory_raw_events e ON j.event_id=e.id "
                "LEFT JOIN memory_assertions a ON j.assertion_id=a.assertion_id "
                "WHERE (? IS NULL OR coalesce(e.scope,a.scope)=?) "
                "AND (? IS NULL OR CASE WHEN j.phase='audit' OR EXISTS (SELECT 1 FROM memory_extraction_runs r "
                "WHERE r.event_id=e.id AND json_type(r.result,'$.source_revision')='object') "
                "THEN j.updated_at ELSE coalesce(e.recorded_at,a.recorded_at) END <= ?) AND ("
                "(j.phase != 'refresh' AND (j.status != 'completed' OR j.error IS NOT NULL)) "
                "OR j.waiting_model != 0 OR EXISTS (SELECT 1 FROM json_each(j.proposals) p "
                "WHERE json_extract(p.value,'$.status') IN ('unresolved','pending') "
                "AND coalesce(json_extract(p.value,'$.kind'),'') != 'branch_relevance' "
                "AND NOT EXISTS (SELECT 1 FROM json_each(?) x WHERE json_extract(x.value,'$.job_id')=j.id "
                "AND json_extract(x.value,'$.proposal_index')=CAST(p.key AS INTEGER)) "
                "AND NOT (?=1 AND j.status='completed' AND j.event_id IN (SELECT value FROM json_each(?)) "
                "AND json_extract(p.value,'$.kind') IN ('source_coverage','source_revision','interpretation','context_recovery')))))",
                (scope,scope,cutoff,cutoff,json.dumps(relation_exclusions),int(freshness_only),json.dumps(stable_ids))).fetchone()[0])

    def _pending_count(self) -> int:
        with self.store.transaction() as connection:
            return connection.execute(
                "SELECT count(*) FROM memory_capture_jobs WHERE status != 'completed'"
            ).fetchone()[0]

    def policy_coverage(self, scope=None):
        from .source_reprocessing import legacy_coverage
        with self.store.transaction() as connection:
            source = legacy_coverage(connection, scope)
        return {'source': source, 'navigation': self.derived.legacy_coverage(self.engine, scope),
                'notice': 'Policy audit coverage only; legacy checks remain historical, not upgraded proof or world-state verification.'}

    def status(
        self,event_id: str | None = None,*,scope: str | None = None,offset: int = 0,
        limit: int = 100,section: str = 'overview', catalog_id: str | None = None,
    ) -> dict:
        if section == 'policy_coverage':
            if event_id is not None or catalog_id is not None or offset != 0:
                raise ValueError('policy_coverage requires no event_id, catalog_id or offset')
            return {'policy_coverage': self.policy_coverage(scope), 'response_complete': True}
        if section == 'transport':
            if scope is not None or event_id is not None or catalog_id is not None or offset != 0:
                raise ValueError('transport is database-wide; omit scope, event_id, catalog_id and offset')
            return {'transport_status': self.transport_status(), 'response_complete': True}
        if section == 'model_usage':
            if scope is not None or event_id is not None or catalog_id is not None:
                raise ValueError('model_usage is database-wide; omit scope, event_id and catalog_id')
            from .model_usage import usage_status
            return usage_status(self.store.path)
        if section=='view_names':
            from .view_name_work import NameWorkQueue
            page=NameWorkQueue(self.store).status(scope=scope,offset=offset,limit=limit)
            while True:
                end=offset+len(page['entries'])
                page['next_offset']=end if end<page['total'] else None
                cursor=None if page['next_offset'] is None else _continuation('memory_status',scope=scope,
                    section=section,offset=end,limit=limit)
                result={'view_names':page,'response_complete':cursor is None,'continuation':cursor}
                if _fits(result):return result
                if len(page['entries'])<=1:raise ValueError('naming work metadata exceeds response budget')
                page['entries'].pop()
        if section == 'navigation_catalog':
            page = derived_directory.catalog_page(self, scope, catalog_id, offset, limit)
            return {'navigation_catalog': page, 'response_complete': page['response_complete']}
        if catalog_id is not None:
            raise ValueError('catalog_id is only supported for navigation_catalog')
        if section == 'job':
            if event_id is None:
                raise ValueError('job section requires event_id')
            value = self._status_job(event_id,scope)
            return _text_page(value,'memory_status',{'event_id':event_id,'scope':scope,'section':'job'},offset,limit)
        if section == 'derived_overview':
            if scope is None or scope not in derived_directory.scopes(self, scope):
                raise ValueError('derived overview details require a known scope')
            return _text_page(derived_directory.descriptor(self, scope), 'memory_status',
                {'scope': scope, 'section': section}, offset, limit)
        if section == 'derived_overviews':
            directory = derived_directory.directory(self, scope, offset, limit, _fits)
            return {'derived_overviews': directory, 'response_complete': directory['continuation'] is None}
        if section != 'overview':
            raise ValueError('unsupported diagnostic section')
        result = self._status_snapshot(event_id,scope=scope,offset=offset,limit=limit)
        return _bounded_status(result,scope,offset)

    def _status_snapshot(
        self, event_id: str | None = None, *, scope: str | None = None,
        offset: int = 0, limit: int = 100,
    ) -> dict:
        """Exact aggregate counts and explicit independent pages of diagnostic details.

        Scope=None requests global diagnostics. Counts include all matching rows;
        each detail list has its own total and next_offset in pagination.
        """
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('offset must be nonnegative and limit between 1 and 1000')
        if scope is not None and (not isinstance(scope,str) or not scope.strip()):
            raise ValueError('scope must be nonempty or None for global diagnostics')
        with self.store.transaction() as connection:
            result = self._status_counts(connection,scope)
            from .ingest_streams import transport_status
            result['transport_status'] = transport_status(connection)
            result.update(self._status_details(connection,scope,offset,limit,result))
            result.update({
                'source_version':self.store.scope_version(scope),
                'model_configured':self.engine.model is not None,
                'provider_status':'configured' if self.engine.model is not None else 'unconfigured',
                'model_backend':type(self.engine.model).__name__ if self.engine.model else None,
                'retrieval_backend':self.engine.candidates.identity,'pipeline_key':self.engine.pipeline_key,
                'diagnostic_scope':'global' if scope is None else scope})
            if event_id is not None:
                result['job'] = self._status_job(event_id,scope)
        result['derived_overviews'] = derived_directory.directory(self, scope, 0, 10, _fits, include_catalog=False)
        return result

    def _status_job(self, event_id: str, scope: str | None) -> dict:
        job = self.queue.get(event_id)
        if job is not None and scope is not None:
            source = (self.queue.get_event(job.event_id) if job.event_id else
                      self.store.get_assertion(job.assertion_id))
            if source is None or source.scope != scope:
                job = None
        if job is None:
            raise ValueError('unknown job or event ID in requested scope')
        return asdict(job)

    def _status_counts(self, connection, scope, *, selection=None) -> dict:
        base, parameters = selection if selection is not None else _status_selection(scope)
        rows=connection.execute(f'SELECT j.phase,j.status,j.event_id IS NOT NULL,count(*) '
            f'{base} GROUP BY j.phase,j.status,j.event_id IS NOT NULL',parameters).fetchall()
        counts,by_phase,by_origin={},{},{}
        for phase,status,is_message,count in rows:
            counts[status]=counts.get(status,0)+count
            for group,key in ((by_phase,phase),(by_origin,'raw_message' if is_message else 'assertion')):
                values=group.setdefault(key,{})
                values[status]=values.get(status,0)+count
        event_count = connection.execute('SELECT count(*) FROM memory_raw_events '
            'WHERE ? IS NULL OR scope = ?',(scope,scope)).fetchone()[0]
        assertion_count = connection.execute('SELECT count(*) FROM memory_assertions '
            'WHERE ? IS NULL OR scope = ?',(scope,scope)).fetchone()[0]
        error_count = connection.execute(f'SELECT count(*) {base} AND j.error IS NOT NULL',
                                         parameters).fetchone()[0]
        unresolved_from = base.replace(' WHERE ', ', json_each(j.proposals) p WHERE ',1)
        unresolved_count = connection.execute(f"SELECT count(*) {unresolved_from} AND "
            "json_extract(p.value,'$.status') IN ('unresolved','pending')",parameters).fetchone()[0]
        result={'job_counts':counts,'job_counts_by_phase':by_phase,'job_counts_by_origin':by_origin,
                'pending_count':sum(n for status,n in counts.items() if status!='completed'),
                'event_count':event_count,'assertion_count':assertion_count,'error_count':error_count,
                'unresolved_count':unresolved_count}
        from .source_omission import status_counts
        recoveries=status_counts(connection,scope)
        if recoveries:result['source_audit_request_counts']=recoveries
        return result

    def _status_details(self, connection, scope, offset, limit, counts, *, selection=None) -> dict:
        base, parameters = selection if selection is not None else _status_selection(scope)
        pending = connection.execute('SELECT j.id job_id,j.status,j.phase,j.attempts,j.available_at '+base+
            " AND j.status != 'completed' ORDER BY j.available_at,j.id LIMIT ? OFFSET ?",
            (*parameters,limit,offset)).fetchall()
        errors = connection.execute('SELECT j.id job_id,j.status,j.error '+base+
            ' AND j.error IS NOT NULL ORDER BY j.available_at,j.id LIMIT ? OFFSET ?',
            (*parameters,limit,offset)).fetchall()
        unresolved_from = base.replace(' WHERE ', ', json_each(j.proposals) p WHERE ',1)
        owner = ('coalesce(e.scope,a.scope)' if scope is not None else
            'coalesce((SELECT scope FROM memory_raw_events WHERE id=j.event_id), '
            '(SELECT scope FROM memory_assertions WHERE assertion_id=j.assertion_id))')
        unresolved = connection.execute(
            'SELECT j.id job_id,j.event_id,j.assertion_id,'+owner+' proposal_scope,p.value proposal '+unresolved_from+
            " AND json_extract(p.value,'$.status') IN ('unresolved','pending') "
            'ORDER BY j.available_at,j.id,CAST(p.key AS INTEGER) LIMIT ? OFFSET ?',
            (*parameters,limit,offset)).fetchall()
        from .answer_relation_gaps import digest
        safe_unresolved=[]
        for row in unresolved:
            item=dict(row)
            proposal=json.loads(item.pop('proposal'))
            owner=item.pop('proposal_scope')
            item['proposal']=(proposal if isinstance(owner,str) and owner.strip() else {'revision':digest(proposal)})
            safe_unresolved.append(item)
        pages = {'pending_jobs':[dict(row) for row in pending],'errors':[dict(row) for row in errors],
                 'unresolved':safe_unresolved}
        totals = {'pending_jobs':counts['pending_count'],'errors':counts['error_count'],
                  'unresolved':counts['unresolved_count']}
        if counts.get('source_audit_request_counts'):
            from .source_omission import status_page
            pages['source_audit_requests']=status_page(connection,scope,offset,limit)
            totals['source_audit_requests']=sum(counts['source_audit_request_counts'].values())
        pages['pagination'] = {name:{'offset':offset,'limit':limit,'total':total,
            'next_offset':offset+limit if offset+limit<total else None} for name,total in totals.items()}
        return pages


def _status_selection(scope):
    if scope is None:
        return 'FROM memory_capture_jobs j WHERE 1', ()
    return ('FROM memory_capture_jobs j LEFT JOIN memory_raw_events e ON j.event_id=e.id '
            'LEFT JOIN memory_assertions a ON j.assertion_id=a.assertion_id '
            'WHERE coalesce(e.scope,a.scope)=?',(scope,))



def _describe_inline_omissions(result, records, inline_ids):
    omitted = [row['id'] for row in records if row['id'] not in inline_ids]
    status = {**result['source_status'], 'inline_evidence_count': len(inline_ids),
              'omitted_evidence_count': len(omitted), 'omitted_source_ids': [],
              'omitted_ids_complete': True, 'omission_reason': 'persistence_failure_response_budget'}
    result = {**result, 'source_status': status}
    for identifier in omitted:
        proposed = [*result['source_status']['omitted_source_ids'], identifier]
        candidate = {**result, 'source_status': {**result['source_status'], 'omitted_source_ids': proposed}}
        if not _fits(candidate):
            result['source_status']['omitted_ids_complete'] = False
            break
        result = candidate
    return result


def _payload_fingerprint(payload: str) -> str:
    return 'sha256:' + hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _event_descriptor(event) -> dict:
    return {key: value for key, value in asdict(event).items() if key != 'prompt'} | {
        'text_characters': len(event.prompt)}


def _job_descriptor(job) -> dict:
    fields = ('id', 'event_id', 'status', 'phase', 'attempts', 'error', 'updated_at', 'available_at')
    return {key: getattr(job, key) for key in fields} | {
        'extracted_count': len(job.extracted_ids), 'review_count': len(job.proposals)}


def _request_usage(memory_db, trace_id, wall_ms):
    from .model_usage import read_usage
    report = read_usage(memory_db, trace_id=trace_id)
    result = {'status':report['status'], 'wall_ms':wall_ms}
    if report['status'] != 'available':
        return result
    fields = ('attempts','failed','in_progress','known_attempts','unknown_attempts',
              'input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens',
              'input_tokens_known_attempts','output_tokens_known_attempts',
              'duration_ms','duration_ms_known_attempts')
    stages = report['by_task'][:2]
    return {**result,'totals':{key:report['totals'][key] for key in fields},
            'stages':[{'task':row['task'],'attempts':row['attempts']} for row in stages],
            'stages_omitted_count':report['tasks_omitted']+len(report['by_task'])-len(stages),
            'duration_semantics':'Attempt durations may overlap; wall_ms measures this query.',
            'token_semantics':'Provider-reported counts; null means unknown. Cached/reasoning are subsets.'}


def _fits(value):
    text=json.dumps(value,ensure_ascii=False,indent=2)
    envelope={'content':[{'type':'text','text':text}],'structuredContent':value,'isError':False}
    reserve = QUERY_USAGE_RESERVE_CHARACTERS if current_query_trace() is not None else 0
    return len(json.dumps(envelope,ensure_ascii=False,separators=(',',':'))) + 256 <= RESPONSE_CHARACTERS-reserve


def _continuation(tool,**arguments):
    return {'tool':tool,'arguments':arguments}


def _text_page(value,tool,arguments,offset,limit,*,plain=False,envelope=None):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=12_000:
        raise ValueError('offset must be nonnegative and limit between 1 and 12000 characters')
    text = value if plain else json.dumps(value,ensure_ascii=False,separators=(',',':'))
    if offset>len(text):
        raise ValueError('offset is outside the requested section')
    end = min(offset+limit,len(text))
    while True:
        cursor = _continuation(tool,**arguments,offset=end,limit=limit) if end<len(text) else None
        result = {**(envelope or {}),**arguments,'encoding':'text' if plain else 'json','offset':offset,
                  'end':end,'total_characters':len(text),'item_count':len(value) if isinstance(value,list) else None,
                  'content':text[offset:end],'response_complete':end==len(text),'continuation':cursor}
        if _fits(result):
            return result
        if end<=offset+1:
            raise ValueError('section metadata exceeds the response budget')
        end = offset+(end-offset)//2


def _version_descriptor(value):
    fields = ('id','view_id','source_version','created_at','valid_as_of','recorded_as_of','pipeline_key')
    return {key:value[key] for key in fields} | {'summary_characters':len(value['summary']),
        'evidence_count':len(value['evidence']),'edge_count':len(value['edges']),
        'missing_root_count':len(value['missing_roots'])}


def _assertion_descriptor(assertion):
    fields = ('assertion_id','actor','source','episode','scope','valid_at','recorded_at')
    return {key:getattr(assertion,key) for key in fields} | {'text_characters':len(assertion.text)}


def _route_descriptor(row):
    request = row['plan'].get('request',{})
    return {key:row[key] for key in ('request_id','revision','view_id','recorded_at')} | {
        'method':row['plan'].get('method'),'valid_as_of':request.get('valid_as_of'),
        'recorded_as_of':request.get('recorded_as_of')}


def _bounded_status(result,scope,offset):
    names = tuple(name for name in ('pending_jobs','errors','unresolved','source_audit_requests','thought_pending_jobs','thought_errors') if name in result)
    while True:
        result=_diagnostic_pagination(result,scope,offset)
        if _fits(result):
            return result
        removable = [name for name in names if len(result[name])>1]
        if not removable:
            directory = result.get('derived_overviews')
            if directory and len(directory['entries']) > 1:
                directory['entries'].pop()
                derived_directory.repaginate(directory, scope)
                continue
            return _diagnostic_descriptors(result,scope,offset)
        largest = max(removable,key=lambda name:len(json.dumps(result[name],ensure_ascii=False)))
        result[largest].pop()


def _diagnostic_pagination(result,scope,offset):
    directory = result.get('derived_overviews', {})
    cursors = [directory['continuation']] if directory.get('continuation') else []
    for name,info in result['pagination'].items():
        length=len(result[name])
        next_offset=offset+length if offset+length<info['total'] else None
        actual_limit=length if next_offset is not None and length<info['limit'] else info['limit']
        info.update(limit=actual_limit,next_offset=next_offset)
        if next_offset is not None:
            cursor=_continuation('memory_status',scope=scope,offset=next_offset,limit=100)
            if cursor not in cursors:
                cursors.append(cursor)
    result['response_complete']=not cursors
    if cursors:
        result['continuations']=cursors
    else:
        result.pop('continuations',None)
    return result


def _diagnostic_descriptors(result,scope,offset):
    cursors=[]
    for name in ('pending_jobs','errors','unresolved','thought_pending_jobs','thought_errors'):
        if name not in result:continue
        compact=[]
        for row in result[name]:
            job_id=row['job_id']
            cursor=_continuation('memory_status',event_id=job_id,scope=scope,section='job',offset=0,limit=6000)
            compact.append({'job_id':job_id,'details_inline':False,'continuation':cursor})
            if cursor not in cursors:
                cursors.append(cursor)
        result[name]=compact
    if 'job' in result:
        job=result['job']
        cursors.append(_continuation('memory_status',event_id=job['id'],scope=scope,section='job',offset=0,limit=6000))
        result['job']={k:job[k] for k in ('id','event_id','assertion_id','status','phase','attempts')}
    result=_diagnostic_pagination(result,scope,offset)
    result['response_complete']=False
    result.setdefault('continuations',[]).extend(cursors)
    if not _fits(result):
        for name in ('pending_jobs', 'errors', 'unresolved','thought_pending_jobs','thought_errors'):
            for row in result.get(name,[]):
                row.pop('continuation', None)
                row['details_in_continuations'] = True
    directory = result.get('derived_overviews')
    while not _fits(result) and directory and directory['entries']:
        directory['entries'].pop()
        derived_directory.repaginate(directory, scope)
        result = _diagnostic_pagination(result, scope, offset)
        result['response_complete'] = False
        result.setdefault('continuations', []).extend(cursors)
    return result


def _compact_event(event):
    result=_event_descriptor(event)
    result.pop('metadata',None)
    result['metadata_fields']=len(event.metadata)
    result['role']=event.metadata.get('role','assistant' if event.actor=='assistant' else 'user')
    return result


def _check_descriptor(check):
    if check is None:
        return None
    fields = ('id','view_id','version_id','source_version','coverage_version','valid_as_of',
              'recorded_as_of','pipeline_key','input_fingerprint','checked_at','query_snapshot','freshness')
    review = check.get('overview_review') or {}
    return {**{key:check[key] for key in fields},
            'review_status':review.get('status'),'corpus_revision':review.get('corpus_revision'),
            'audit':_continuation('memory_history',view_id=check['view_id'],version_id=check['version_id'],
                                 section='checks',snapshot_id=check['id'],offset=0,limit=PAGE_CHARACTERS)}
