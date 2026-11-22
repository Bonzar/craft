"""Canonical thought application API; raw transport uses the existing intake API."""
from __future__ import annotations

import json
import time
from dataclasses import asdict

from .model import ModelProviderUnavailable, SemanticUnavailable
from .service import MemoryService, _text_page, _request_usage
from .storage import background_write_retry
from .thought_engine import ThoughtEngine
from .thought_engine_contract import SemanticReviewRejected, thought_drafts
from .thought_store import ThoughtStore
from .thought_trace import ThoughtTrace
from .usage_context import query_usage_trace
from .thought_processing_selection import normalize_session_ids, processing_cohort

EMPTY_RETRIEVED_MATERIAL_NOTICE = 'В полученных материалах нет сведений для ответа на этот вопрос'

INSTRUCTIONS = ('Shared thought memory. Source interpretations and composite explanations use one thought format. '
    'A thought has equal questions; their order gives no priority and does not prove applicability to a changed version. '
    'Capture raw user, assistant and useful tool reports with stable session/message provenance. '
    'Capture acknowledges durable intake; it does not mean semantic processing finished. '
    'memory_query centrally constructs and saves a missing answer, then returns it; an existing suitable '
    'thought may answer directly. Preserve conditions, exceptions, uncertainty and attribution. '
    'A source report is not independent confirmation of present reality. Memory paraphrases are derivative. '
    'Check answer_accepted, answer_error, knowledge_limit, pending_count, refresh_required and transport_limit separately. '
    'If answer_error is present, report that completion failure and do not use the rejected draft. '
    'A parallel_alternative is independently checked against its grounds, but compatibility with '
    'another answer remains unresolved; show that limit rather than treating completion order as agreement. '
    'Follow all answer_continuations before using an incomplete answer. Exact thought versions, bases and '
    'model run traces are technical provenance. Do not reconstruct the entire graph locally or edit SQLite. '
    'Use memory_source for pinned text, bases and origin; memory_history for versions. '
    'A partial queue or unknown delivery frontier limits completeness, not the meaning of already stored text.')


