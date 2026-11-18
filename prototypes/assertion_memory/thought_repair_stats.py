"""Small bounded repair telemetry; semantic judgments remain the reviewer's claims."""
import hashlib
import json
from collections import Counter,defaultdict
from functools import lru_cache
from pathlib import Path
import re
import zlib
from datetime import datetime,timezone

TOKEN_FIELDS=('input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens')


@lru_cache(maxsize=1)
def code_version():
    try:
        manifest=json.loads((Path(__file__).resolve().parents[2]/'manifest.json').read_text())
    except (OSError,ValueError):return None
    if not isinstance(manifest,dict):return None
    for key in ('source_commit','release_id'):
        value=manifest.get(key)
        if isinstance(value,str) and re.fullmatch('[0-9a-fA-F]{40}',value):return value.lower()
    return None


def route_metadata(model,task='Construct thought'):
    chooser=getattr(model,'model_for',None)
    chosen=chooser(task) if callable(chooser) else model
    primary=chooser('Construct thought') if callable(chooser) else model
    reviewer=chooser('Verify thought') if callable(chooser) else model
    return {'route_profile':getattr(model,'route_profile_key',None),'code_version':code_version(),
        'requested_model':getattr(chosen,'requested_model',None),
        'requested_reasoning_effort':getattr(chosen,'reasoning_effort',None),
        'requested_primary':getattr(primary,'requested_model',None),
        'requested_reviewer':getattr(reviewer,'requested_model',None)}


def ensure_schema(db):
    columns={row[1] for row in db.execute('PRAGMA table_info(memory_thought_model_calls)')}
    for name,kind in {'requested_model':'TEXT','actual_model':'TEXT','requested_reasoning_effort':'TEXT',
        'route_profile':'TEXT','code_version':'TEXT','route_epoch':'INTEGER','purpose':'TEXT',
        'semantic_verdict':'TEXT','format_error':'INTEGER NOT NULL DEFAULT 0','repair_kind':'TEXT','cycle_id':'TEXT'}.items():
        if name not in columns:db.execute('ALTER TABLE memory_thought_model_calls ADD COLUMN '+name+' '+kind)
    columns={row[1] for row in db.execute('PRAGMA table_info(memory_thought_runs)')}
    for name,kind in {'routing':'TEXT','route_profile':'TEXT','code_version':'TEXT','route_epoch':'INTEGER','repair_counters':"TEXT NOT NULL DEFAULT '{}'"}.items():
        if name not in columns:db.execute('ALTER TABLE memory_thought_runs ADD COLUMN '+name+' '+kind)
    db.execute('CREATE INDEX IF NOT EXISTS memory_thought_runs_profile_started ON memory_thought_runs(route_profile,started_at,id)')
    db.execute('CREATE INDEX IF NOT EXISTS memory_thought_runs_epoch_started ON memory_thought_runs(route_epoch,started_at,id)')
    db.execute('CREATE INDEX IF NOT EXISTS memory_thought_events_run_stage ON memory_thought_trace_events(run_id,stage,id)')
    db.execute('CREATE INDEX IF NOT EXISTS memory_thought_calls_epoch ON memory_thought_model_calls(route_epoch,started_at,id)')
    db.execute('CREATE INDEX IF NOT EXISTS memory_thought_calls_profile ON memory_thought_model_calls(route_profile,started_at,id)')
    db.execute('CREATE INDEX IF NOT EXISTS memory_thought_calls_replay ON memory_thought_model_calls(request_hash,response_hash,route_profile)')
    db.execute('CREATE TABLE IF NOT EXISTS memory_thought_draft_cycles '
        '(id TEXT PRIMARY KEY,construct_call_id TEXT NOT NULL,run_id TEXT NOT NULL,last_run_id TEXT NOT NULL,'
        'operation TEXT NOT NULL,purpose TEXT,route_profile TEXT,route_epoch INTEGER,started_at TEXT NOT NULL,'
        'first_review TEXT,last_review TEXT,outcome TEXT NOT NULL,repair_of TEXT,rejection_call_id TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS memory_thought_cycles_epoch ON memory_thought_draft_cycles(route_epoch,started_at,id)')


