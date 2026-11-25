"""Leased canonical intake and separate coalesced thought consolidation."""
from __future__ import annotations
import fcntl
import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from time import sleep
from uuid import uuid4

from .processing_control import paused as processing_paused
from .queue_owner_actions import priority as owner_priority, consume_priority
from .model import format_prompt
from .diagnostic_privacy import diagnostic_scope, restricted, ProtectedContextError
from .queue import _Claim, _event, LeaseLost
from .thought_store import ThoughtConflict, publication_guard, same_meaning_version
from .thought_worker_queue import ThoughtWorkQueue
from .thought_answering import can_refresh, refresh_thought
from .source_extraction import (CanonicalSourceExtractor, SourceExtractorFactory,
    ClaimBoundSourceExtractorFactory, SourceExtractionRequest)
from .thought_processing_selection import (normalize_session_ids,source_predicate,processing_cohort,
    canonical_session_id,processing_deadline)


class _Replay:
    """Persist consumed steps, keeping an unvalidated final reply provisional."""
    def __init__(self, provider, cached, save, renew, identity, invalidate=None, *, on_reuse=None):
        self.provider, self.cached, self.save, self.renew = provider, cached, save, renew
        self.identity, self.pending, self.seen = identity, None, set()
        self.steps,self.invalidate=[],invalidate
        self.on_reuse=on_reuse
        self.prefix = 'thought-replay:' + hashlib.sha256(json.dumps(identity[:2],sort_keys=True).encode()).hexdigest() + ':'

    def __call__(self, request):
        if restricted():
            # Never persist/reuse model bodies influenced by foreign context.
            self.pending=None
            self.renew()
            return self.provider(request)
        key = self.prefix + hashlib.sha256(json.dumps(
            [self.identity, format_prompt(request), request.cache_context], sort_keys=True).encode()).hexdigest()
        if self.pending is not None and self.pending[0] == key:
            self.pending = None
        else:
            self.confirm()
        body = None if key in self.seen else self.cached(key)
        self.seen.add(key)
        self.steps.append((key,request.task))
        self.renew()
        if body is not None:
            if self.on_reuse is not None:
                self.on_reuse(request,body,key)
            return body
        body = self.provider(request)
        if isinstance(body, str) and body.strip():
            self.pending = (key, body)
        return body

    def reject_construct(self, *, source_batch=False):
        constructs=[index for index,(_,task) in enumerate(self.steps) if task=='Construct thought']
        if not constructs:
            self.discard_provisional()
            return
        start=constructs[0] if source_batch else constructs[-1]
        keys={key for key,_ in self.steps[start:]}
        self.pending=None
        if self.invalidate is not None:self.invalidate(keys)
        self.seen.difference_update(keys)
        self.steps=self.steps[:start]

    def discard_provisional(self):
        self.pending = None

    def confirm(self):
        if restricted():
            self.pending=None
            return
        if self.pending is not None:
            self.save(*self.pending)
            self.pending = None

    def clear(self, connection, table):
        if table not in ('memory_refresh_generations','memory_thought_generations'):
            raise ValueError('unknown semantic replay cache')
        connection.execute('DELETE FROM '+table+' WHERE request_key LIKE ?', (self.prefix+'%',))


class _SourceAttemptLease:
    """In-process check only; cannot renew, publish, or return a database handle."""
    def __init__(self, queue, claim):
        import threading
        self._owner = threading.get_ident()
        self._queue, self._claim = queue, claim
        self._open = True

    def ensure_active(self):
        import threading
        if not self._open or self._owner != threading.get_ident():
            raise LeaseLost('source extraction capability closed or on the wrong thread')
        with self._queue.store.transaction() as db:
            self._queue._assert_owned(db, self._claim)

    def check_reader_snapshot(self, db, path):
        """Trusted holder-side check for a separate query-only connection."""
        from pathlib import Path
        if (not self._open or self._queue.store.path == ':memory:' or
                Path(path).resolve() != Path(self._queue.store.path).resolve()):
            raise LeaseLost('source extraction reader is outside its source store')
        self._queue._assert_owned(db, self._claim)
        source=db.execute('SELECT event_id,status FROM memory_capture_jobs WHERE id=?',
                          (self._claim.job.id,)).fetchone()
        if source is None or source[0]!=self._claim.job.event_id or source[1]!='running':
            raise LeaseLost('source extraction no longer owns a running source')

    def close(self):
        self._open = False


