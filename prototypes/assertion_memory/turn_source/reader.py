"""Host-owned claim-scoped visible raw reader. The model receives no database API."""
from contextlib import contextmanager, closing
from pathlib import Path
from hashlib import sha256
import secrets
import os
import sqlite3
from dataclasses import dataclass
from contextvars import ContextVar
from ..source_admission import stamp, unchanged

from ..dialogue import source_role
from ..thought_processing_selection import canonical_session_id
from ..thought_source_policy import event_eligibility
from ..queue import _event


class SourceReadDenied(ValueError):
    pass


@dataclass(frozen=True)
class IssuedReference:
    kind: str
    identity: str
    start: int | None
    end: int | None
    digest: bytes
    order: tuple | None
    context_session: str | None = None
    version: int | None = None
    text: str | None = None


class EarlierDialogue:
    PAGE=8
    CHARS=12_000
    SCAN=32

    def __init__(self,path,request,primary,*,freeze_membership=False):
        self.path=Path(path).resolve(strict=True)
        self.request=request
        self.primary=primary
        self.primary_order=None
        from ..diagnostic_privacy import current_scope
        self.diagnostic_scope=current_scope()
        self.refs={}
        self.selected=set()
        self.owner_scope=None
        self.navigation=None
        self.budget_exhausted=False
        self.pages=0
        self.calls=0
        self.bytes_read=0  # Telemetry only; provider tokens are accounted separately.
        self.cursor,self.bound,self.started=None,None,False
        self._anchor=None  # Compatibility marker: never holds a connection.
        self._closed=False
        self._read_db=ContextVar("source_read_db",default=None)
        self._publishing=ContextVar("source_publication",default=False)
        self.watermark=None
        self.instance=None
        self._identity_fd=os.open(self.path,os.O_RDONLY)
        try:
            stat=os.fstat(self._identity_fd)
            self._identity=(stat.st_dev,stat.st_ino)
            self._initialize(freeze_membership)
        except BaseException:
            self.close()
            raise

    def _initialize(self,freeze_membership):
        manager=self.snapshot()
        request,primary=self.request,self.primary
        with manager as db:
            self.watermark,self.instance=stamp(db)
            target=self.row(db,primary.id)
            if (_event(target)!=primary or not self.visible(primary)
                    or (primary.id,primary.scope,primary.session_id,canonical_session_id(primary))
                    != (request.event_id,request.scope,request.capture_session_id,request.canonical_session_id)):
                raise SourceReadDenied('Invalid primary dialogue source')
            self.thought_watermark=db.execute('SELECT coalesce(max(rowid),0) FROM memory_thought_versions').fetchone()[0]
            self.highwater=db.execute('SELECT max(rowid) FROM memory_raw_events').fetchone()[0]
            self.column='r.rowid' if target['position'] is None else 'p.position'
            self.position=target['ordinal'] if target['position'] is None else target['position']
            self.ordinal=target['ordinal']
            self.primary_order=(target['ordinal'],target['position'])
            self.ensure_order(db,target)
        self.primary_ref=self.offer(primary.id,0,len(primary.prompt),primary.prompt,self.primary_order)
        self.selected.add(self.primary_ref)

    def _connection(self):
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,isolation_level=None,timeout=2,check_same_thread=False)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return db

    def close(self):
        # All read transactions are already closed before returning to a model.
        self._closed=True
        # A plain fd pins the inode against reuse; it holds no SQLite transaction
        # or WAL read mark. Release it before the publication writer is entered.
        if self._identity_fd is not None:
            os.close(self._identity_fd)
            self._identity_fd=None

    def admitted_raw(self,row):
        return self.frozen_raw(row)

    def admitted_thought(self,thought,hash_value):
        return self.frozen_thought(thought,hash_value)

    def frozen_raw(self,row):
        db=self._read_db.get()
        return (db is not None and row['status']!='waiting_origin'
                and unchanged(db,self.watermark,'raw',row['id']))

    def frozen_thought(self,thought,hash_value):
        db=self._read_db.get()
        return (db is not None and unchanged(db,self.watermark,'version',thought.id,thought.version)
                and unchanged(db,self.watermark,'head',thought.id))

    def grant_matches(self,grant):
        if grant is None:return False
        if self._publishing.get():
            # The writer's DB instance is checked below. No path/stat IO under
            # its write lock; preflight already checked the live path identity.
            return (grant.database==self.path and grant.scope==self.request.scope
                    and grant._identity==self._identity)
        return grant.matches(self)

    @contextmanager
    def reading_in(self,db):
        token=self._read_db.set(db)
        try:
            if self.instance is not None and stamp(db)[1]!=self.instance:
                raise SourceReadDenied('Source database instance changed')
            if self.primary_order is not None:
                self.ensure_order(db,self.row(db,self.primary.id))
            yield db
        finally:self._read_db.reset(token)

    @contextmanager
    def snapshot(self):
        if self._closed:raise SourceReadDenied('Source reader is closed')
        self._check_path_identity()
        with closing(self._connection()) as db:
            self._check_path_identity()
            self.request.lease.check_reader_snapshot(db,self.path)
            with self.reading_in(db):yield db

    def _check_path_identity(self):
        stat=self.path.stat()
        if (stat.st_dev,stat.st_ino)!=self._identity:
            raise SourceReadDenied('Source database path changed')

    def publication_preflight(self):
        self._check_path_identity()
        if any(self.refs[ref].context_session is not None for ref in self.selected):
            if not self.grant_matches(self.owner_scope):
                raise SourceReadDenied('Other dialogue authorization changed')

    def publication_check(self,db):
        token=self._publishing.set(True)
        try:
            with self.reading_in(db):
                self.evidence_in(db,tuple(self.selected))
        finally:self._publishing.reset(token)

    def ensure_order(self,db,target):
        if ((_event(target)!=self.primary or not self.visible(self.primary)
                or target['status']=='waiting_origin' or not self.admitted_raw(target))
                or (target['ordinal'],target['position'])!=self.primary_order):
            raise SourceReadDenied('Primary dialogue order or eligibility changed')
        # A captured explicitly positioned message and an unpositioned primary
        # have no reliable relative ordering. Rowids alone may disclose a future
        # message that arrived at ingestion before its chronological predecessor.
        if self.column=='r.rowid' and db.execute(
                'SELECT 1 FROM memory_raw_events r JOIN memory_event_positions p ON p.event_id=r.id '
                'WHERE r.session_id=? AND r.scope=? LIMIT 1',
                (self.request.capture_session_id,self.request.scope)).fetchone():
            raise SourceReadDenied('Mixed-position dialogue has no authoritative primary order')

    @staticmethod
    def visible(event):
        m=event.metadata
        try:role=source_role(event.actor,m)
        except ValueError:return False
        return (event_eligibility(event)['allowed'] and role in ('assistant','user') and event.actor==role
             and m.get('recipient') in (None,'all')
            and m.get('channel') in (None,'final','commentary') and m.get('visibility') in (None,'public')
            and m.get('subagent_id') is None and m.get('phase') not in ('analysis','reasoning')
            and all(m.get(key) not in ('function_call','function_call_output','tool_call','tool_result',
                'custom_tool_call','custom_tool_call_output')
                for key in ('type','message_type','payload_type','response_item_type')))

    def offer(self,event_id,start,end,excerpt,order,*,context_session=None):
        if context_session and self.diagnostic_scope is not None:
            self.diagnostic_scope.restricted=True
        ref=secrets.token_urlsafe(18)
        self.refs[ref]=IssuedReference('raw',event_id,start,end,sha256(excerpt.encode()).digest(),order,context_session)
        return ref

    def offer_thought(self,thought,*,local):
        if not local and self.diagnostic_scope is not None:
            self.diagnostic_scope.restricted=True
        from dataclasses import asdict
        from ..thought_store import _hash
        ref=secrets.token_urlsafe(18)
        self.refs[ref]=IssuedReference('thought',thought.id,None,None,
            bytes.fromhex(_hash(asdict(thought))),None,None if local else 'external',thought.version,thought.text)
        return ref

    def context_authorized(self,row,event,session):
        return (self.grant_matches(self.owner_scope)
                and event.scope==self.request.scope and canonical_session_id(event)==session
                and event.session_id!=self.request.capture_session_id
                and session!=self.request.canonical_session_id and row['status']!='waiting_origin'
                and self.visible(event) and self.frozen_raw(row)
                and self.earlier_chronological(event.recorded_at,row['ordinal']))

    def select(self, refs):
        if (not isinstance(refs,list) or not refs or any(not isinstance(ref,str) for ref in refs)
                or len(set(refs))!=len(refs)):
            raise SourceReadDenied('Expected distinct issued references')
        evidence=self.evidence(refs)
        self.selected.update(refs)
        return {'references':[{'ref':ref,'kind':self.refs[ref].kind,
                    'role':record['role'],'text':record['text'],
                    **({'thought_id':self.refs[ref].identity,'version':self.refs[ref].version}
                       if self.refs[ref].kind=='thought' else
                       {'span':{'start':self.refs[ref].start,'end':self.refs[ref].end}})}
                    for ref,(_,record,_) in zip(refs,evidence)]}


    def row(self,db,identity):
        row=db.execute('SELECT r.*,r.rowid AS ordinal,p.position,j.status FROM memory_raw_events r '
            'LEFT JOIN memory_event_positions p ON p.event_id=r.id JOIN memory_capture_jobs j ON j.event_id=r.id '
            'WHERE r.id=?',(identity,)).fetchone()
        if row is None:raise SourceReadDenied('Source is not available')
        return row

    @property
    def chronological_cutoff(self):
        # Queue capture normalizes recorded timestamps to UTC microseconds.
        return self.primary.recorded_at,self.ordinal

    def earlier_chronological(self, timestamp, ordinal):
        return (timestamp,ordinal)<self.chronological_cutoff and ordinal<=self.highwater

    def authorized(self,row,event):
        position=row['ordinal'] if self.column=='r.rowid' else row['position']
        return (row['ordinal']<=self.highwater and row['status']!='waiting_origin' and self.visible(event)
                and event.scope==self.request.scope and event.session_id==self.request.capture_session_id
                and canonical_session_id(event)==self.request.canonical_session_id
                and (event.id==self.primary.id or position is not None and position<self.position))

    def older(self,cursor):
        self.calls+=1
        if (self.started and (self.cursor is None or cursor!=self.cursor)) or (not self.started and cursor is not None):
            raise SourceReadDenied('Invalid history cursor')
        self.pages+=1
        cut=self.bound or (self.position,self.ordinal,None)
        column=self.column
        op='<=' if cut[2] is not None else '<'
        with self.snapshot() as db:
            rows=db.execute(f'''SELECT r.*,r.rowid AS ordinal,p.position,{column} AS pos,j.status
              FROM memory_raw_events r LEFT JOIN memory_event_positions p ON p.event_id=r.id
              JOIN memory_capture_jobs j ON j.event_id=r.id WHERE r.session_id=? AND r.scope=? AND r.rowid<=?
              AND {column}<? AND ({column}<? OR ({column}=? AND r.rowid{op}?))
              ORDER BY {column} DESC,r.rowid DESC LIMIT ?''',
              (self.request.capture_session_id,self.request.scope,self.highwater,self.position,
               cut[0],cut[0],cut[1],self.SCAN+1)).fetchall()
            results=[];remaining=self.CHARS;examined=0;split=False
            for row in rows[:self.SCAN]:
                if remaining<=0 or len(results)>=self.PAGE:break
                event=_event(row)
                self.bound=(row['pos'],row['ordinal'],None);examined+=1
                if not self.authorized(row,event) or not self.admitted_raw(row):continue
                end=cut[2] if cut[2] is not None and cut[1]==row['ordinal'] else len(event.prompt)
                if end<=0:continue
                start=max(0,end-remaining)
                results.append({'ref':self.offer(event.id,start,end,event.prompt[start:end],(row['ordinal'],row['position'])),'text':event.prompt[start:end],'role':event.actor,
                                'complete_message':(start,end)==(0,len(event.prompt))})
                remaining-=end-start
                if start:
                    self.bound=(row['pos'],row['ordinal'],start);split=True;break
        with self.snapshot():pass
        self.started=True
        self.bytes_read+=sum(len(item['text'].encode()) for item in results)
        has_more=split or examined<len(rows)
        status='more' if has_more else 'history_end'
        self.cursor=secrets.token_urlsafe(18) if status=='more' else None
        return {'messages':list(reversed(results)),'next_cursor':self.cursor,'history_status':status}

    def evidence(self,refs):
        with self.snapshot() as db:
            return self.evidence_in(db,refs)

    def evidence_in(self,db,refs):
        results=[]
        for ref in refs:
            if not isinstance(ref,str) or ref not in self.refs:raise SourceReadDenied('Unissued evidence reference')
            issued=self.refs[ref]
            if issued.kind=='thought':
                if self.navigation is None:
                    raise SourceReadDenied('Thought reference resolver unavailable')
                results.append(self.navigation.resolve_thought(db,issued))
                continue
            event_id,start,end,digest,order=(issued.identity,issued.start,issued.end,issued.digest,issued.order)
            row=self.row(db,event_id); event=_event(row)
            if ((row['ordinal'],row['position'])!=order or not (self.context_authorized(row,event,issued.context_session) if issued.context_session
                            else self.authorized(row,event))
                    or not self.admitted_raw(row) or not 0<=start<end<=len(event.prompt)):
                raise SourceReadDenied('Exact evidence no longer available')
            role=('external_dialogue_context' if issued.context_session else
                  'source' if event_id==self.primary.id else 'context')
            span={'start':start,'end':end}
            text=event.prompt[start:end]
            if sha256(text.encode()).digest()!=digest:
                raise SourceReadDenied('Offered source text changed')
            results.append((event,{'role':role,'text':text,
                **({'context_only':True,'canonical_session_id':issued.context_session} if issued.context_session else {})},
                {} if issued.context_session else {'event_id':event_id,'span':span}))
        return results