class ThoughtService(MemoryService):
    def __init__(self, store, model=None, candidates=None, *, processing_session_ids=None):
        thoughts = ThoughtStore(store)
        super().__init__(store, ThoughtEngine(thoughts, model, candidates))
        self.thoughts = thoughts
        from .thought_worker_queue import ThoughtWorkQueue
        self.processing_session_ids = normalize_session_ids(processing_session_ids)
        self.work = ThoughtWorkQueue(self.queue, processing_session_ids=self.processing_session_ids)

    def _pending_count(self):
        with self.store.transaction() as db:
            raw = db.execute("SELECT count(*) FROM memory_capture_jobs WHERE event_id IS NOT NULL AND status!='completed'").fetchone()[0]
            semantic = db.execute("SELECT count(*) FROM memory_thought_work WHERE status!='completed'").fetchone()[0]
            return raw+semantic

    def capture_message(self, *args, **kwargs):
        result = super().capture_message(*args, **kwargs)
        event = self.queue.get_event(result['event']['id'])
        from .thought_source_policy import event_eligibility
        admission = event_eligibility(event)
        if not admission['allowed']:
            with self.store.transaction(write=True) as db:
                db.execute("UPDATE memory_capture_jobs SET status='completed',phase='excluded',"
                    "error=?,lease_token=NULL,lease_until=NULL WHERE event_id=?",
                    (admission['policy']+': '+admission['reason'],event.id))
            result['semantic_admission'] = admission
            result['job'] = {'id':event.id,'status':'completed','phase':'excluded'}
            result['pending_count'] = self._pending_count()
        return result

    def query(self, query, scope=None, valid_as_of=None, recorded_as_of=None, *, request_session_id=None):
        sessions = () if request_session_id is None else normalize_session_ids((request_session_id,))
        with processing_cohort(sessions):
            return self._query(query, scope, valid_as_of, recorded_as_of, request_session_id=request_session_id)

    def _query(self, query, scope=None, valid_as_of=None, recorded_as_of=None, *, request_session_id=None):
        if not isinstance(query, str) or not query.strip() or len(query) > 8000:
            raise ValueError('query must contain between 1 and 8000 characters')
        from .thought_selection import ThoughtSelection
        selection = (ThoughtSelection(self.thoughts, scope=scope, valid_as_of=valid_as_of,
                     recorded_as_of=recorded_as_of) if any(value is not None for value in
                     (scope, valid_as_of, recorded_as_of)) else self.thoughts)
        started = time.monotonic()
        with background_write_retry(), query_usage_trace() as usage_id:
            with self.store.transaction() as db:
                prior_version = db.execute('SELECT coalesce(max(rowid),0) FROM memory_thought_versions').fetchone()[0]
            trace = ThoughtTrace(self.store, 'query', {'question': query, 'request_session_id': request_session_id})
            engine = traced_engine(selection, self.engine.model, self.engine.candidates, trace)
            try:
                from .thought_query_replay import query_replay
                with query_replay(engine, trace, {'question': query, 'scope': scope,
                        'valid_as_of': valid_as_of, 'recorded_as_of': recorded_as_of}):
                    thought = engine.answer(query)
            except SemanticReviewRejected as error:
                trace.record('query_outcome', details={'answer_accepted': False,
                    'error': 'semantic_review_rejected', 'reason': error.reason})
                trace.finish('failed')
                result = {'answer': None, 'answer_accepted': False, 'answer_payload_complete': True,
                          'answer_continuations': [], 'run_id': trace.id,
                          'answer_error': {'code': 'semantic_review_rejected',
                                           'message': 'Проверка не подтвердила ответ даже после исправления.'}}
            except ModelProviderUnavailable as error:
                trace.record('query_outcome', details={'answer_accepted': False, 'error': error.code})
                trace.finish('failed')
                result = {'answer': None, 'answer_accepted': False, 'answer_payload_complete': True,
                          'answer_continuations': [], 'run_id': trace.id,
                          'answer_error': {'code': error.code, 'message': 'Модель Codex недоступна.'}}
            except SemanticUnavailable as error:
                trace.record('knowledge_limit', details={'reason': str(error)})
                trace.finish('knowledge_limit')
                result = {'answer': None, 'answer_accepted': False, 'answer_payload_complete': True,
                          'knowledge_limit': str(error), 'run_id': trace.id}
                if getattr(error, 'empty_ground', False):
                    result.update(answer=EMPTY_RETRIEVED_MATERIAL_NOTICE, answer_continuations=[],
                        answer_metadata={'notice':{'purpose':'knowledge_limit_notice', 'scope':'retrieved_material',
                            'text':EMPTY_RETRIEVED_MATERIAL_NOTICE, 'notice_payload_complete':True}})
            except BaseException:
                trace.finish('failed')
                raise
            else:
                with self.store.transaction() as db:
                    published = db.execute('SELECT rowid FROM memory_thought_versions WHERE thought_id=? AND version=?',
                                           (thought.id,thought.version)).fetchone()[0] > prior_version
                if published and not thought.metadata.get('selection_context'):
                    self.work.enqueue([thought])
                trace.record('query_outcome',details={'answer_accepted':True,'thought':{'thought_id':thought.id,'version':thought.version},'published':published})
                trace.finish()
                page = self.source(thought.id, section='text', snapshot_id=thought.version)
                result = {'answer': page['content'], 'thought': thought_descriptor(thought, self.engine.question_texts(thought)), 'run_id': trace.id,
                          'answer_accepted': True, 'answer_payload_complete': True,
                          'answer_continuations': [page['continuation']] if page['continuation'] else [],
                          'version_id': thought.version, 'view_id': thought.id,
                          'query_snapshot': {'scope': scope, 'valid_as_of': valid_as_of,
                                             'recorded_as_of': recorded_as_of}}
                result['answer_payload_complete'] = page['response_complete']
                if engine._is_parallel_alternative(thought):
                    result['knowledge_limit'] = ('Сохранён параллельно подготовленный проверенный вариант. '
                        'Его совместимость с другим ответом на тот же вопрос ещё не установлена; '
                        'порядок завершения не определяет, какой вариант верен.')
        result['request_usage']=_request_usage(self.store.path,usage_id,round((time.monotonic()-started)*1000))
        from .thought_status import query_diagnostics
        return query_diagnostics(self,result,scope)

    def source(self, record_id, start=None, end=None, *, section='overview', offset=0, limit=6000, snapshot_id=None):
        thought = self.thoughts.get(record_id, snapshot_id)
        if thought is None:
            return super().source(record_id, start, end, section=section, offset=offset,
                                  limit=limit, snapshot_id=snapshot_id)
        if (start is None) != (end is None):
            raise ValueError('thought span requires start and end')
        if start is not None:
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(thought.text) or end-start > 6000:
                raise ValueError('thought span is outside text or exceeds 6000 characters')
            return {'thought_id': thought.id, 'version': thought.version, 'text': thought.text[start:end],
                    'start': start, 'end': end, 'origin': thought.origin, 'response_complete': True}
        if section == 'text':
            value = thought.text
        elif section == 'overview':
            value = public_thought(thought, self.engine.question_texts(thought))
        elif section == 'metadata':
            value = thought.metadata
        elif section == 'references':
            value = thought.bases
        elif section == 'edges':
            value = self.thoughts.links(record_id)
        elif section == 'extraction_history':
            value = [public_thought(row, self.engine.question_texts(row))
                     for row in self.thoughts.versions(record_id) if row.version <= thought.version]
        else:
            raise ValueError('unknown thought section')
        return _text_page(value, 'memory_source', {'record_id': record_id,
            'section': section, 'snapshot_id': thought.version}, offset, limit, plain=section == 'text')

    def history(self, view_id, *, offset=0, limit=6000, section='overview', snapshot_id=None, **kwargs):
        versions = self.thoughts.versions(view_id)
        if not versions:
            raise ValueError('unknown thought identity')
        maximum = snapshot_id or versions[-1].version
        version_id = kwargs.get('version_id')
        value = [public_thought(row, self.engine.question_texts(row)) for row in versions if row.version <= maximum
                 and (version_id is None or row.version == version_id)]
        return _text_page(value, 'memory_history', {'view_id': view_id, 'section': section, 'version_id': version_id,
                          'snapshot_id': maximum}, offset, limit)

    def provenance(self, record_ids):
        if not isinstance(record_ids,list) or not 1 <= len(record_ids) <= 100:
            raise ValueError('record_ids must contain 1 to 100 identities')
        from .session_api import SessionAPI, _ORIGIN_COLUMNS, _provenance_record
        records = []
        with self.store.transaction() as db:
            for identity in record_ids:
                thought = self.thoughts.get(identity)
                if thought is None:
                    records.extend(SessionAPI(db).provenance([identity])['records'])
                    continue
                pending, visited, raw_ids, unresolved = [thought], set(), set(), 0
                while pending:
                    current = pending.pop()
                    if (current.id,current.version) in visited:
                        continue
                    visited.add((current.id,current.version))
                    if len(visited) > 10000:
                        raise ValueError('provenance dependency budget exceeded; inspect pinned bases separately')
                    for basis in current.bases:
                        if 'event_id' in basis:
                            raw_ids.add(basis['event_id'])
                        elif 'thought_id' in basis:
                            child = self.thoughts.get(basis['thought_id'],basis['version'])
                            if child is None: unresolved += 1
                            else: pending.append(child)
                origins = []
                for event_id in sorted(raw_ids):
                    row = db.execute('SELECT '+_ORIGIN_COLUMNS+' FROM memory_raw_events e WHERE e.id=?', (event_id,)).fetchone()
                    if row is None: unresolved += 1
                    else: origins.append(row)
                records.append(_provenance_record(identity,origins,complete=bool(raw_ids) and unresolved == 0,unresolved=unresolved))
        return {'records':records,'membership_basis':'exact_pinned_thought_raw_ancestry','response_complete':True}

    def run_history(self, run_id, *, section='run', offset=0, limit=6000, snapshot_id=None):
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM memory_thought_runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                return super().run_history(run_id, section=section, offset=offset, limit=limit, snapshot_id=snapshot_id)
            if section == 'run':
                value = dict(row, inputs=json.loads(row['inputs']))
                value['model_calls'] = [dict(item) for item in db.execute(
                    'SELECT id,stage,status,duration_ms,usage,error FROM memory_thought_model_calls '
                    'WHERE run_id=? ORDER BY started_at,id', (run_id,))]
            elif section == 'causes':
                value = json.loads(row['inputs'])
            elif section == 'events':
                maximum = snapshot_id if snapshot_id is not None else db.execute(
                    'SELECT coalesce(max(id),0) FROM memory_thought_trace_events WHERE run_id=?', (run_id,)).fetchone()[0]
                value = [dict(item, data=json.loads(item['data'])) for item in db.execute(
                    'SELECT * FROM memory_thought_trace_events WHERE run_id=? AND id<=? ORDER BY id', (run_id,maximum))]
                snapshot_id = maximum
            else:
                raise ValueError('unknown run section')
        return _text_page(value, 'memory_run_history', {'run_id': run_id, 'section': section,
                          'snapshot_id': snapshot_id}, offset, limit)

    def model_call(self, call_id, *, section='overview', offset=0, limit=6000):
        import zlib
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM memory_thought_model_calls WHERE id=?', (call_id,)).fetchone()
            if row is None:
                raise ValueError('unknown canonical model call')
            if section == 'overview':
                value = dict(row, bindings=json.loads(row['bindings']), usage=json.loads(row['usage']) if row['usage'] else None)
            elif section in ('request','response'):
                body = db.execute('SELECT body FROM memory_thought_model_payloads WHERE hash=?',
                                  (row[section+'_hash'],)).fetchone()
                value = zlib.decompress(body['body']).decode() if body else None
            else:
                raise ValueError('unknown model call section')
        return _text_page(value, 'memory_model_call', {'call_id': call_id, 'section': section},
                          offset, limit, plain=isinstance(value,str))

    def status(self, event_id=None, *, scope=None, offset=0, limit=100, section='overview', catalog_id=None):
        if section == 'model_usage':
            result=super().status(event_id, scope=scope, offset=offset, limit=limit, section=section, catalog_id=catalog_id)
            from .thought_repair_stats import repair_statistics
            result['thought_repairs']=repair_statistics(self.store,self.engine.model)
            return result
        if section=='transport':
            return super().status(event_id,scope=scope,offset=offset,limit=limit,section=section,catalog_id=catalog_id)
        if section not in ('overview','job','navigation_catalog'):
            raise ValueError('unsupported canonical diagnostic section')
        if catalog_id is not None:
            raise ValueError('canonical diagnostics do not use catalog_id')
        if section=='job':
            if event_id is None:raise ValueError('job section requires event_id')
            from .thought_status import job
            return _text_page(job(self,event_id,scope),'memory_status',
                {'event_id':event_id,'scope':scope,'section':'job'},offset,limit)
        if section=='overview':
            from .thought_status import snapshot
            return snapshot(self,event_id,scope,offset,limit)
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError('bounded positive page required')
        if section == 'navigation_catalog':
            from .thought_selection import ThoughtSelection
            thoughts = ThoughtSelection(self.thoughts, scope=scope) if scope is not None else self.thoughts
            rows = thoughts.list_thoughts()
            page = rows[offset:offset+limit]
            end = offset+len(page)
            return {'thoughts': [thought_descriptor(row, self.engine.question_texts(row)) for row in page], 'total': len(rows),
                    'next_offset': end if end < len(rows) else None, 'response_complete': end >= len(rows)}