class ThoughtWorker:
    def __init__(self, queue, engine, *, lane='all', lock_owned=False,
                 claim_observer=None, context_limit=8, context_characters=12000, processing_session_ids=None,
                 is_pausing=None, source_extractor_factory: SourceExtractorFactory = CanonicalSourceExtractor,
                 claim_bound_source_extractor_factory: ClaimBoundSourceExtractorFactory | None = None):
        if engine.store is not queue.store:
            raise ValueError('thought worker and raw queue must share a MemoryStore')
        if lane not in ('all', 'source', 'maintenance') or type(lock_owned) is not bool:
            raise ValueError('valid thought worker lane and lock ownership required')
        if any(type(value) is not int or value < 1 for value in (context_limit, context_characters)):
            raise ValueError('positive bounded context limits required')
        self.queue, self.engine, self.model = queue, engine, engine.model
        self.lane, self.lock_owned, self.claim_observer = lane, lock_owned, claim_observer
        self.context_limit, self.context_characters = context_limit, context_characters
        self.processing_session_ids = normalize_session_ids(processing_session_ids)
        self.work = ThoughtWorkQueue(queue,self.processing_session_ids)
        self.is_paused = False
        self.is_pausing = is_pausing
        if (claim_bound_source_extractor_factory is not None and
                (not callable(claim_bound_source_extractor_factory) or
                 source_extractor_factory is not CanonicalSourceExtractor)):
            raise ValueError('provide exactly one custom source extractor factory')
        self.source_extractor_factory = source_extractor_factory
        self.claim_bound_source_extractor_factory = claim_bound_source_extractor_factory

    @contextmanager
    def _lock(self):
        if self.lock_owned or self.queue.store.path == ':memory:':
            yield True
            return
        path = Path(self.queue.store.path + '.worker.lock')
        with path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _notify(self, event, kind, identity, token):
        if self.claim_observer is not None:
            self.claim_observer(event, kind, identity, token)

    def run_once(self):
        if self.engine.model is None and self.engine.gateway is None:
            return None
        with self._lock() as owned:
            self.is_paused = not owned
            if not owned:
                return None
            if self.lane != 'maintenance':
                claim = self._claim_source()
                if claim is not None:
                    return self._source(claim)
            if self.lane != 'source':
                task = self.work.claim(is_pausing=self.is_pausing)
                if task is not None:
                    return self._consolidate(task)
        return None

    def _claim_source(self, *, event_id=None):
        with self.queue.store.transaction(write=True) as c:
            # SQLite write admission may have waited past the pause request.
            if processing_paused(c) or (self.is_pausing is not None and self.is_pausing()):
                return None
            now = self.queue._time()
            eligible,args = source_predicate(self.processing_session_ids,'e')
            if event_id is not None:
                eligible += ' AND e.id=?'
                args = [*args, event_id]
            if self.claim_bound_source_extractor_factory is None:
                c.execute("UPDATE memory_capture_jobs SET status='failed',lease_token=NULL,lease_until=NULL,"
                    "error='Thought source lease expired too many times' WHERE event_id IS NOT NULL "
                    "AND status='running' AND lease_until<=? AND attempts>=? AND EXISTS(SELECT 1 FROM memory_raw_events e "
                    'WHERE e.id=memory_capture_jobs.event_id AND '+eligible+')',(now,self.queue._max_attempts,*args))
            priority = owner_priority(c,'source','j.id','j.updated_at','j.status')
            row = c.execute("SELECT j.id FROM memory_capture_jobs j JOIN memory_raw_events e ON e.id=j.event_id "
                "WHERE ((j.status='pending' AND j.available_at<=?) OR (j.status='running' AND j.lease_until<=?) "
                "OR j.status='waiting_model') AND "+eligible+f" ORDER BY {priority} DESC,j.available_at,j.id LIMIT 1",(now,now,*args)).fetchone()
            if row is None:
                return None
            token = uuid4().hex
            c.execute("UPDATE memory_capture_jobs SET status='running',attempts=attempts+1,"
                "lease_token=?,lease_until=?,updated_at=? WHERE id=?",
                (token, self.queue._time(self.queue._lease_seconds), now, row[0]))
            consume_priority(c,'source',row[0])
            return _Claim(self.queue.get(row[0]), token)

    def _context(self, event):
        with self.queue.store.transaction() as c:
            target = c.execute('SELECT e.rowid,p.position FROM memory_raw_events e '
                'LEFT JOIN memory_event_positions p ON p.event_id=e.id WHERE e.id=?', (event.id,)).fetchone()
            column = 'e.rowid' if target['position'] is None else 'p.position'
            cutoff = target['rowid'] if target['position'] is None else target['position']
            from .thought_source_policy import eligible_sql
            eligibility, arguments = eligible_sql('e')
            session, session_arguments = source_predicate((canonical_session_id(event),), 'e')
            rows = c.execute(f'SELECT e.*,j.status,{column} AS context_position FROM memory_raw_events e JOIN memory_capture_jobs j ON j.event_id=e.id '
                'LEFT JOIN memory_event_positions p ON p.event_id=e.id WHERE e.session_id=? AND e.scope=? '
                f'AND {column}<? AND {session} AND {eligibility} ORDER BY {column} DESC,e.rowid DESC LIMIT ?',
                (event.session_id, event.scope, cutoff, *session_arguments, *arguments, self.context_limit + 1)).fetchall()
        context, omitted, size = [], [], 0
        for row in rows[:self.context_limit]:
            reason = ('unverified_origin' if row['status'] == 'waiting_origin' else
                      'character_budget' if size + len(row['prompt']) > self.context_characters else None)
            if reason is not None:
                omitted.append({'event_id': row['id'], 'reason': reason})
                continue
            context.append(_event(row))
            size += len(row['prompt'])
        overflow = rows[self.context_limit:]
        omitted.extend({'event_id': row['id'], 'reason': 'event_budget'} for row in overflow)
        context.reverse()
        emit = getattr(self.engine, '_emit', None)
        if emit is not None:
            emit('worker_context', event_id=event.id, selected=[{'event_id': row.id} for row in context],
                omitted=omitted, earlier_uninspected=bool(overflow), complete_context=not omitted,
                boundary={'order': column, 'before': cutoff,
                          'oldest_examined': rows[-1]['context_position'] if rows else None},
                limits={'events': self.context_limit, 'characters': self.context_characters},
                source_policy='codex-visible-dialogue-only-v1', excluded_source_kinds='automatic tools/private channels filtered before budget')
        return context

    @contextmanager
    def _processing(self, identity, guard, renew, cached, save, *, on_publish=None):
        original = self.engine.gateway
        provider = self.engine.model
        def invoke(request):
            bindings = json.loads(request.cache_context)['bindings']
            return original(request.task, request, tuple(bindings)) if original else provider(request)
        def invalidate(keys):
            with self.queue.store.transaction(write=True) as c:
                guard(c)
                for table in ('memory_refresh_generations','memory_thought_generations'):
                    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                        c.execute('DELETE FROM '+table+' WHERE request_key IN (SELECT value FROM json_each(?))',(json.dumps(sorted(keys)),))
        def reused(request,body,key):
            self.engine._emit('model_step_reused',source_call_id=None,request_key=key,
                request_hash=hashlib.sha256(format_prompt(request).encode()).hexdigest(),
                response_hash=hashlib.sha256(body.encode()).hexdigest(),
                cached_bindings=json.loads(request.cache_context)['bindings'])
        replay = _Replay(invoke, cached, save, renew, identity, invalidate, on_reuse=reused)
        def gateway(task, request, bindings):
            context = {'bindings': bindings, 'original': request.cache_context}
            return replay(replace(request, cache_context=json.dumps(context, sort_keys=True)))
        gateway.discard_provisional = replay.discard_provisional
        gateway.reject_construct = replay.reject_construct
        self.engine.gateway = gateway
        try:
            with publication_guard(guard, on_publish=on_publish):
                yield replay
        finally:
            self.engine.gateway = original

    def _source(self, claim):
        event = self.queue.get_event(claim.job.event_id)
        sessions = (canonical_session_id(event),) if event is not None else None
        with processing_cohort(sessions), diagnostic_scope():
            return self._source_owned(claim)

    def _source_owned(self, claim):
        self._notify('claimed', 'source', claim.job.id, claim.token)
        try:
            event = self.queue.get_event(claim.job.event_id)
            from .thought_source_policy import event_eligibility
            eligibility = event_eligibility(event)
            if not eligibility['allowed']:
                with self.queue.store.transaction(write=True) as c:
                    self.queue._assert_owned(c, claim)
                    c.execute("UPDATE memory_capture_jobs SET status='completed',phase='excluded',error=?,lease_token=NULL,lease_until=NULL WHERE id=?",
                        (eligibility['policy'] + ': ' + eligibility['reason'], claim.job.id))
                return self.queue.get(claim.job.id)
            resumed = claim.job.phase == 'partial_source'
            context = [] if resumed or self.claim_bound_source_extractor_factory is not None else self._context(event)
            with self._processing(('source-v1', event.id, [row.id for row in context]),
                lambda c: self.queue._assert_owned(c, claim), lambda: self.queue.renew_claim(claim),
                self.queue._refresh_generation, lambda key, body: self.queue._save_refresh_generation(claim, key, body)) as replay:
                self._process_source_claim(claim, event, context, replay, resumed=resumed)
            return self.queue.get(claim.job.id)
        except LeaseLost:
            return self.queue.get(claim.job.id)
        except Exception as error:
            try:
                return self.queue._fail(claim, ProtectedContextError() if restricted() else error,
                    retry_without_limit=self.claim_bound_source_extractor_factory is not None)
            except LeaseLost:
                return self.queue.get(claim.job.id)
        finally:
            self._notify('released', 'source', claim.job.id, claim.token)

    @contextmanager
    def _source_extractor(self, event, claim):
        bind = self.claim_bound_source_extractor_factory
        if bind is None:
            yield self.source_extractor_factory(self.engine)
            return
        if claim.job.event_id != event.id:
            raise LeaseLost('source extraction request does not belong to this claim')
        lease = _SourceAttemptLease(self.queue, claim)
        try:
            lease.ensure_active()
            request = SourceExtractionRequest(event.id, event.scope, event.session_id,
                                              canonical_session_id(event), lease)
            yield bind(self.engine, request)
            lease.ensure_active()
        finally:
            lease.close()

    def _process_source_claim(self, claim, event, context, replay, *, resumed):
        from .thought_engine import SourceDraftRepairPending
        carried, prepared, changed, pending = [], [], [], None
        try:
            if resumed:
                context, carried, feedback = self._load_source_draft_checkpoint(claim)
                with self._source_extractor(event, claim) as extractor:
                    prepared = extractor.resume(event, context, carried, feedback)
            else:
                _, event_id, origin, _, _ = self.engine._source_packet(event)
                changed = self.engine.reusable_source_thoughts(event_id, origin)
                if not changed:
                    with self._source_extractor(event, claim) as extractor:
                        drafts = extractor.extract(event, context)
                    if self.claim_bound_source_extractor_factory is not None:
                        preflight=getattr(extractor,'publication_preflight',None)
                        if preflight is not None:preflight()
                        check=getattr(extractor,'publication_check',None)
                        def final_check(db):
                            self.queue._assert_owned(db,claim)
                            if check is not None:check(db)
                        with publication_guard(final_check):
                            changed=self.engine._publish(drafts,origin=origin,rebuild=False)
                    else:
                        changed=self.engine._publish(drafts,origin=origin)
        except SourceDraftRepairPending as failure:
            if restricted():
                raise ProtectedContextError() from None
            pending = failure
            prepared = failure.accepted
        with self.queue.store.transaction(write=True) as c:
            self.queue._assert_owned(c, claim)
            if any(self.engine.thoughts.get(row.id) != row for row in carried):
                raise RuntimeError('An accepted source thought changed during its residual review')
            extra = self.engine.publish_source_drafts(event, prepared, covered=carried) if prepared else []
            changed.extend(extra)
            ids = [row.id for row in [*carried, *changed]]
            self.enqueue_changed(changed)
            if pending is not None:
                progress = {'context_ids': [row.id for row in context], 'feedback': pending.feedback,
                            'accepted': [self.engine._basis(row) for row in [*carried, *changed]]}
                c.execute('INSERT INTO memory_source_draft_checkpoints(job_id,progress) VALUES (?,?) '
                          'ON CONFLICT(job_id) DO UPDATE SET progress=excluded.progress',
                          (claim.job.id, json.dumps(progress)))
                c.execute("UPDATE memory_capture_jobs SET phase='partial_source',extracted_ids=? WHERE id=?",
                          (json.dumps(ids), claim.job.id))
                self.queue._fail(claim, pending,
                    retry_without_limit=self.claim_bound_source_extractor_factory is not None)
            else:
                replay.confirm()
                c.execute("UPDATE memory_capture_jobs SET status='completed',phase='done',error=NULL,"
                    'lease_token=NULL,lease_until=NULL,extracted_ids=?,updated_at=?,available_at=? WHERE id=?',
                    (json.dumps(ids), self.queue._time(), self.queue._time(), claim.job.id))
                replay.clear(c,'memory_refresh_generations')
                if resumed:
                    c.execute('DELETE FROM memory_source_draft_checkpoints WHERE job_id=?', (claim.job.id,))
        for row in extra:
            self.engine._emit('publication', thought=self.engine._basis(row), bases=row.bases)

    def _load_source_draft_checkpoint(self, claim):
        with self.queue.store.transaction() as c:
            saved = c.execute('SELECT progress FROM memory_source_draft_checkpoints WHERE job_id=?',
                              (claim.job.id,)).fetchone()
        if saved is None:
            raise RuntimeError('Pending source draft repair has no durable checkpoint')
        checkpoint = json.loads(saved[0])
        if [ref['thought_id'] for ref in checkpoint['accepted']] != claim.job.extracted_ids:
            raise RuntimeError('Pending source draft repair lost its accepted thought references')
        context = [self.queue.get_event(event_id) for event_id in checkpoint['context_ids']]
        carried = [self.engine.thoughts.get(thought_id) for thought_id in claim.job.extracted_ids]
        if any(row is None for row in context) or any(
                row is None or row.version != ref['version'] or row.origin.get('event_id') != claim.job.event_id
                for row, ref in zip(carried, checkpoint['accepted'])):
            raise RuntimeError('A pinned source draft repair basis or accepted thought changed')
        return context, carried, checkpoint['feedback']

    def enqueue_changed(self, changed):
        changed = list(changed)
        self.work.enqueue(changed)

    def enqueue_dependents(self, changed, *, retired=False):
        for child in changed:
            cause = (child.id, child.version, 'retired') if retired else (child.id, child.version)
            after_id = ''
            while True:
                parents = self.engine.thoughts.direct_dependents(child.id, limit=100, after_id=after_id)
                for parent in parents:
                    if parent.id != child.id and can_refresh(self.engine, parent) and any(
                            basis.get('thought_id') == child.id and self._basis_changed(basis, child, retired)
                            for basis in parent.bases):
                        self.work.enqueue_refresh(parent, cause)
                if len(parents) < 100:
                    break
                after_id = parents[-1].id

    def _basis_changed(self, basis, child, retired=False):
        if retired:
            return True
        if basis['version'] == child.version:
            return False
        previous = self.engine.thoughts.get(child.id, basis['version'])
        return not same_meaning_version(previous, child)

    def _refresh_inputs(self, task, current):
        selected = {}
        needs_refresh = False
        versions = {(basis['thought_id'], basis['version']) for basis in current.bases if 'thought_id' in basis}
        for cause in self.work.causes(task):
            if not isinstance(cause, list) or len(cause) < 3:
                continue
            kind, identity, version = cause[:3]
            if identity == current.id:
                continue
            if kind == 'basis':
                child = self.engine.thoughts.get(identity)
                for key, pinned_version in versions:
                    if key == identity and (len(cause) > 3 or
                            (child is not None and pinned_version < version
                             and self._basis_changed({'version':pinned_version}, child))):
                        needs_refresh = True
            elif kind == 'relevance' and (identity, version) not in versions:
                thought = self.engine.thoughts.get(identity)
                if thought is not None and thought.version == version:
                    selected[identity] = thought
        return needs_refresh or bool(selected), list(selected.values())

    def _generalize(self, current, task):
        def on_affected(parents):
            with self.queue.store.transaction(write=True) as connection:
                self.work.owned(connection, task)
                if self.engine.thoughts.get(current.id) != current or any(
                        self.engine.thoughts.get(parent.id) != parent for parent in parents):
                    raise ThoughtConflict('A thought changed during maintenance relevance review')
                self.enqueue_dependents([current])
                for parent in parents:
                    self.work.request(parent, 'refresh', ('relevance', current.id, current.version))
        return self.engine.consolidate([current], on_affected=on_affected)

    def _consolidate(self, task):
        with processing_cohort(task.get('processing_session_ids')):
            return self._consolidate_owned(task)

    def _consolidate_owned(self, task):
        identity, token = task['thought_id'], task['lease_token']
        self._notify('claimed', 'thought', identity, token)
        try:
            current = self.engine.thoughts.get(identity)
            if current is None:
                return self.work.finish(task, task['version'])
            is_refresh = task['action'] == 'refresh'

            def queue_followup(previous, published):
                # A retry can see this head as current before the claim is finished.
                if is_refresh:
                    if previous == current and not same_meaning_version(previous, published):
                        self.enqueue_dependents([published])
                elif previous is None and published.origin.get('kind') == 'inference':
                    self.enqueue_changed([published])

            with self._processing(('change-v2', identity, current.version, task['generation'], task['action']),
                lambda c: self.work.owned(c, task), lambda: self.work.renew(task), self.work.cached,
                lambda key, body: self.work.save(task, key, body), on_publish=queue_followup) as replay:
                processed_version = current.version
                if is_refresh:
                    needed, additional = self._refresh_inputs(task, current)
                    result = refresh_thought(self.engine, current, additional=additional) if needed else current
                    if result.id == current.id:
                        processed_version = result.version
                else:
                    self._generalize(current, task)
                replay.confirm()
                with self.queue.store.transaction(write=True) as c:
                    self.work.owned(c, task)
                    replay.clear(c,'memory_thought_generations')
                    return self.work.finish(task, processed_version)
        except LeaseLost:
            return self.work.get(identity)
        except Exception as error:
            try:
                return self.work.fail(task, error)
            except LeaseLost:
                return self.work.get(identity)
        finally:
            self._notify('released', 'thought', identity, token)

    def seconds_until_work(self, *, with_model=True):
        if not with_model and self.engine.gateway is None:
            return None
        with self.queue.store.transaction() as c:
            deadline = processing_deadline(c,self.processing_session_ids,now=self.queue._time(),
                with_model=with_model or self.engine.gateway is not None,lane=self.lane)
        return max(0.0,(datetime.fromisoformat(deadline)-datetime.fromisoformat(self.queue._time())).total_seconds()) if deadline else None

    def drain(self, limit=100, *, wait_for_retries=False):
        if type(limit) is not int or limit < 0:
            raise ValueError('drain limit must be nonnegative')
        results = []
        while len(results) < limit:
            result = self.run_once()
            if result is not None:
                results.append(result)
                continue
            delay = self.seconds_until_work(with_model=self.engine.model is not None)
            if self.is_paused or not wait_for_retries or delay is None:
                break
            sleep(min(1, max(.05, delay)))
        return results