class RepairAccounting:
    def __init__(self,trace,inputs=()):
        self.trace=trace;self.meta={};self.purposes={};self.last_call=None;self.active=None
        self.parent=None;self.semantic=False;self.group=False;self.format=False;self.technical=False;self.counters=Counter()
        self.inputs={};self.reused={};self.active_origin=None;self.draft_origins=defaultdict(set)
        self.retry_inputs=inputs if isinstance(inputs,dict) else {}
        attempt=self.retry_inputs.get('attempt')
        self.queue_retry=trace.operation in ('source','refresh','consolidate') and type(attempt) is int and attempt>1
        if self.queue_retry:
            self.counters['queue_retry_attempts']=1
            previous=self.retry_inputs.get('previous_error') or ''
            key='queue_retry_after_semantic_reject' if previous.startswith('SemanticReviewRejected:') else 'queue_retry_after_other_error' if previous else 'queue_retry_cause_unknown'
            self.counters[key]=1

    def bind(self,model):
        self.meta=route_metadata(model)
        with self.trace.store.transaction(write=True) as db:
            previous=db.execute('SELECT route_profile,code_version,route_epoch FROM memory_thought_runs '
                'WHERE route_profile IS NOT NULL AND id!=? ORDER BY rowid DESC LIMIT 1',(self.trace.id,)).fetchone()
            rowid=db.execute('SELECT rowid FROM memory_thought_runs WHERE id=?',(self.trace.id,)).fetchone()[0]
            epoch=previous[2] if previous and previous[0]==self.meta['route_profile'] and previous[1]==self.meta['code_version'] else rowid
            db.execute('UPDATE memory_thought_runs SET routing=?,route_profile=?,code_version=?,route_epoch=? WHERE id=?',
                (json.dumps(self.meta,sort_keys=True),self.meta['route_profile'],self.meta['code_version'],epoch,self.trace.id))

    def call_started(self,db,call_id,model,request,stage):
        meta=route_metadata(model,request.usage_task or request.task)
        epoch=db.execute('SELECT route_epoch FROM memory_thought_runs WHERE id=?',(self.trace.id,)).fetchone()[0]
        self.last_call=call_id
        kind='format' if self.format else 'semantic' if self.semantic else 'technical' if self.technical else 'queue_retry' if self.queue_retry else None
        db.execute('UPDATE memory_thought_model_calls SET requested_model=?,requested_reasoning_effort=?,route_profile=?,code_version=?,'
            'route_epoch=?,purpose=?,repair_kind=?,cycle_id=? WHERE id=?',
            (meta['requested_model'],meta['requested_reasoning_effort'],meta['route_profile'],meta['code_version'],epoch,
             self.purposes.get(self.trace._request_hash),kind,None if stage=='Construct thought' else self.active,call_id))

    def event(self,db,stage,details):
        task=details.get('task')
        if stage=='query_outcome':
            self.counters['query_outputs']+=int(details.get('answer_accepted') is True)
        elif stage=='publication':
            self.counters['publications']+=len(details.get('thought',{}))>0
        elif stage=='model_step_reused':
            self.reused[(details.get('request_hash'),details.get('response_hash'))]=details.get('source_call_id')
        elif stage=='model_input':
            self.purposes[details.get('request_hash')]=details.get('purpose')
            self.inputs[details.get('request_hash')]=json.dumps(details.get('_bindings',[]),ensure_ascii=False,sort_keys=True,separators=(',',':'))
        elif stage=='model_contract_repair':
            origin,actual=self._origin(db,details,task)
            if actual:
                db.execute('UPDATE memory_thought_model_calls SET format_error=1,semantic_verdict=NULL WHERE id=?',(origin['id'],))
            else:self.counters['cached_format_errors']+=1
            self.format=True
        elif stage in ('semantic_construct_repair','source_batch_semantic_repair'):
            self.semantic=True;self.parent=self.active
            self.group=stage=='source_batch_semantic_repair' or self.group
            self.counters[stage]+=1
        elif stage=='model_output':
            self.format=False
            if task=='Construct thought' and details.get('has_draft_batch'):
                self._construct(db,details)
            elif task in ('Verify thought','Verify source thought batch'):
                verdict=details.get('verdict')
                origin,actual=self._origin(db,details,task)
                review_id=origin['id'] if origin else None
                if actual and review_id:
                    db.execute('UPDATE memory_thought_model_calls SET semantic_verdict=? WHERE id=?',(verdict,review_id))
                if not actual:self._note_reuse(db,task,origin)
                if verdict=='reject':
                    if task=='Verify source thought batch':self.draft_origins.clear()
                    elif self.active_origin is not None:
                        # None represents unknown creators, not one shared creator.
                        # Dropping it could relabel an independent cached draft.
                        for values in self.draft_origins.values():values.discard(self.active_origin)
                if task=='Verify thought' and self.active:
                    db.execute('UPDATE memory_thought_draft_cycles SET first_review=coalesce(first_review,?),last_review=?,last_run_id=?,outcome=?, '
                        'rejection_call_id=CASE WHEN ?=\'reject\' THEN ? ELSE rejection_call_id END WHERE id=?',
                        (verdict,verdict,self.trace.id,'unresolved' if verdict=='reject' else
                         'accepted_after_repair' if self.semantic else 'accepted',verdict,review_id,self.active))
                    if verdict=='accept' and not self.group:self.semantic=False
                    if verdict=='accept':self.technical=False
                if task=='Verify source thought batch' and verdict=='accept':self.semantic=False;self.group=False
        db.execute('UPDATE memory_thought_runs SET repair_counters=? WHERE id=?',(json.dumps(dict(self.counters)),self.trace.id))

    def _origin(self,db,details,task):
        key=(details.get('request_hash'),details.get('response_hash'))
        binding=self.inputs.get(key[0])
        query='SELECT * FROM memory_thought_model_calls WHERE stage=? AND request_hash=? AND response_hash=? AND bindings=?'
        args=(task,key[0],key[1],binding)
        if key in self.reused:
            hint=self.reused.pop(key)
            if hint is not None:
                row=db.execute(query+' AND id=?',(*args,hint)).fetchone()
                return row,False
        elif self.last_call:
            row=db.execute(query+' AND id=?',(*args,self.last_call)).fetchone()
            if row:return row,True
        rows=db.execute(query+' LIMIT 2',args).fetchall()
        return (rows[0] if len(rows)==1 else None),False

    def _note_reuse(self,db,task,origin):
        self.counters['cached_constructs_resumed' if task=='Construct thought' else 'cached_reviews_resumed']+=1
        if origin is None:self.counters['unmatched_cached_constructs' if task=='Construct thought' else 'unmatched_cached_reviews']+=1
        detail={'task':task,'source_call_id':origin['id'] if origin else None,
            'requested_origin_model':origin['requested_model'] if origin else None,
            'actual_origin_model':(origin['actual_model'] or (json.loads(origin['usage']).get('actual_model') if origin['usage'] else None)) if origin else None,
            'requested_origin_reasoning_effort':origin['requested_reasoning_effort'] if origin else None,
            'origin_route_profile':origin['route_profile'] if origin else None,
            'origin_metadata_known':bool(origin and origin['requested_model'])}
        db.execute('INSERT INTO memory_thought_trace_events(run_id,stage,data,recorded_at) VALUES(?,?,?,?)',
            (self.trace.id,'checkpoint_origin',json.dumps({'details':detail},sort_keys=True),
             datetime.now(timezone.utc).isoformat()))

    def _construct(self,db,details):
        row,actual=self._origin(db,details,'Construct thought')
        self.active_origin=row['id'] if row else None
        for text_hash in details.get('draft_text_hashes',[]):self.draft_origins[text_hash].add(self.active_origin)
        if not actual:
            self._note_reuse(db,'Construct thought',row)
            self.active=row['cycle_id'] if row else None
            if self.active:
                prior=db.execute('SELECT outcome FROM memory_thought_draft_cycles WHERE id=?',(self.active,)).fetchone()
                self.technical=bool(prior and prior[0] in ('technical_failure','interrupted'))
            else:self.counters['cached_constructs_without_tracked_cycle']+=1
            return
        self.active=row['id']
        parent=self.parent if self.semantic else None
        if parent:db.execute("UPDATE memory_thought_draft_cycles SET outcome='reconstructed' WHERE id=?",(parent,))
        db.execute('INSERT OR IGNORE INTO memory_thought_draft_cycles VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL,?,?,NULL)',
            (self.active,row['id'],self.trace.id,self.trace.id,self.trace.operation,
             self.purposes.get(details.get('request_hash')),row['route_profile'],row['route_epoch'],row['started_at'],
             'pending',parent))
        db.execute('UPDATE memory_thought_model_calls SET cycle_id=? WHERE id=?',(self.active,row['id']))
        self.parent=self.active if self.group else None

    def publication_origin(self,db,details):
        from .thought_payload import decode_payload
        ref=details.get('thought',{})
        if not ref:return None
        row=db.execute('SELECT payload FROM memory_thought_versions WHERE thought_id=? AND version=?',
            (ref.get('thought_id'),ref.get('version'))).fetchone()
        if row is None:return None
        text_hash=hashlib.sha256(decode_payload(row[0])['text'].strip().encode()).hexdigest()
        origins=self.draft_origins.get(text_hash,set())
        return next(iter(origins)) if len(origins)==1 else None

    def finish(self,db,status):
        if not self.active:return
        row=db.execute('SELECT outcome FROM memory_thought_draft_cycles WHERE id=?',(self.active,)).fetchone()
        if row and row[0]=='pending' and status!='running':
            interrupted=db.execute("SELECT 1 FROM memory_thought_model_calls WHERE run_id=? AND status='interrupted' LIMIT 1",(self.trace.id,)).fetchone()
            outcome='interrupted' if interrupted else 'technical_failure' if status in ('failed','retry_required') else 'unresolved'
            db.execute('UPDATE memory_thought_draft_cycles SET outcome=? WHERE id=?',(outcome,self.active))


