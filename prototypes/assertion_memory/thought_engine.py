"""One semantic construction, verification and publication path for all thoughts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass

from .dialogue import source_role
from .source_message_clock import normalize as normalize_clock, projection as clock_projection
from .storage import _timestamp
from .model import CandidateSearch, ModelError, ModelRequest, SemanticUnavailable, format_prompt
from .thought_source_policy import event_eligibility, thought_eligibility
from .thought_engine_inputs import RAW_CHUNK_CHARACTERS, paragraph_chunks
from .thought_engine_contract import (APPLICABILITY, construction_instructions, GENERALIZE, GROUNDING,
    MAX_THOUGHTS, PROTOCOL, SOURCE_BATCH, VERIFY, WHOLE_REFRESH, exact_fields, object_response, semantic_text,
    thought_drafts, validate_response, SupportQuoteMissing, RAW_PASSAGE, SemanticReviewRejected)
from .thought_schema import response_schema
from .thought_publication import UNRESOLVED as PARALLEL_ALTERNATIVE_NOTICE


class ThoughtDetailNeeded(SemanticUnavailable):
    def __init__(self, question, reason):
        self.question = question
        self.reason = reason
        super().__init__('Additional supporting detail is needed: ' + question + '. ' + reason)


def empty_material(reason):
    error = SemanticUnavailable(reason)
    error.empty_ground = True
    return error


class ThoughtEngine:
    def __init__(self, thought_store, model, candidates=None, trace=None, *, gateway=None):
        self.thoughts = thought_store
        self.store = thought_store.store
        self.model = model
        self.candidates = candidates or CandidateSearch()
        self.trace = trace
        self.gateway = gateway
        with self.store.transaction(write=True) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS memory_thought_reuse_checks '
                '(thought_id TEXT NOT NULL, version INTEGER NOT NULL, question TEXT NOT NULL, '
                'frontier TEXT NOT NULL, PRIMARY KEY(thought_id,version,question))')

    def _emit(self, stage, **payload):
        if self.trace is not None:
            self.trace({'stage': stage, **payload})

    def _call(self, task, records, bindings, instructions, *, support_count=None, single_thought=False):
        if self.model is None and self.gateway is None:
            raise ModelError('unified thought processing requires a semantic model')
        request = ModelRequest(task, records, instructions, usage_task=task,
            response_schema=response_schema(task,support_count=support_count,single_thought=single_thought))
        frozen = tuple(dict(basis) for basis in bindings)
        wire=format_prompt(request)
        request_hash=hashlib.sha256(wire.encode()).hexdigest()
        self._emit('model_input', task=task, bindings=frozen, records=records,
                   serialized_chars=len(wire),request_hash=request_hash)
        for attempt in range(2):
            body = (self.gateway(task, request, frozen) if self.gateway is not None
                    else self.model(request))
            if not isinstance(body, str):
                raise TypeError('thought model must return serialized JSON text')
            try:
                result = object_response(body)
                validate_response(task, result, support_count=support_count,single_thought=single_thought)
            except ModelError as malformed:
                self._discard_provisional()
                self._emit('model_contract_repair', task=task, attempt=attempt + 1, reason=str(malformed),
                           request_hash=request_hash,response_hash=hashlib.sha256(body.encode()).hexdigest())
                if attempt:
                    failure = ModelError('Thought model response contract failed twice at ' + task)
                    failure.recoverable = True
                    failure.stage = task
                    raise failure from malformed
                continue
            self._emit('model_output', task=task, output=result,request_hash=request_hash,
                       response_hash=hashlib.sha256(body.encode()).hexdigest(),verdict=result.get('verdict'),
                       has_draft_batch=(isinstance(result.get('thoughts'),list) or isinstance(result.get('thought'),dict))
                           and result.get('needs_detail') is None)
            return result

    def _discard_provisional(self):
        discard = getattr(self.gateway, 'discard_provisional', None)
        if discard is None:
            discard = getattr(self.model, 'discard_provisional', None)
        if discard is not None:
            discard()

    def _reject_construct_checkpoint(self, *, source_batch=False):
        reject=getattr(self.gateway,'reject_construct',None) or getattr(self.model,'reject_construct',None)
        if reject is not None:
            reject(source_batch=source_batch)
            return True
        self._discard_provisional()
        return False

    @staticmethod
    def _basis(thought):
        return {'thought_id': thought.id, 'version': thought.version}

    @staticmethod
    def _is_parallel_alternative(thought):
        return thought.metadata.get('publication', {}).get('state') == 'parallel_alternative'

    def question_texts(self, thought):
        return self.thoughts.questions(thought)

    def _content(self, thought):
        return {'text': thought.text, 'questions': self.question_texts(thought),
                'origin': ThoughtEngine._model_origin(thought.origin),
                'verification': thought.metadata.get('verification', 'unknown'),
                'qualifications': {**({'parallel_alternative': PARALLEL_ALTERNATIVE_NOTICE}
                    if thought.metadata.get('publication', {}).get('state') == 'parallel_alternative' else {}),
                    **{key: thought.metadata[key] for key in
                    ('conditions', 'exceptions', 'conflicts', 'uncertainty', 'limitations', 'known_gaps')
                    if key in thought.metadata}},
                'details': [basis['meaning'] for basis in thought.bases
                            if 'thought_id' in basis and basis.get('meaning')]}

    def retrieve(self, question, explicit_ids=(), limit=10, *, exclude_ids=()):
        question = semantic_text(question, 'question')
        if type(limit) is not int or limit < 1 or limit > MAX_THOUGHTS:
            raise ValueError('thought retrieval limit must be between one and ten')
        from .thought_questions import retrieve
        return retrieve(self, question, explicit_ids, limit, exclude_ids)

    @staticmethod
    def _frontier(candidates, thought_id=None, raw_records=(), raw_bases=()):
        refs = sorted((row.id, row.version) for row in candidates if row.id != thought_id)
        pending = [(binding, hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
                   for record, binding in zip(raw_records, raw_bases)]
        payload = ({'thoughts': refs, 'pending_raw': sorted(pending, key=lambda row: json.dumps(row[0], sort_keys=True))}
                   if pending else refs)
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def _checked(self, thought, question, frontier, *, allow_unrecorded=True):
        if self.thoughts.question_checked(thought, question, frontier):
            return True
        with self.store.transaction() as connection:
            row = connection.execute('SELECT frontier FROM memory_thought_reuse_checks '
                'WHERE thought_id=? AND version=? AND question=?',
                (thought.id, thought.version, question)).fetchone()
        return row is None and allow_unrecorded and thought.metadata.get('protocol') != PROTOCOL

    def _save_check(self, thought, question, frontier):
        self.thoughts.save_question_check(thought, question, frontier)

    def _fresh(self, thought):
        head = self.thoughts.get(thought.id)
        if head is None or head.version != thought.version:
            return False
        pending = [thought]
        visited = set()
        while pending:
            current = pending.pop()
            marker = (current.id, current.version)
            if marker in visited:
                continue
            visited.add(marker)
            for basis in current.bases:
                if 'thought_id' not in basis:
                    continue
                historical_self = basis['thought_id'] == current.id
                latest = self.thoughts.get(basis['thought_id'], basis['version'] if historical_self else None)
                if latest is None or latest.version != basis['version']:
                    return False
                pending.append(latest)
        return True

    def _verify(self, drafts, records, bases, origin=None, *, source_batch=False, support_catalog=None, support_selections=None):
        whole = any(record.get('role') == 'whole_refresh_scope' for record in records)
        raw = not whole and (source_batch or any(record.get('role') == 'source' for record in records))
        if support_catalog is None or support_selections is None:
            raise ModelError('verification requires program-bound prepared support selections')
        packet=support_catalog.selected_packet(support_selections)
        review = self._call('Verify source thought batch' if source_batch else 'Verify thought', [*packet,
            *[{'role': 'draft', **draft, 'support_fragments':support_selections[index],
               'origin': self._model_origin(origin or {'kind': 'inference'})} for index,draft in enumerate(drafts)]],
            bases, VERIFY + (RAW_PASSAGE if raw else '') + (SOURCE_BATCH if source_batch else '') + (WHOLE_REFRESH if whole else ''))
        exact_fields(review, {'verdict', 'text'})
        if review.get('verdict') not in ('accept', 'reject'):
            raise ModelError('thought verifier must return an explicit whole-batch verdict')
        semantic_text(review.get('text'))
        if review['verdict'] != 'accept':
            failure=SemanticReviewRejected(review['text'])
            failure.checkpoint_invalidated=self._reject_construct_checkpoint(source_batch=source_batch)
            raise failure

    def _resolve_support(self, draft, catalog):
        return catalog.resolve(draft['support_fragments'])

    def _prepare(self, records, bases, *, question=None, origin=None, review_ambiguity=True):
        correction = ''
        for attempt in range(2):
            try:
                return self._prepare_once(records, bases, question=question, origin=origin,
                                          review_ambiguity=review_ambiguity, correction=correction)
            except (SupportQuoteMissing,SemanticReviewRejected) as failure:
                if isinstance(failure,SupportQuoteMissing):
                    self._reject_construct_checkpoint()
                    reason='A support quotation did not occur in the unchanged pinned material'
                else:
                    reason=failure.reason
                self._emit('support_quote_repair' if isinstance(failure,SupportQuoteMissing) else 'semantic_construct_repair',
                    attempt=attempt+1,reason=reason)
                if attempt:
                    failure.recoverable=True
                    failure.stage='Construct thought'
                    raise
                correction=('\nRevise the rejected construction against the SAME pinned material. '
                    'Verifier feedback: '+reason+' Preserve the applicable coverage purpose, conditions, '
                    'attribution and uncertainty. Select only the provided prepared support fragments; never write quotations or addresses.')

    def _prepare_once(self, records, bases, *, question=None, origin=None,
                      review_ambiguity=True, correction=''):
        from .thought_support import prepare_support
        catalog=prepare_support(records,bases)
        if not catalog.fragments:
            raise empty_material('The retrieved material contains no admitted full supporting content')
        whole = any(record.get('role') == 'whole_refresh_scope' for record in records)
        single_thought = question is not None or whole
        instructions = construction_instructions(single_thought=single_thought) + correction
        request_records = catalog.wire
        if whole:
            instructions += WHOLE_REFRESH
        elif any(record.get('role')=='source' for record in records):
            instructions += RAW_PASSAGE
        if question is not None:
            instructions += '\nProduce exactly one useful answer to the supplied question.'
            request_records = [{'role': 'question', 'text': question}, *catalog.wire]
        response = self._call('Construct thought', request_records, bases, instructions,
            support_count=len(catalog.fragments),single_thought=single_thought)
        detail = response.get('needs_detail')
        if response.get('verdict') == 'needs_detail':
            detail = {key: response.get(key) for key in ('question', 'text')}
        if detail is not None:
            exact_fields(detail, {'question', 'text'})
            raise ThoughtDetailNeeded(semantic_text(detail.get('question'), 'missing question'),
                                      semantic_text(detail.get('text')))
        drafts = thought_drafts(response, support_count=len(catalog.fragments),single_thought=single_thought)
        verified, selections = [], []
        primary_ids = {basis.get('event_id') for record, basis in zip(records, bases)
                       if record.get('role') == 'source'}
        for supported_draft in drafts:
            draft = {key: supported_draft[key] for key in ('text', 'question')}
            if question is not None or whole:
                draft['question'] = question if not whole else None
            grounds, actual_bases = self._resolve_support(supported_draft, catalog)
            if primary_ids and not any(record.get('role') == 'source' for record in grounds):
                self._emit('source_context_repeat_omitted',reason='The draft has only earlier context grounds, not new primary speech')
                continue
            selections.append(supported_draft['support_fragments'])
            verified.append((draft, actual_bases))
        if verified or (records and records[0].get('role') == 'source'):
            self._verify([draft for draft,_ in verified],records,bases,origin,
                         support_catalog=catalog,support_selections=selections)
        return verified

    def _verify_source_batch(self, prepared, records, bases, origin):
        quotes, pinned = self._source_support_material(prepared, records, bases)
        from .thought_support import prepare_support
        catalog=prepare_support(quotes,pinned)
        self._verify([draft for draft,_ in prepared],quotes,pinned,origin,source_batch=True,
            support_catalog=catalog,support_selections=[catalog.options_for_bases(bases) for _,bases in prepared])

    def _source_support_material(self, prepared, records, bases):
        lookup = {self._source_key(basis): record for record, basis in zip(records, bases)}
        quotes, pinned, seen = [], [], set()
        for _, actual_bases in prepared:
            for basis in actual_bases:
                key = (self._source_key(basis), json.dumps(basis.get('span'), sort_keys=True))
                if key in seen:
                    continue
                seen.add(key)
                record = lookup[self._source_key(basis)]
                span = basis.get('span') or {'start': 0, 'end': len(record['text'])}
                quotes.append({'role': 'verified_support_quote',
                               'text': record['text'][span['start']:span['end']],
                               'origin': record.get('origin')})
                pinned.append(basis)
        return quotes, pinned

    @staticmethod
    def _source_key(basis):
        return (basis.get('event_id'), basis.get('thought_id'), basis.get('version'))

    def _publish(self, verified, *, origin=None, target=None, emit=True, whole_refresh=None):
        from .thought_publication import publish
        return publish(self, verified, origin=origin, target=target, emit=emit, whole_refresh=whole_refresh)

    def _construct(self, records, bases, *, question=None, origin=None, target=None):
        verified = self._prepare(records, bases, question=question, origin=origin)
        scope = next(((record, basis) for record, basis in zip(records, bases)
                      if record.get('role') == 'whole_refresh_scope'), None)
        return self._publish(verified, origin=origin, target=target, whole_refresh=scope)

    @staticmethod
    def _model_origin(origin):
        return {key: value for key, value in origin.items() if key not in ('event_id', 'session_id')}

    @staticmethod
    def _raw_origin(raw):
        metadata = raw.get('metadata', {})
        if not isinstance(metadata, dict):
            raise ModelError('raw thought metadata must be an object')
        try:
            normalized = normalize_clock(metadata, source=raw.get('source'))
            role = source_role(raw.get('actor', 'user'), normalized)
            clocks = clock_projection(normalized, source=raw.get('source'))
        except ValueError as error:
            raise ModelError(str(error)) from error
        origin = {key: raw[key] for key in ('actor', 'source', 'session_id', 'recorded_at') if key in raw}
        origin.update(role=role, timezone=normalized.get('timezone'), **clocks)
        return origin

    def _prepare_source(self, records, bases, text, origin):
        try:
            return self._prepare(records, bases, origin=origin)
        except ModelError as error:
            from . import model as model_contract
            overflow = getattr(model_contract, 'ModelContextExceeded', None)
            if overflow is None or not isinstance(error, overflow):
                raise
        correction, cross = '', []
        for batch_attempt in range(2):
            prepared, chunk_count = [], 0
            for span, body in paragraph_chunks(text, RAW_CHUNK_CHARACTERS):
                chunk_count += 1
                source_record = dict(records[0], text=body)
                if span['start'] != 0 or span['end'] != len(text):
                    source_record['source_completeness'] = ('Partial passage of a longer source; other passages '
                        'can contain qualifications. Preserve this limitation in every thought from this passage.')
                other = [(record, basis) for record, basis in cross if basis.get('event_id') == bases[0].get('event_id')
                         and not (span['start'] <= basis['span']['start'] and basis['span']['end'] <= span['end'])]
                chunk_records = [source_record, *records[1:], *[record for record, _ in other]]
                chunk_bases = [dict(bases[0], span=span), *bases[1:], *[basis for _, basis in other]]
                try:
                    batch = (self._prepare_once(chunk_records, chunk_bases, origin=origin, correction=correction)
                             if correction else self._prepare(chunk_records, chunk_bases, origin=origin))
                except SemanticReviewRejected as failure:
                    failure.recoverable = True
                    raise
                prepared.extend(batch)
            if chunk_count < 2:
                return prepared
            try:
                self._verify_source_batch(prepared, records, bases, origin)
                return prepared
            except SemanticReviewRejected as failure:
                if batch_attempt:
                    failure.recoverable = True
                    raise
                quotes, pinned = self._source_support_material(prepared, records, bases)
                cross = [(dict(record, role='context', source_completeness='Exact checked quotation from another passage of the same source, not independent confirmation.'), basis)
                         for record, basis in zip(quotes, pinned)]
                correction = ('\nRepair this passage after the source-wide review rejected cross-passage coverage: '
                    + failure.reason + ' Use the exact supplied cross-passage quotations as grounds where needed; '
                    'feedback itself is not factual evidence. Preserve source attribution and partial-passage limits.')
                self._emit('source_batch_semantic_repair', attempt=1, reason=failure.reason)

    def _source_packet(self, event, context=()):
        raw = asdict(event) if is_dataclass(event) else dict(event)
        text = raw.get('prompt', raw.get('text'))
        semantic_text(text)
        event_id = semantic_text(raw.get('id'), 'event id')
        origin = self._raw_origin(raw)
        origin['kind'] = 'source_interpretation'
        bases = [{'event_id': event_id}]
        records = [{'role': 'source', 'text': text, 'origin': self._model_origin(origin)}]
        for item in context:
            if hasattr(item, 'bases'):
                if not thought_eligibility(self.thoughts, item)['allowed']:
                    continue
                bases.append(self._basis(item))
                records.append({'role': 'context', **self._content(item)})
            else:
                context_raw = asdict(item) if is_dataclass(item) else dict(item)
                if not event_eligibility(context_raw)['allowed']:
                    continue
                bases.append({'event_id': semantic_text(context_raw.get('id'), 'context event id')})
                semantic_text(context_raw.get('prompt'))
                records.append({'role': 'context', 'text': context_raw['prompt'],
                                'origin': self._raw_origin(context_raw)})
        origin['event_id'] = event_id
        return text, event_id, origin, records, bases

    def _source_heads(self, event_id, origin):
        result = []
        for row in self.thoughts.list_thoughts():
            direct = row.origin.get('event_id') == event_id
            raw_bound = any(basis.get('event_id') == event_id for basis in row.bases)
            native = (row.origin.get('kind') == 'source_interpretation'
                      and all(row.origin.get(key) == origin.get(key)
                              for key in ('actor', 'source', 'session_id', 'recorded_at')))
            legacy = row.origin.get('kind') == 'legacy_assertion'
            if direct or (raw_bound and (native or legacy)):
                result.append(row)
        return result

    def ingest(self, event, context=(), *, consolidate=True):
        if not event_eligibility(event)['allowed']:
            return []
        text, event_id, origin, records, bases = self._source_packet(event, context)
        existing = [row for row in self._source_heads(event_id, origin)
                    if row.metadata.get('protocol') == PROTOCOL]
        created = existing or self._publish(self._prepare_source(records, bases, text, origin), origin=origin)
        if consolidate and created:
            self.consolidate(created)
        return created

    def reprocess_source(self, event, context=()):
        if not event_eligibility(event)['allowed']:
            return {'thoughts': [], 'retired': []}
        text, event_id, origin, records, bases = self._source_packet(event, context)
        old = self._source_heads(event_id, origin)
        prepared = self._prepare_source(records, bases, text, origin)
        proof = {'verification': 'accepted', 'event_id': event_id, 'protocol': PROTOCOL,
                 'expected_versions': {row.id: row.version for row in old}}
        reason = 'Replaced by a verified semantic interpretation of the same raw source'
        with self.store.transaction(write=True):
            published = self._publish(prepared, origin=origin, emit=False)
            retired = self.thoughts.retire([row.id for row in old], reason=reason, guardproof=proof) if old else []
        for row in published:
            self._emit('publication', thought=self._basis(row), bases=row.bases)
        for row in retired:
            self._emit('retirement', thought=self._basis(row), reason=reason)
        return {'thoughts': published, 'retired': retired}

    def reprocess_sources(self, events, context=()):
        events = list(events)
        if not events:
            raise ValueError('source refinement requires at least one raw event')
        events = [event for event in events if event_eligibility(event)['allowed']]
        if not events:
            return {'thoughts': [], 'retired': []}
        packets = [self._source_packet(event, context if index == 0 else ())
                   for index, event in enumerate(events)]
        event_ids = [packet[1] for packet in packets]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError('source refinement cannot repeat a primary raw event')
        records = [packet[3][0] for packet in packets]
        bases = [packet[4][0] for packet in packets]
        context_pairs = [(record, basis) for record, basis in zip(packets[0][3][1:], packets[0][4][1:])
                         if basis.get('event_id') not in event_ids]
        records.extend(record for record, _ in context_pairs)
        bases.extend(basis for _, basis in context_pairs)
        old = {row.id: row for packet in packets for row in self._source_heads(packet[1], packet[2])}
        for row in self.thoughts.list_thoughts():
            raw_ids = {basis['event_id'] for basis in row.bases if 'event_id' in basis}
            if row.origin.get('operation') == 'source_refinement' and raw_ids and raw_ids <= set(event_ids):
                old[row.id] = row
        origin = {'kind': 'inference', 'operation': 'source_refinement'}
        # Keep every matching quotation's provenance; the single whole-packet
        # reviewer judges attribution, never a guessed identity or extra witness.
        prepared = self._prepare(records, bases, origin=origin, review_ambiguity=False)
        proof = {'verification': 'accepted', 'event_ids': event_ids, 'protocol': PROTOCOL,
                 'expected_versions': {row.id: row.version for row in old.values()}}
        reason = 'Replaced by verified useful meaning from a bounded raw source batch'
        with self.store.transaction(write=True):
            published = self._publish(prepared, origin=origin, emit=False)
            retired = self.thoughts.retire(list(old), reason=reason, guardproof=proof) if old else []
        for row in published:
            self._emit('publication', thought=self._basis(row), bases=row.bases)
        for row in retired:
            self._emit('retirement', thought=self._basis(row), reason=reason)
        return {'thoughts': published, 'retired': retired}

    def _applicable(self, question, thought, context=()):
        review = self._call('Check ready thought',
            [{'role': 'question', 'text': question},
             {'role': 'candidate', **self._content(thought)},
             *[{'role': 'current_context', **self._content(row)} for row in context]],
            [self._basis(thought), *[self._basis(row) for row in context]], APPLICABILITY)
        exact_fields(review, {'verdict', 'text', 'question'})
        semantic_text(review.get('text'))
        if review.get('verdict') not in ('ready', 'build', 'drill'):
            raise ModelError('applicability requires ready, build or drill')
        if review['verdict'] == 'drill':
            semantic_text(review.get('question'), 'missing question')
        return review

    def _usable_content(self, thought):
        if thought.metadata.get('verification') == 'legacy_unverified':
            return False
        return self._fresh(thought)

    def _bounded_frontier(self, question, pending, unread, known_ids=()):
        corpus = {key: '\n'.join([row.text, *self.question_texts(row)]) for key, row in pending.items()}
        ranked = self.candidates.rank_relevance(question, corpus, limit=len(corpus))
        ready = [row.id for row in ranked if self._usable_content(pending[row.id])
                 or pending[row.id].origin.get('kind') == 'legacy_assertion']
        ordered = list(dict.fromkeys([*ready, *[row.id for row in ranked]]))
        keep = list(dict.fromkeys([*ordered[:MAX_THOUGHTS],
                                  *[key for key in known_ids if key in pending]]))
        unread.update(set(pending) - set(keep))
        return {key: pending[key] for key in keep}

    def _raw_allowed(self, raw):
        if not event_eligibility(raw)['allowed']:
            return False
        context = getattr(self.thoughts, 'context', {})
        wanted = context.get('scopes')
        if wanted is not None and raw.get('scope') not in wanted:
            return False
        cutoff = context.get('recorded_as_of')
        if cutoff is not None and _timestamp(raw['recorded_at']) > cutoff:
            return False
        cutoff = context.get('valid_as_of')
        valid = raw.get('metadata', {}).get('valid_at')
        return cutoff is None or (valid is not None and _timestamp(valid) <= cutoff)

    def _pending_raw_material(self, question, existing=()):
        excluded = {basis.get('event_id') for basis in existing if 'event_id' in basis}
        with self.store.transaction() as connection:
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_capture_jobs'").fetchone()
            if not exists:
                return [], []
            rows = connection.execute('SELECT e.id,e.actor,e.source,e.metadata,e.scope,e.recorded_at '
                'FROM memory_raw_events e JOIN memory_capture_jobs j ON j.event_id=e.id '
                "WHERE j.status IN ('pending','waiting_model','running') ORDER BY e.rowid").fetchall()
            eligible = []
            for row in rows:
                raw = dict(row, metadata=json.loads(row['metadata']))
                try:
                    visible = source_role(raw['actor'], raw['metadata']) in ('user', 'assistant')
                except ValueError:
                    visible = False  # Private or unsupported roles are not chat read-repair input.
                if raw['id'] not in excluded and visible and self._raw_allowed(raw):
                    eligible.append(raw)
            bodies = {row['id']: dict(connection.execute('SELECT * FROM memory_raw_events WHERE id=?',
                        (row['id'],)).fetchone()) for row in eligible}
        ranked = self.candidates.rank_relevance(question,
            {key: row['prompt'] for key, row in bodies.items()}, limit=len(bodies))
        records, bases, selected = [], [], []
        for candidate in ranked:
            if candidate.id in excluded:
                continue
            if len(selected) == 3:
                break
            primary = self._raw_packet(candidate.id)
            if not primary:
                continue
            record, basis = primary[0]
            records.append(record)
            bases.append(basis)
            selected.append(candidate.id)
            excluded.add(candidate.id)
            for record, basis in primary[1:]:
                if basis['event_id'] in excluded:
                    continue
                records.append(record)
                bases.append(basis)
                excluded.add(basis['event_id'])
        self._emit('raw_gap_search', question=question,
            candidates=[{'event_id': row.id, 'score': row.score, 'method': row.method} for row in ranked],
            selected=bases, details={'primary_count': len(selected), 'input_chars': len(json.dumps(records, ensure_ascii=False)),
                                     'queue_unchanged': True})
        return records, bases

    def _raw_packet(self, event_id):
        with self.store.transaction() as connection:
            target = connection.execute('SELECT e.id,e.session_id,e.actor,e.source,e.metadata,e.scope,e.recorded_at,e.rowid AS intake,p.position FROM memory_raw_events e '
                'LEFT JOIN memory_event_positions p ON p.event_id=e.id WHERE e.id=?', (event_id,)).fetchone()
            if target is None or not self._raw_allowed(dict(target, metadata=json.loads(target['metadata']))):
                return []
            target_body = connection.execute('SELECT * FROM memory_raw_events WHERE id=?', (event_id,)).fetchone()
            order = 'p.position' if target['position'] is not None else 'e.rowid'
            cutoff = target['position'] if target['position'] is not None else target['intake']
            prior = connection.execute('SELECT e.id,e.actor,e.source,e.metadata,e.scope,e.recorded_at FROM memory_raw_events e '
                'LEFT JOIN memory_event_positions p ON p.event_id=e.id '
                f'WHERE e.session_id=? AND {order}<? ORDER BY {order} DESC,e.rowid DESC LIMIT 16',
                (target['session_id'], cutoff)).fetchall()
        selected = [dict(target_body)]
        context = []
        for row in prior:
            raw = dict(row, metadata=json.loads(row['metadata']))
            if self._raw_allowed(raw):
                with self.store.transaction() as connection:
                    body = connection.execute('SELECT * FROM memory_raw_events WHERE id=?', (raw['id'],)).fetchone()
                context.append(dict(body))
                if len(context) == 2:
                    break
        selected.extend(reversed(context))
        result = []
        for raw in selected:
            if isinstance(raw['metadata'], str):
                raw['metadata'] = json.loads(raw['metadata'])
            if not self._raw_allowed(raw):
                continue
            result.append(({'role': 'material', 'text': raw['prompt'],
                            'origin': self._model_origin(self._raw_origin(raw)),
                            'verification': 'original_source',
                            'qualifications': {key: raw['metadata'][key] for key in
                                ('conditions', 'exceptions', 'conflicts', 'uncertainty', 'limitations', 'known_gaps')
                                if key in raw['metadata']},
                            'context_completeness': 'Only bounded preceding visible context is supplied.'},
                           {'event_id': raw['id']}))
        return result

    def _current_material(self, question, candidates, *, known_ids=()):
        pending = {row.id: row for row in candidates}
        visited, unread, material = set(), set(), []
        admission_memo = {}
        while pending:
            if material and not any(self._usable_content(row) or row.origin.get('kind') == 'legacy_assertion' or row.id in known_ids
                                    for row in pending.values()):
                break
            pending = self._bounded_frontier(question, pending, unread, known_ids)
            if not pending:
                break
            key = next(iter(pending))
            thought = pending.pop(key)
            latest = self.thoughts.get(thought.id)
            thought = latest or thought
            marker = (thought.id, thought.version)
            if marker in visited or not thought_eligibility(self.thoughts, thought, memo=admission_memo)['allowed']:
                continue
            visited.add(marker)
            if self._usable_content(thought):
                record = {'role': 'material', **self._content(thought)}
                material.append((record, self._basis(thought)))
                continue
            children = {}
            for basis in thought.bases:
                if 'thought_id' in basis:
                    current = self.thoughts.get(basis['thought_id'])
                    if current is not None and (current.id, current.version) not in visited:
                        children[current.id] = current
                elif 'event_id' in basis:
                    for record, raw_basis in self._raw_packet(basis['event_id']):
                        if any(existing.get('event_id') == raw_basis['event_id'] for _, existing in material):
                            continue
                        material.append((record, raw_basis))
            pending.update(children)
            self._emit('expansion', parent=self._basis(thought),
                       candidates=[self._basis(row) for row in children.values()],
                       reason='Navigate unverified or changed support with a bounded ranked frontier', question=question)
        unread.update(pending)
        unread.difference_update(basis.get('thought_id') for _, basis in material)
        if not material:
            raise empty_material('No admitted current supporting source was found in the selected material')
        if unread:
            availability = {'coverage': 'partial', 'unread_supporting_branches': len(unread),
                'uncertainty': 'Unopened related support may contain additional conditions or conflicts; completeness is not established.'}
            for record, _ in material:
                record['material_availability'] = availability
            self._emit('material_gap', question=question, details=availability)
        return [row for row, _ in material], [basis for _, basis in material]

    @staticmethod
    def _merge_material(records, bases, extra_records, extra_bases):
        packet, pinned, positions = [], [], {}
        for record, binding in zip([*records, *extra_records], [*bases, *extra_bases]):
            identity = {key: binding[key] for key in ('event_id', 'thought_id', 'version', 'span') if key in binding}
            key = json.dumps(identity, sort_keys=True)
            if key in positions:
                previous = packet[positions[key]]
                if previous.get('text') != record.get('text'):
                    raise ModelError('the same exact source binding has inconsistent material text')
                for field in ('material_availability', 'source_completeness', 'context_completeness'):
                    if field in record and field not in previous:
                        previous[field] = record[field]
                continue
            positions[key] = len(packet)
            packet.append(dict(record))
            pinned.append(binding)
        return packet, pinned

    def answer(self, question, explicit_ids=(), *, _detail_state=None):
        from .thought_query import answer
        return answer(self, question, explicit_ids)

    def consolidate(self, changed):
        changed = list(changed)
        if not changed:
            return []
        related = self.retrieve('\n'.join(row.text for row in changed), [row.id for row in changed])
        response = self._call('Find useful shared thought question',
            [{'role': 'changed', **self._content(row)} for row in changed] +
            [{'role': 'existing', **self._content(row)} for row in related if row.id not in {r.id for r in changed}],
            [self._basis(row) for row in related], GENERALIZE)
        exact_fields(response, {'question', 'text'})
        question = semantic_text(response.get('question'), 'question', nullable=True)
        if question is None:
            return []
        semantic_text(response.get('text'))
        candidates = self.retrieve(question, [row.id for row in changed])
        target = next((row for row in candidates if question in self.question_texts(row)
                       and not self._is_parallel_alternative(row)), None)
        if target is not None and all((row.id, row.version) == (target.id, target.version) for row in changed):
            return [target]
        if target is not None:
            from .thought_answering import can_refresh, refresh_thought
            if can_refresh(self, target):
                return [refresh_thought(self, target, additional=changed)]
            target = None
        frontier = self._frontier(candidates, target.id if target else None)
        records, bases = self._current_material(question, candidates, known_ids=[row.id for row in changed])
        result = self._construct(records, bases, question=question,
            origin={'kind': 'inference'}, target=target)
        for thought in result:
            self._save_check(thought, question, frontier)
        return result
