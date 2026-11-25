"""Exact technical run data, separate from the semantic thought graph."""
from __future__ import annotations
import hashlib
import json
import time
import zlib
from datetime import datetime,timezone
from uuid import uuid4

from . import genai_traces, query_timing as timing
from .model import ModelError,ProcessingCancelled,check_processing_cancelled,format_prompt
from .usage_context import observe_attempt_usage
from .diagnostic_privacy import restricted, redact, ProtectedContextError, diagnostic_hash
from .thought_repair_stats import RepairAccounting,ensure_schema


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def _now():
    return datetime.now(timezone.utc).isoformat()


class ThoughtTrace:
    def __init__(self,store,operation,inputs=()):
        self.store=store;self.id=str(uuid4());self.operation=operation
        with store.transaction(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS memory_thought_runs '
                '(id TEXT PRIMARY KEY,operation TEXT NOT NULL,inputs TEXT NOT NULL,started_at TEXT NOT NULL,'
                'finished_at TEXT,status TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS memory_thought_trace_events '
                '(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL,stage TEXT NOT NULL,data TEXT NOT NULL,recorded_at TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS memory_thought_events_run ON memory_thought_trace_events(run_id,id)')
            db.execute('CREATE TABLE IF NOT EXISTS memory_thought_model_payloads '
                '(hash TEXT PRIMARY KEY,body BLOB NOT NULL,characters INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS memory_thought_model_calls '
                '(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,stage TEXT NOT NULL,request_hash TEXT NOT NULL,'
                'response_hash TEXT,bindings TEXT NOT NULL,status TEXT NOT NULL,started_at TEXT NOT NULL,'
                'finished_at TEXT,duration_ms INTEGER,usage TEXT,error TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS memory_thought_calls_run ON memory_thought_model_calls(run_id,started_at)')
            ensure_schema(db)
            db.execute('INSERT INTO memory_thought_runs(id,operation,inputs,started_at,status) VALUES(?,?,?,?,?)',
                (self.id,operation,_json(inputs),_now(),'running'))
        self.accounting=RepairAccounting(self,inputs)
        if self.accounting.queue_retry:
            self.record('queue_retry_attempt',details={key:inputs.get(key) for key in
                ('job_id','event_id','thought_id','generation','attempt','previous_error') if key in inputs})

    def bind_model(self,model):
        self.accounting.bind(model)

    def record(self,stage,inputs=(),candidates=(),selected=(),outputs=(),details=None):
        details=dict(details or {})
        data={'inputs':inputs,'candidates':candidates,'selected':selected,'outputs':outputs,'details':details}
        if restricted():
            inputs=candidates=selected=outputs=()
            details=redact(details)
            data={'inputs':[],'candidates':[],'selected':[],'outputs':[],'details':details}
        with self.store.transaction(write=True) as db:
            if stage=='publication':details['construction_call_id']=self.accounting.publication_origin(db,details)
            result=db.execute('INSERT INTO memory_thought_trace_events(run_id,stage,data,recorded_at) VALUES(?,?,?,?)',
                (self.id,stage,_json(data),_now()))
            self.accounting.event(db,stage,dict(details,_bindings=inputs))
            return result.lastrowid

    def _payload(self,db,body):
        key=hashlib.sha256(body.encode()).hexdigest()
        if restricted():
            body=_json(dict(redact(body),original_sha256=key))
            key=diagnostic_hash(key)
        db.execute('INSERT OR IGNORE INTO memory_thought_model_payloads VALUES(?,?,?)',
            (key,zlib.compress(body.encode()),len(body)))
        return key

    def call(self,model,request,bindings=(),stage=None):
        wire=format_prompt(request);call_id=str(uuid4());stage=stage or request.task
        model_phase = timing.model_stage(stage)
        with timing.bind(run_id=self.id, call_id=call_id, model_phase=model_phase), timing.span(model_phase, start=True):
            return self._call(model, request, bindings, stage, wire, call_id)

    def _call(self,model,request,bindings,stage,wire,call_id):
        bound=_json([] if restricted() else bindings)
        if not self.accounting.meta:self.bind_model(model)
        with timing.span('trace_request_write'), self.store.transaction(write=True) as db:
            request_hash=self._payload(db,wire)
            self._request_hash=request_hash
            db.execute('INSERT INTO memory_thought_model_calls '
                '(id,run_id,stage,request_hash,bindings,status,started_at) VALUES(?,?,?,?,?,?,?)',
                (call_id,self.id,stage,request_hash,bound,'running',_now()))
            self.accounting.call_started(db,call_id,model,request,stage)
        started=time.monotonic();usage={};status='failed';response_hash=None;error=None
        try:
            check_processing_cancelled()
            with timing.span('provider_attempt', start=True), observe_attempt_usage(usage.update):
                try:
                    result=model(request)
                finally:
                    genai_traces.observe_usage(usage)
            check_processing_cancelled()
            if format_prompt(request)!=wire:
                raise ModelError('thought model input mutated during acquisition')
            body=result if isinstance(result,str) else _json(result)
            with timing.span('trace_response_write'), self.store.transaction(write=True) as db:
                response_hash=self._payload(db,body)
            status='succeeded'
            return result
        except BaseException as failure:
            error=(str(ProtectedContextError()) if restricted() else type(failure).__name__+': '+str(failure))
            if isinstance(failure,(KeyboardInterrupt,ProcessingCancelled)) or type(failure).__name__=='CancelledError':status='interrupted'
            raise
        finally:
            if restricted():
                usage={key:value for key,value in usage.items() if key in
                    ('input_tokens','output_tokens','cached_input_tokens','reasoning_output_tokens')
                    and type(value) in (int,float)}
            with self.store.transaction(write=True) as db:
                db.execute('UPDATE memory_thought_model_calls SET response_hash=?,status=?,finished_at=?,'
                    'duration_ms=?,usage=?,error=?,actual_model=? WHERE id=?',
                    (response_hash,status,_now(),round((time.monotonic()-started)*1000),
                     _json(usage) if usage else None,error,usage.get('actual_model') or None,call_id))

    def finish(self,status='completed'):
        with self.store.transaction(write=True) as db:
            db.execute('UPDATE memory_thought_runs SET status=?,finished_at=? WHERE id=?',
                       (status,_now(),self.id))
            self.accounting.finish(db,status)

    def events(self):
        with self.store.transaction() as db:
            rows=db.execute('SELECT * FROM memory_thought_trace_events WHERE run_id=? ORDER BY id',(self.id,)).fetchall()
        return [{**dict(row),'data':json.loads(row['data'])} for row in rows]

    def calls(self):
        with self.store.transaction() as db:
            rows=db.execute('SELECT * FROM memory_thought_model_calls WHERE run_id=? ORDER BY started_at,id',(self.id,)).fetchall()
        return [{**dict(row),'bindings':json.loads(row['bindings']),
                 'usage':json.loads(row['usage']) if row['usage'] else None} for row in rows]

    def payload(self,key):
        with self.store.transaction() as db:
            row=db.execute('SELECT body FROM memory_thought_model_payloads WHERE hash=?',(key,)).fetchone()
        if row is None:raise ValueError('unknown trace payload')
        return zlib.decompress(row['body']).decode()