def _tokens(rows):
    result={}
    for key in TOKEN_FIELDS:
        values=[(json.loads(row['usage']).get('tokens') or {}).get(key) if row['usage'] else None for row in rows]
        known=[value for value in values if type(value) is int and value>=0]
        result[key]={'reported_total':sum(known),'reported_calls':len(known),'unknown_calls':len(values)-len(known)}
    return result


def repair_statistics(store,model=None,*,limit=2000):
    if type(limit) is not int or not 1<=limit<=10000:raise ValueError('bounded repair stats limit required')
    profile=getattr(model,'route_profile_key',None)
    with store.transaction() as db:
        columns={row[1] for row in db.execute('PRAGMA table_info(memory_thought_model_calls)')}
        if 'route_epoch' not in columns:
            return {'summary':{'construct_cycles':0},'coverage':{'tracking':'not_started','legacy_model_metadata':'unknown'}}
        latest=db.execute('SELECT route_epoch,route_profile,code_version,started_at FROM memory_thought_runs '
            'WHERE route_profile IS ? ORDER BY started_at DESC,id DESC LIMIT 1',(profile,)).fetchone()
        if latest is None:return {'summary':{'construct_cycles':0},'coverage':{'tracking':'no_observed_calls','legacy_model_metadata':'unknown'}}
        epoch=latest['route_epoch']
        rows=[dict(row) for row in db.execute('SELECT c.*,r.operation FROM memory_thought_model_calls c JOIN memory_thought_runs r ON r.id=c.run_id '
            'WHERE c.route_epoch=? ORDER BY c.started_at DESC,c.id DESC LIMIT ?',(epoch,limit+1))]
        truncated=len(rows)>limit;rows=rows[:limit]
        cycles=[dict(row) for row in db.execute('SELECT * FROM memory_thought_draft_cycles WHERE route_epoch=? '
            'ORDER BY started_at DESC,id DESC LIMIT ?',(epoch,limit+1))]
        cycle_truncated=len(cycles)>limit;cycles=cycles[:limit]
        summary={'construct_cycles':len(cycles),'first_review_accept':sum(row['first_review']=='accept' for row in cycles),
            'first_review_reject':sum(row['first_review']=='reject' for row in cycles),
            'unreviewed_cycles':sum(row['first_review'] is None for row in cycles),
            'accepted_after_repair':sum(row['outcome']=='accepted_after_repair' for row in cycles),
            'semantic_reconstructions':sum(row['repair_of'] is not None for row in cycles),
            'unresolved_cycles':sum(row['outcome']=='unresolved' for row in cycles),
            'interrupted_cycles':sum(row['outcome']=='interrupted' for row in cycles),
            'calls':len(rows),'queue_retry_calls':sum(row['repair_kind']=='queue_retry' for row in rows),'repair_calls':sum(row['repair_kind'] is not None for row in rows),
            'format_retries':sum(bool(row['format_error']) for row in rows),
            'semantic_accept_calls':sum(row['semantic_verdict']=='accept' for row in rows),
            'semantic_reject_calls':sum(row['semantic_verdict']=='reject' for row in rows),
            'source_batch_accept_calls':sum(row['stage']=='Verify source thought batch' and row['semantic_verdict']=='accept' for row in rows),
            'source_batch_reject_calls':sum(row['stage']=='Verify source thought batch' and row['semantic_verdict']=='reject' for row in rows),
            'technical_failed_calls':sum(row['status']=='failed' for row in rows),
            'interrupted_calls':sum(row['status']=='interrupted' for row in rows),
            'timed_calls':sum(row['duration_ms'] is not None for row in rows),
            'timing_unknown_calls':sum(row['duration_ms'] is None for row in rows),
            'duration_ms':sum(row['duration_ms'] or 0 for row in rows),
            'repair_duration_ms':sum((row['duration_ms'] or 0) for row in rows if row['repair_kind'])}
        grouped=defaultdict(list)
        for row in rows:grouped[(row['operation'],row['purpose'],row['stage'],row['requested_model'],row['actual_model'])].append(row)
        stages=[{'operation':key[0],'purpose':key[1],'stage':key[2],'requested_model':key[3],'actual_model':key[4],
            'calls':len(group),'semantic_accept':sum(row['semantic_verdict']=='accept' for row in group),
            'semantic_reject':sum(row['semantic_verdict']=='reject' for row in group),
            'format_errors':sum(bool(row['format_error']) for row in group),
            'technical_failures':sum(row['status']=='failed' for row in group),
            'repair_calls':sum(row['repair_kind'] is not None for row in group),
            'duration_ms':sum(row['duration_ms'] or 0 for row in group),'tokens':_tokens(group)} for key,group in grouped.items()]
        examples=[]
        for cycle in cycles:
            if cycle['repair_of'] is None or len(examples)>=3:continue
            before=db.execute('SELECT construct_call_id,rejection_call_id FROM memory_thought_draft_cycles WHERE id=?',(cycle['repair_of'],)).fetchone()
            if before is None:continue
            refs={}
            for name,key in (('before',before['construct_call_id']),('review',before['rejection_call_id']),('after',cycle['construct_call_id'])):
                call=db.execute('SELECT id,response_hash FROM memory_thought_model_calls WHERE id=?',(key,)).fetchone()
                refs[name]=dict(call) if call else None
            reason=None
            if refs['review']:
                payload=db.execute('SELECT body FROM memory_thought_model_payloads WHERE hash=?',(refs['review']['response_hash'],)).fetchone()
                if payload:
                    try:reason=json.loads(zlib.decompress(payload[0])).get('text')
                    except (ValueError,zlib.error):reason=None
            examples.append(dict(refs,reason=reason[:2000] if isinstance(reason,str) else None,outcome=cycle['outcome']))
        runs=[dict(row) for row in db.execute('SELECT id,operation,status,repair_counters FROM memory_thought_runs WHERE route_epoch=? '
            'ORDER BY started_at DESC,id DESC LIMIT ?',(epoch,limit+1))]
        run_truncated=len(runs)>limit;runs=runs[:limit]
        counters=Counter();outcomes=Counter();operations=defaultdict(Counter)
        for run in runs:
            seen=json.loads(run['repair_counters'] or '{}');counters.update(seen)
            outcome=run['status']
            if outcome=='running':outcome='running_or_interrupted_unknown'
            elif outcome=='retry_required':outcome='unresolved'
            elif outcome=='completed' and (seen.get('semantic_construct_repair') or seen.get('source_batch_semantic_repair')):outcome='completed_after_repair'
            outcomes[outcome]+=1;operations[run['operation']][outcome]+=1
            if run['operation']=='query' and run['status']!='running' and not seen.get('query_outputs'):
                operations['query']['no_accepted_output']+=1
        summary.update({key:counters.get(key,0) for key in ('queue_retry_attempts','queue_retry_after_semantic_reject','queue_retry_after_other_error','queue_retry_cause_unknown','cached_format_errors','cached_constructs_resumed','cached_reviews_resumed','unmatched_cached_reviews','cached_constructs_without_tracked_cycle','unmatched_cached_constructs','source_batch_semantic_repair')})
        models=defaultdict(list)
        for row in rows:models[(row['requested_model'],row['actual_model'],row['requested_reasoning_effort'])].append(row)
        by_model=[{'requested_model':key[0],'actual_model':key[1],'requested_reasoning_effort':key[2],
            'calls':len(group),'duration_ms':sum(row['duration_ms'] or 0 for row in group),'tokens':_tokens(group)} for key,group in models.items()]
        epoch_start=db.execute('SELECT started_at FROM memory_thought_runs WHERE rowid=?',(epoch,)).fetchone()
        run_ids=[run['id'] for run in runs]
        publications=[];seen_refs=set()
        for event in db.execute("SELECT run_id,data,recorded_at FROM memory_thought_trace_events WHERE stage='publication' "
            'AND run_id IN (SELECT value FROM json_each(?)) ORDER BY id DESC LIMIT 10',(json.dumps(run_ids),)):
            detail=json.loads(event['data']).get('details') or {}
            ref=detail.get('thought')
            if ref and (ref.get('thought_id'),ref.get('version')) not in seen_refs:
                seen_refs.add((ref.get('thought_id'),ref.get('version')))
                publications.append({'run_id':event['run_id'],'thought':ref,'construction_call_id':detail.get('construction_call_id'),'recorded_at':event['recorded_at']})
        queue_retry_causes=[json.loads(row[0]).get('details') for row in db.execute("SELECT data FROM memory_thought_trace_events WHERE stage='queue_retry_attempt' "
            'AND run_id IN (SELECT value FROM json_each(?)) ORDER BY id DESC LIMIT 10',(json.dumps(run_ids),))]
        cached_origins=[json.loads(row[0]).get('details') for row in db.execute("SELECT data FROM memory_thought_trace_events WHERE stage='checkpoint_origin' "
            'AND run_id IN (SELECT value FROM json_each(?)) ORDER BY id DESC LIMIT 20',(json.dumps(run_ids),))]
    reviewed=summary['first_review_accept']+summary['first_review_reject']
    rates={'repair_construct_share':{'numerator':summary['semantic_reconstructions'],'denominator':len(cycles),
        'fraction':summary['semantic_reconstructions']/len(cycles) if cycles else None},
        'first_local_semantic_accept_share':{'numerator':summary['first_review_accept'],'denominator':reviewed,
        'fraction':summary['first_review_accept']/reviewed if reviewed else None}}
    return {'scope':{'route_profile':latest['route_profile'],'code_version':latest['code_version'],
        'configured_requested':route_metadata(model),'route_epoch':epoch,'since':epoch_start[0] if epoch_start else None,'boundary':'first_observed_run_for_configured_profile_and_code'},
        'summary':summary,'rates':rates,'coverage':{'calls_scanned':len(rows),'cycles_scanned':len(cycles),'runs_scanned':len(runs),'truncated':truncated or cycle_truncated or run_truncated,
            'limit':limit,'window_notice':'Calls, Construct cycles and runs are independently bounded latest windows; do not divide token totals by cycle counts as if they were identical cohorts.',
            'requested_model_known_calls':sum(row['requested_model'] is not None for row in rows),
            'actual_model_reported_calls':sum(row['actual_model'] is not None for row in rows),'denominator':'validated Construct batches; local first semantic review, not individual thoughts',
            'legacy_model_metadata':'unknown; historical rows were not relabeled',
            'repair_overhead_definition':'additional actual calls after semantic/format repair or an explicitly recorded worker retry; no token estimates',
            'timing_notice':'duration_ms sums possibly overlapping calls; it is not elapsed wall-clock time',
            'first_review_notice':'first semantic review per actual Construct cycle, including queue retries; not first-queue-attempt success'},
        'tokens':_tokens(rows),'repair_tokens':_tokens([row for row in rows if row['repair_kind']]),
        'by_stage':stages,'by_model':by_model,'examples':examples,'cached_origins':cached_origins,'queue_retry_causes':queue_retry_causes,'run_outcomes':dict(outcomes),
        'by_operation_outcome':{key:dict(values) for key,values in operations.items()},'publication_refs':publications,'judgment_notice':'Rejections are reviewer judgments, not established primary-model errors.'}
