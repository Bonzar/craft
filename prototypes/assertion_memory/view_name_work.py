"""Narrow per-view editorial work, executed by the existing LinkWorker.

Capture jobs require a real assertion XOR event. Scope-derived work owns a
semantic frontier and one scope lease; it cannot independently retry each view's
editorial phase. Accepted name rows are immutable and cannot hold a lease.
This table therefore holds only operational intent/checkpoints, never knowledge.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .processing_control import paused as processing_paused
from .view_names import (PROTOCOL, REVIEW_FORMAT_POLICY, NameReviewContractError, NameProviderUnavailable,
    NameReviewRejected, _definition_sha, _digest, _context, _json, _title, candidate_key,
    append, prepare, review, target, BINDING_POLICY, _normal_record)
from .model import ModelError, format_prompt
from .presentation_titles import CAPTION_POLICY


class NameYield(BaseException):
    """Cooperative provider boundary, deliberately not a failed model response."""


class NameLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class NameClaim:
    view_id: str
    token: str
    request: dict
    phase: str
    checkpoint: dict
    attempts: int


@dataclass(frozen=True)
class NameTask:
    view_id: str
    status: str
    kind: str = 'view_name'
    error: str | None = None


def ensure_schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS memory_view_name_work (
        view_id TEXT PRIMARY KEY REFERENCES memory_views(id), request TEXT NOT NULL,
        claimed_request TEXT, status TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'writer',
        checkpoint TEXT NOT NULL DEFAULT '{}', attempts INTEGER NOT NULL DEFAULT 0,
        failures INTEGER NOT NULL DEFAULT 0, available_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        error_kind TEXT, error TEXT, lease_token TEXT, lease_until TEXT,
        turn INTEGER NOT NULL DEFAULT 0, completed_name_id INTEGER REFERENCES memory_view_names(id))''')
    c.execute('CREATE INDEX IF NOT EXISTS memory_view_name_ready ON memory_view_name_work(status,available_at,turn)')
    c.execute('CREATE TABLE IF NOT EXISTS memory_queue_schedule(id INTEGER PRIMARY KEY CHECK(id=1),source_streak INTEGER NOT NULL)')
    c.execute('INSERT OR IGNORE INTO memory_queue_schedule(id,source_streak) VALUES(1,0)')
    columns={row[1] for row in c.execute('PRAGMA table_info(memory_queue_schedule)')}
    for name,kind in (('name_cursor','INTEGER'),('name_highwater','INTEGER'),('name_caption_policy','TEXT')):
        if name not in columns:c.execute('ALTER TABLE memory_queue_schedule ADD COLUMN '+name+' '+kind)


class NameWorkQueue:
    def __init__(self, store, *, clock=None, lease_seconds=300):
        self.store=store
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.lease_seconds=lease_seconds
        with store.transaction(write=True) as c:ensure_schema(c)

    def _time(self, after=0):
        return (self.clock()+timedelta(seconds=after)).astimezone(timezone.utc).isoformat(timespec='microseconds')

    def _obsolete(self,engine,view_id):
        from .background_overviews import technical_root
        return technical_root(engine,view_id)

    def _retire(self,c,view_id):
        c.execute("UPDATE memory_view_name_work SET status='unresolved',error_kind='obsolete_navigation_policy',error='Controller navigation is operational metadata; no content name is produced',lease_token=NULL,lease_until=NULL,updated_at=? WHERE view_id=?",(self._time(),view_id))
        return NameTask(view_id,'unresolved',error='obsolete_navigation_policy')

    def _accepted(self,c,view_id,definition_sha,after_name_id=0,*,version_id=None,semantic_content_sha256=None):
        definition=c.execute('SELECT definition FROM memory_views WHERE id=?',(view_id,)).fetchone()
        if definition is None:return None
        question=json.loads(definition[0])['query']
        rows=c.execute('SELECT * FROM memory_view_names WHERE view_id=? AND definition_sha256=? '
            'AND protocol=? AND id>? AND version_id IS ? AND semantic_content_sha256 IS ? ORDER BY id DESC',
            (view_id,definition_sha,PROTOCOL,after_name_id,version_id,semantic_content_sha256))
        return next((row for row in rows if _normal_record(row,question)),None)

    def enqueue(self, engine, spec, *, authorized_scopes, operation, purpose=None, after_name_id=None):
        if self._obsolete(engine,spec.id):
            with self.store.transaction(write=True) as c:
                row=c.execute('SELECT status,lease_until FROM memory_view_name_work WHERE view_id=?',(spec.id,)).fetchone()
                if row and (row['status']!='running' or row['lease_until']<=self._time()):self._retire(c,spec.id)
            return
        if not spec.scopes or not set(spec.scopes)<=set(authorized_scopes):
            return  # Unscoped questions remain visible; no implicit global naming authority.
        request={'definition_sha256':_definition_sha(spec),'authorized_scopes':sorted(set(authorized_scopes)),
            'operation':operation,'protocol':PROTOCOL,'purpose':purpose,'review_format_policy':REVIEW_FORMAT_POLICY,
            'caption_policy':CAPTION_POLICY}
        if after_name_id is not None:
            if type(after_name_id) is not int or after_name_id<0:raise ValueError('name generation requires nonnegative journal frontier')
            request['after_name_id']=after_name_id
        version,semantic_sha=target(engine,spec.id)
        request.update(binding_policy=BINDING_POLICY,target_version_id=version.id if version else None,semantic_content_sha256=semantic_sha)
        encoded=_json(request)
        with self.store.transaction(write=True) as c:
            accepted=self._accepted(c,spec.id,request['definition_sha256'],request.get('after_name_id',0),
                version_id=request.get('target_version_id'),semantic_content_sha256=request.get('semantic_content_sha256'))
            if accepted:
                c.execute("UPDATE memory_view_name_work SET request=?,status='completed',completed_name_id=?, "
                    "error=NULL,error_kind=NULL,lease_token=NULL,lease_until=NULL,updated_at=? "
                    "WHERE view_id=? AND (status!='running' OR lease_until<=?)",
                    (encoded,accepted['id'],self._time(),spec.id,self._time()))
                return
            row=c.execute('SELECT * FROM memory_view_name_work WHERE view_id=?',(spec.id,)).fetchone()
            if row:
                old=json.loads(row['request'])
                # An operational policy alone does not change producer intent. In
                # particular an old producer must not erase explicit recovery audit.
                previous=json.loads(row['checkpoint']).get('recovery',{}).get('previous_request',{})
                intent=lambda value:{k:v for k,v in value.items() if k!='review_format_policy'}
                if row['status'] not in ('completed','unresolved') and (intent(old)==intent(request) or intent(previous)==intent(request)):return
            if row and row['status']=='running':
                # Preserve active ownership/checkpoint; the desired revision wins
                # only after its owner yields, never by overwriting a live lease.
                c.execute('UPDATE memory_view_name_work SET request=?,updated_at=? WHERE view_id=?',
                    (encoded,self._time(),spec.id));return
            c.execute('''INSERT INTO memory_view_name_work(view_id,request,status,available_at,updated_at)
                VALUES(?,?,'pending',?,?) ON CONFLICT(view_id) DO UPDATE SET request=excluded.request,
                status='pending',phase='writer',checkpoint='{}',attempts=0,failures=0,
                available_at=excluded.available_at,updated_at=excluded.updated_at,error=NULL,error_kind=NULL,
                lease_token=NULL,lease_until=NULL,completed_name_id=NULL''',(spec.id,encoded,self._time(),self._time()))

    def claim(self, engine, *, with_model):
        now=self._time()
        with self.store.transaction(write=True) as c:
            if processing_paused(c):
                return None
            if not with_model:
                c.execute("UPDATE memory_view_name_work SET status='waiting_model',error_kind='unavailable',"
                    "error='No configured naming provider',updated_at=? WHERE status IN ('pending','retry')",(now,))
                return None
            row=c.execute("SELECT * FROM memory_view_name_work WHERE "
                "(status IN ('pending','retry','waiting_model') AND available_at<=?) OR "
                "(status='running' AND lease_until<=?) ORDER BY turn,updated_at,view_id LIMIT 1",(now,now)).fetchone()
            if row is None:return None
            if self._obsolete(engine,row['view_id']):return self._retire(c,row['view_id'])
            request=json.loads(row['request'])
            version,semantic_sha=target(engine,row['view_id'])
            request.update(binding_policy=BINDING_POLICY,target_version_id=version.id if version else None,semantic_content_sha256=semantic_sha)
            encoded=_json(request)
            if encoded!=row['request']:
                c.execute('UPDATE memory_view_name_work SET request=? WHERE view_id=?',(encoded,row['view_id']))
            accepted=self._accepted(c,row['view_id'],request['definition_sha256'],request.get('after_name_id',0),version_id=request.get('target_version_id'),semantic_content_sha256=request.get('semantic_content_sha256'))
            if accepted:
                c.execute("UPDATE memory_view_name_work SET status='completed',completed_name_id=?,"
                    "lease_token=NULL,lease_until=NULL,error=NULL,error_kind=NULL WHERE view_id=?",(accepted[0],row['view_id']))
                return NameTask(row['view_id'],'completed')
            changed=row['claimed_request'] is not None and row['claimed_request']!=encoded
            changed=changed or encoded!=row['request']
            phase='writer' if changed else row['phase']
            checkpoint={} if changed else json.loads(row['checkpoint'])
            token=uuid4().hex
            c.execute("UPDATE memory_view_name_work SET status='running',claimed_request=request,phase=?,checkpoint=?,"
                "attempts=?,lease_token=?,lease_until=?,updated_at=?,turn=(SELECT coalesce(max(turn),0)+1 FROM memory_view_name_work) "
                'WHERE view_id=?',(phase,_json(checkpoint),0 if changed else row['attempts'],token,
                self._time(self.lease_seconds),now,row['view_id']))
            return NameClaim(row['view_id'],token,request,phase,checkpoint,0 if changed else row['attempts'])

    def _owned(self,c,claim,*,current=True):
        row=c.execute('SELECT * FROM memory_view_name_work WHERE view_id=? AND lease_token=? AND lease_until>?',
            (claim.view_id,claim.token,self._time())).fetchone()
        if row is None:raise NameLeaseLost('editorial lease lost')
        if current and row['request']!=_json(claim.request):raise NameLeaseLost('editorial request superseded')
        return row

    def renew(self,claim):
        with self.store.transaction(write=True) as c:
            self._owned(c,claim)
            c.execute('UPDATE memory_view_name_work SET lease_until=? WHERE view_id=?',
                (self._time(self.lease_seconds),claim.view_id))

    def save_response(self,claim,key,response):
        with self.store.transaction(write=True) as c:
            self._owned(c,claim)
            c.execute('INSERT OR IGNORE INTO memory_refresh_generations VALUES(?,?)',(key,response))

    def release(self,claim,*,checkpoint=None,phase=None,error=None,deterministic=False,terminal=False,error_kind=None):
        with self.store.transaction(write=True) as c:
            row=self._owned(c,claim,current=False)
            changed=row['request']!=_json(claim.request)
            cp={} if changed else checkpoint if checkpoint is not None else json.loads(row['checkpoint'])
            phase='writer' if changed else phase or row['phase']
            attempts=0 if changed else row['attempts']+int(bool(error) and deterministic)
            failures=0 if changed or not error else row['failures']+1
            state='pending' if changed or not error else 'unresolved' if terminal else 'retry'
            delay=min(300,2**min(failures,9)) if state=='retry' else 0
            c.execute('UPDATE memory_view_name_work SET status=?,phase=?,checkpoint=?,attempts=?,failures=?, '
                'error_kind=?,error=?,available_at=?,updated_at=?,lease_token=NULL,lease_until=NULL WHERE view_id=?',
                (state,phase,_json(cp),attempts,failures,None if changed else error_kind or ('editorial' if deterministic else 'transient' if error else None),
                 str(error)[:1000] if error else None,self._time(delay),self._time(),claim.view_id))
            return NameTask(claim.view_id,state,error=str(error)[:1000] if error else None)

    def complete(self,engine,claim,approved):
        with self.store.transaction(write=True) as c:
            self._owned(c,claim)
            from .publication_fences import check_guard
            check_guard(engine,c)
            if self._obsolete(engine,claim.view_id):return self._retire(c,claim.view_id)
            record=append(engine,approved,expected_definition_sha=claim.request['definition_sha256'],
                expected_previous_name_id=approved['previous_name_id'])
            c.execute("UPDATE memory_view_name_work SET status='completed',completed_name_id=?,checkpoint='{}',"
                "error=NULL,error_kind=NULL,lease_token=NULL,lease_until=NULL,updated_at=? WHERE view_id=?",
                (record['id'],self._time(),claim.view_id))
            return NameTask(claim.view_id,'completed')

    def recover_abandoned(self):
        # Only the existing supervisor's exclusive flock authorizes this method.
        with self.store.transaction(write=True) as c:
            c.execute("UPDATE memory_view_name_work SET status='pending',lease_token=NULL,lease_until=NULL,"
                "available_at=? WHERE status='running'",(self._time(),))

    def seconds_until_work(self,*,with_model):
        if not with_model:return None
        with self.store.transaction() as c:
            recovery=c.execute('SELECT name_caption_policy,name_cursor,name_highwater FROM memory_queue_schedule WHERE id=1').fetchone()
            if recovery[0]!=CAPTION_POLICY or recovery[2] is None or (recovery[1] or 0)<recovery[2]:return 0
            value=c.execute("SELECT min(CASE WHEN status='running' THEN lease_until ELSE available_at END) "
                "FROM memory_view_name_work WHERE status IN ('pending','retry','waiting_model','running')").fetchone()[0]
        return max(0,(datetime.fromisoformat(value)-datetime.fromisoformat(self._time())).total_seconds()) if value else None

    def status(self,*,scope=None,offset=0,limit=100):
        if type(offset)is not int or offset<0 or type(limit)is not int or not 1<=limit<=1000:raise ValueError('invalid naming status page')
        clause='(? IS NULL OR EXISTS(SELECT 1 FROM json_each(w.request,\'$.authorized_scopes\') WHERE value=?))'
        with self.store.transaction() as c:
            counts={r[0]:r[1] for r in c.execute('SELECT status,count(*) FROM memory_view_name_work w WHERE '+clause+' GROUP BY status',(scope,scope))}
            rows=c.execute('SELECT view_id,status,phase,attempts,failures,error_kind,error,available_at,updated_at,completed_name_id '
                'FROM memory_view_name_work w WHERE '+clause+' ORDER BY view_id LIMIT ? OFFSET ?',(scope,scope,limit,offset)).fetchall()
        total=sum(counts.values());end=offset+len(rows)
        return {'counts':counts,'total':total,'entries':[dict(r) for r in rows],'offset':offset,
            'next_offset':end if end<total else None}

    def reconcile_page(self,engine,limit=64):
        """Resume one metadata page per turn after a caption-policy change."""
        with self.store.transaction(write=True) as c:
            state=c.execute('SELECT name_caption_policy,name_cursor,name_highwater FROM memory_queue_schedule WHERE id=1').fetchone()
            cursor=state[1] or 0;high=state[2]
            if state[0]!=CAPTION_POLICY:
                cursor=0;high=None
            if high is None:high=c.execute('SELECT coalesce(max(rowid),0) FROM memory_views').fetchone()[0]
            c.execute('UPDATE memory_queue_schedule SET name_caption_policy=?,name_cursor=?,name_highwater=? WHERE id=1',
                (CAPTION_POLICY,cursor,high))
            if cursor>=high:return False
            rows=c.execute('SELECT rowid,definition,current_id FROM memory_views WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?',
                (cursor,high,limit)).fetchall()
            from .engine import ViewSpec
            for row in rows:
                spec=ViewSpec(**json.loads(row['definition']))
                intent=c.execute('SELECT request,checkpoint,status FROM memory_view_name_work WHERE view_id=?',(spec.id,)).fetchone()
                prior=json.loads(intent['request']) if intent else {}
                needs=(not intent or prior.get('caption_policy')!=CAPTION_POLICY or
                    prior.get('target_version_id')!=row['current_id'] or prior.get('binding_policy')!=BINDING_POLICY)
                if row['current_id'] is not None and needs:
                    self._recover_caption(engine,spec,intent)
                cursor=row['rowid']
            c.execute('UPDATE memory_queue_schedule SET name_cursor=? WHERE id=1',(cursor if rows else high,))
            return cursor<high

    def _recover_caption(self,engine,spec,intent):
        previous=({'request':json.loads(intent['request']),'checkpoint':json.loads(intent['checkpoint']),
            'status':intent['status']} if intent else None)
        self.enqueue(engine,spec,authorized_scopes=spec.scopes,operation='caption-policy-recovery/'+CAPTION_POLICY)
        if previous:
            with self.store.transaction(write=True) as c:
                c.execute("UPDATE memory_view_name_work SET checkpoint=json_set(checkpoint,'$.recovery',json(?)) "
                    "WHERE view_id=? AND status!='running' AND json_extract(request,'$.caption_policy')=?",
                    (_json(previous),spec.id,CAPTION_POLICY))


def _machine_definition(c,spec):
    if not spec.scopes:return False
    def hashed(value,n):return hashlib.sha256(json.dumps(value,ensure_ascii=False).encode()).hexdigest()[:n]
    if spec.id=='theme-'+hashed([spec.query,spec.scopes,list(spec.root_ids)],32):return True
    for suffix in ([],['navigation_index']):
        if spec.id=='theme-group-'+hashed([spec.query,spec.scopes,list(spec.depends_on),*suffix],32):return True
    # Current request identities come from the authoritative route producer,
    # including its normalized temporal suffix, never an assumed digest length.
    if not spec.root_ids and not spec.depends_on and spec.max_seeds==8:
        from .question_routes import _key
        if spec.id==_key(spec.query,spec.scopes):return True
        # Pre-upgrade planner failures have no route. Their immutable failure
        # receipt supplies the actual requested cutoffs; do not guess dates from
        # the question or enumerate arbitrary temporal hash candidates.
        cutoffs=c.execute("SELECT DISTINCT valid_cutoff,recorded_cutoff FROM memory_view_checks WHERE view_id=? "
            "AND json_extract(overview_review,'$.failure_proof')=1 "
            "AND json_extract(overview_review,'$.failed_phase')='planning' "
            "AND query_snapshot IS NOT NULL "
            "AND coalesce(json_extract(query_snapshot,'$.requested_valid_as_of'),'')=valid_cutoff "
            "AND coalesce(json_extract(query_snapshot,'$.requested_recorded_as_of'),'')=recorded_cutoff",
            (spec.id,)).fetchall()
        for row in cutoffs:
            try:
                if spec.id==_key(spec.query,spec.scopes,valid_as_of=row['valid_cutoff'] or None,
                                recorded_as_of=row['recorded_cutoff'] or None):return True
            except (ValueError,TypeError):
                continue  # Unknown/corrupt historical cutoffs confer no machine identity.
    # The separate CLI _read_view producer is proven to use the 20-character
    # ASCII-JSON identity; retain that compatibility rather than misusing it for
    # current question_routes keys.
    if spec.id=='question-'+hashlib.sha256(json.dumps([spec.query,spec.scopes]).encode()).hexdigest()[:20]:return True
    if len(spec.scopes)==1:
        from .desktop import _json as desktop_json
        if spec.id=='desktop-'+hashlib.sha256(desktop_json([spec.scopes[0],spec.query]).encode()).hexdigest()[:24]:return True
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_question_route_versions'").fetchone()
    return bool(exists and c.execute("SELECT 1 FROM memory_question_route_versions WHERE view_id=? AND "
        "json_extract(plan,'$.reuse_view_id') IS NULL LIMIT 1",(spec.id,)).fetchone())


def _writer_turn(names,engine,claim,spec,cooperative):
    candidate=prepare(engine,spec,authorized_scopes=claim.request['authorized_scopes'],
        operation=claim.request['operation'],purpose=claim.request['purpose'],
        feedback=claim.checkpoint.get('feedback'),model=cooperative,
        version_id=claim.request.get('target_version_id'),require_current=True)
    compact={key:value for key,value in candidate.items() if key!='context'}
    return names.release(claim,phase='reviewer',checkpoint={**claim.checkpoint,'candidate':compact,
        'review_format_policy':REVIEW_FORMAT_POLICY})


def _reviewer_turn(names,engine,claim,spec,cooperative):
    candidate=dict(claim.checkpoint['candidate'])
    context=_context(engine,spec,claim.request['authorized_scopes'],claim.request['operation'],
        claim.request['purpose'],version_id=claim.request.get('target_version_id'),require_current=True)
    if _digest(context)!=candidate['context_sha256']:raise ValueError('naming context references changed')
    candidate['context']=context
    feedback=claim.checkpoint.get('review_formats',{}).get(candidate_key(candidate))
    approved=review(engine,candidate,model=cooperative,format_feedback=feedback,format_retry=False)
    for field in ('review_audit','semantic_audit','recovery'):
        if claim.checkpoint.get(field):approved['review'][field]=claim.checkpoint[field]
    return names.complete(engine,claim,approved)


def _retry_editorial(names,claim,error):
    checkpoint={**claim.checkpoint,'generation_epoch':claim.checkpoint.get('generation_epoch',0)+1}
    if isinstance(error,NameProviderUnavailable):
        checkpoint['provider_error']=str(error)[:1000]
        return names.release(claim,checkpoint=checkpoint,phase=claim.phase,error=error,error_kind='provider_unavailable')
    if isinstance(error,NameReviewContractError):
        key=error.audit['candidate_key'];formats=dict(checkpoint.get('review_formats',{}))
        if key not in formats:
            formats[key]=error.diagnostic
            checkpoint.update(review_formats=formats,
                review_audit=[*checkpoint.get('review_audit',[]),error.audit])
            return names.release(claim,checkpoint=checkpoint,phase='reviewer',error=error,deterministic=True)
    if isinstance(error,NameReviewRejected):
        checkpoint['semantic_audit']=[*checkpoint.get('semantic_audit',[]),{**error.audit,'verdict':error.verdict}]
        checkpoint['feedback']={'rejected_title':error.verdict['title'],'reason':error.verdict['reason'][:2000]}
    else:
        checkpoint['feedback']={'reason':str(error)[:2000]}
    checkpoint.pop('candidate',None);checkpoint.pop('review_formats',None)
    return names.release(claim,checkpoint=checkpoint,phase='writer',error=error,
        deterministic=isinstance(error,(NameReviewRejected,NameReviewContractError)),error_kind='editorial_retry')


def run_turn(worker,*,fairness=False):
    names,engine=worker.names,worker.engine;provider=engine.naming_model
    claim=names.claim(engine,with_model=provider is not None)
    if claim is None or isinstance(claim,NameTask):return claim
    worker._notify('claimed','name',claim.view_id,claim.token)
    worker.queue._refresh_turn_taken('editorial');generated=0
    def cooperative(request):
        nonlocal generated
        from .queue import _identity
        key=_identity('editorial-generation',PROTOCOL,CAPTION_POLICY,engine.pipeline_key,
            claim.checkpoint.get('generation_epoch',0),getattr(provider,'identity',type(provider).__qualname__),
            format_prompt(request))
        cached=worker.queue._refresh_generation(key);names.renew(claim)
        if cached is not None:return cached
        if generated>=1 or (getattr(worker,'lane','all')!='maintenance' and not fairness and worker.queue._ready_sources(with_model=worker.model is not None)):raise NameYield()
        response=provider(request);generated+=1
        if isinstance(response,str) and response.strip():names.save_response(claim,key,response)
        return response
    try:
        spec=engine._spec(claim.view_id)
        if _definition_sha(spec)!=claim.request['definition_sha256']:raise ValueError('naming definition changed')
        step=_writer_turn if claim.phase=='writer' else _reviewer_turn
        from .publication_fences import bind_guard
        with bind_guard(engine,lambda db:names._owned(db,claim)):
            return step(names,engine,claim,spec,cooperative)
    except NameYield:return names.release(claim)
    except NameLeaseLost:return NameTask(claim.view_id,'pending',error='editorial ownership lost')
    except Exception as error:
        from .queue import LeaseLost
        if isinstance(error,LeaseLost):return NameTask(claim.view_id,'pending',error='editorial ownership lost')
        try:return _retry_editorial(names,claim,error)
        except NameLeaseLost:return NameTask(claim.view_id,'pending',error='editorial ownership lost')
    finally:
        worker._notify('released','name',claim.view_id,claim.token)