def public_thought(thought, questions):
    # The immutable old payload keeps its construction question for hash/history compatibility.
    payload = asdict(thought)
    payload.pop('question', None)
    return dict(payload, questions=list(questions))


def thought_descriptor(thought, questions):
    return {'id': thought.id, 'version': thought.version, 'questions': list(questions),
            'created_at': thought.created_at, 'origin': thought.origin,
            'basis_count': len(thought.bases), 'bases': thought.bases,
            'publication': thought.metadata.get('publication')}


def traced_engine(thoughts, model, candidates, trace):
    trace.bind_model(model)
    def event(value):
        fields = dict(value)
        stage = fields.pop('stage')
        # Full wire bodies already have compressed, hashed durable storage.
        records=fields.pop('records',None)
        if stage=='model_input':
            roles={record.get('role') for record in (records or [])}
            fields['purpose']='raw_source_batch' if fields.get('task')=='Verify source thought batch' else 'whole_refresh' if 'whole_refresh_scope' in roles else 'raw_passage' if 'source' in roles else 'question_answer' if 'question' in roles else 'composite'
        output=fields.pop('output',None)
        if stage=='model_output' and fields.get('task')=='Construct thought' and isinstance(output,dict):
            import hashlib
            drafts=thought_drafts(output,single_thought='thought' in output) if fields.get('has_draft_batch') else []
            fields['draft_text_hashes']=[hashlib.sha256(row['text'].strip().encode()).hexdigest()
                for row in drafts]
        trace.record(stage, inputs=fields.pop('bindings', ()),
                     candidates=fields.pop('candidates', ()), selected=fields.pop('selected', ()),
                     details=fields)
    return ThoughtEngine(thoughts, model, candidates, event,
        gateway=lambda task, request, bindings: trace.call(model, request, bindings, stage=task))
