"""Host-owned claim-scoped visible raw reader. The model receives no database API."""
from contextlib import contextmanager, closing
from pathlib import Path
from hashlib import sha256
import secrets
import sqlite3
from threading import RLock

from ..dialogue import source_role
from ..thought_processing_selection import canonical_session_id
from ..thought_source_policy import event_eligibility
from ..queue import _event


class SourceReadDenied(ValueError):
    pass


class EarlierDialogue:
    PAGE=8
    CHARS=12_000
    SCAN=32
    MAX_PAGES=16
    MAX_BYTES=256_000
    MAX_REFS=128

    def __init__(self,path,request,primary,*,freeze_membership=False):
        self.path=Path(path).resolve(strict=True)
        self.request=request
        self.primary=primary
        self.primary_order=None
        self.refs={}
        self.pages=0
        self.calls=0
        self.bytes_read=len(primary.prompt.encode())
        self.cursor,self.bound,self.started=None,None,False
        self._anchor_lock=RLock()
        self._anchor=None
        try:
            self._initialize(freeze_membership)
        except BaseException:
            self.close()
            raise

    def _initialize(self,freeze_membership):
        if freeze_membership:
            self._anchor=self._connection()
        manager=self.snapshot() if self._anchor is None else self._admission()
        request,primary=self.request,self.primary
        with manager as db:
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

    def _connection(self):
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,isolation_level=None,timeout=2,check_same_thread=False)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return db

    @contextmanager
    def _admission(self):
        self.request.lease.check_reader_snapshot(self._anchor,self.path)
        yield self._anchor

    def close(self):
        with self._anchor_lock:
            if self._anchor is not None:
                self._anchor.close()
                self._anchor=None

    def admitted_raw(self,row):
        return self._anchor is None or self.frozen_raw(row)

    def admitted_thought(self,thought,hash_value):
        return self._anchor is None or self.frozen_thought(thought,hash_value)

    def frozen_raw(self,row):
        with self._anchor_lock:
            if self._anchor is None:
                return False
            fields=self._anchor.execute('SELECT r.*,r.rowid AS ordinal,p.position,j.status '
                'FROM memory_raw_events r LEFT JOIN memory_event_positions p ON p.event_id=r.id '
                'JOIN memory_capture_jobs j ON j.event_id=r.id WHERE r.id=?',(row['id'],)).fetchone()
            # Original waiting-origin rows were not admitted. Normal lease/job
            # progress after admission is allowed; position and raw bytes are not.
            return (fields is not None and fields['status']!='waiting_origin'
                    and all(fields[name]==row[name] for name in fields.keys() if name!='status'))

    def frozen_thought(self,thought,hash_value):
        with self._anchor_lock:
            if self._anchor is None:
                return False
            row=self._anchor.execute('SELECT content_hash FROM memory_thought_versions WHERE thought_id=? AND version=?',
                                     (thought.id,thought.version)).fetchone()
            return row is not None and row['content_hash']==hash_value

    @contextmanager
    def snapshot(self):
        with closing(self._connection()) as db:
            self.request.lease.check_reader_snapshot(db,self.path)
            if self.primary_order is not None:
                self.ensure_order(db,self.row(db,self.primary.id))
            yield db

    def ensure_order(self,db,target):
        if ((_event(target)!=self.primary or not self.visible(self.primary)
                or target['status']=='waiting_origin')
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

    def offer(self,event_id,start,end,excerpt,order):
        ref=secrets.token_urlsafe(18)
        self.refs[ref]=(event_id,start,end,sha256(excerpt.encode()).digest(),order)
        return ref

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
        if self.calls>self.MAX_PAGES:
            raise SourceReadDenied('Attempt history call budget exhausted')
        if (self.started and (self.cursor is None or cursor!=self.cursor)) or (not self.started and cursor is not None):
            raise SourceReadDenied('Invalid history cursor')
        # Reserve a complete worst-case page (UTF-8 can encode in four bytes).
        # Do not disguise an attempt ceiling as exhaustion of eligible history.
        if not self.can_read_page(include_current=True):
            with self.snapshot():pass
            return {'messages':[],'next_cursor':None,'history_status':'attempt_budget_exhausted'}
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
        status=('more' if self.can_read_page() else 'attempt_budget_exhausted') if has_more else 'history_end'
        self.cursor=secrets.token_urlsafe(18) if status=='more' else None
        return {'messages':list(reversed(results)),'next_cursor':self.cursor,'history_status':status}

    def can_read_page(self,*,include_current=False):
        charged=self.calls-int(include_current)
        return (max(self.pages,charged)<self.MAX_PAGES and self.bytes_read+4*self.CHARS<=self.MAX_BYTES
                and len(self.refs)+self.PAGE<=self.MAX_REFS)

    def evidence(self,refs):
        with self.snapshot() as db:
            return self.evidence_in(db,refs)

    def evidence_in(self,db,refs):
        results=[]
        for ref in refs:
            if not isinstance(ref,str) or ref not in self.refs:raise SourceReadDenied('Unissued evidence reference')
            event_id,start,end,digest,order=self.refs[ref]
            row=self.row(db,event_id); event=_event(row)
            if ((row['ordinal'],row['position'])!=order or not self.authorized(row,event)
                    or not self.admitted_raw(row) or not 0<=start<end<=len(event.prompt)):
                raise SourceReadDenied('Exact evidence no longer available')
            role='source' if event_id==self.primary.id else 'context'
            span={'start':start,'end':end}
            text=event.prompt[start:end]
            if sha256(text.encode()).digest()!=digest:
                raise SourceReadDenied('Offered source text changed')
            results.append((event,{'role':role,'text':text},{'event_id':event_id,'span':span}))
        return results
