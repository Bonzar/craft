"""Read another exact, authorized dialogue as context, never as proposal proof."""
from dataclasses import dataclass, replace
from hashlib import sha256
import secrets

from ..queue import _event
from ..storage import _timestamp
from ..thought_processing_selection import canonical_session_id, source_predicate
from ..thought_source_policy import eligible_sql
from .reader import SourceReadDenied


@dataclass(frozen=True)
class SessionPage:
    session: str
    start: str | None
    end: str | None
    local: bool = False
    boundary: tuple[str, int, int] | None = None


class SessionDialogue:
    def __init__(self, reader, grant):
        self.reader, self.grant = reader, grant
        self.cursors = {}

    def owns_cursor(self, token):
        return isinstance(token, str) and token in self.cursors

    def _charge(self):
        self.reader.calls += 1
        if self.reader.calls > self.reader.MAX_PAGES:
            raise SourceReadDenied('Attempt history call budget exhausted')

    @staticmethod
    def _date(value):
        if not isinstance(value,str) or len(value)>40:
            raise SourceReadDenied('Invalid UTC date bound')
        try:return _timestamp(value)
        except (ValueError,TypeError,OverflowError):
            raise SourceReadDenied('Invalid UTC date bound') from None

    def read(self, args):
        self._charge()
        if 'cursor' in args:
            if set(args)!={'cursor'} or not self.owns_cursor(args['cursor']):
                raise SourceReadDenied('Invalid history cursor')
            state=self.cursors.pop(args['cursor'])
        else:
            session=args.get('session_id','current')
            if not isinstance(session,str) or not session.strip() or len(session)>256:
                raise SourceReadDenied('Expected an exact canonical dialogue')
            if session=='current':
                session=self.reader.request.canonical_session_id
            start=self._date(args['start_date']) if 'start_date' in args else None
            end=self._date(args['end_date']) if 'end_date' in args else None
            if start is not None and end is not None and start>=end:
                raise SourceReadDenied('Invalid date range')
            state=SessionPage(session,start,end,local=session==self.reader.request.canonical_session_id)
        if not state.local and (self.grant is None or not self.grant.matches(self.reader)):
            raise SourceReadDenied('Other dialogue is not authorized')

        session,args=source_predicate((state.session,),'r')
        visible,visibility=eligible_sql('r')
        capture_op='=' if state.local else '!='
        clauses=[session,visible,'r.scope=?',f'r.session_id{capture_op}?','r.rowid<=?',
                 '(r.recorded_at<? OR (r.recorded_at=? AND r.rowid<?))',"j.status!='waiting_origin'","r.actor IN ('user','assistant')",
                 "coalesce(json_extract(r.metadata,'$.recipient'),'all')='all'",
                 "coalesce(json_extract(r.metadata,'$.visibility'),'public')='public'",
                 "json_extract(r.metadata,'$.subagent_id') IS NULL"]
        stamp,ordinal=self.reader.chronological_cutoff
        parameters=[*args,*visibility,self.reader.request.scope,self.reader.request.capture_session_id,
                    self.reader.highwater,stamp,stamp,ordinal]
        view=(' FROM memory_raw_events r JOIN memory_capture_jobs j ON j.event_id=r.id WHERE ')
        with self.reader.snapshot() as db:
            if not state.local and state.boundary is None and not self._eligible_dialogue(db,view,clauses,parameters,state.session):
                raise SourceReadDenied('Other dialogue is not authorized')
            if not self.reader.can_read_page(include_current=True):
                return {'messages':[],'next_cursor':None,'history_status':'attempt_budget_exhausted'}
            self.reader.pages+=1
            if state.start is not None: clauses.append('r.recorded_at>=?'); parameters.append(state.start)
            if state.end is not None: clauses.append('r.recorded_at<?'); parameters.append(state.end)
            if state.boundary is not None:
                stamp,ordinal,offset=state.boundary
                op='>=' if offset else '>'
                clauses.append(f'(r.recorded_at>? OR (r.recorded_at=? AND r.rowid{op}?))')
                parameters.extend((stamp,stamp,ordinal))
            selected='SELECT r.*,r.rowid AS ordinal,p.position,j.status'
            with_positions=view.replace(' WHERE ',' LEFT JOIN memory_event_positions p ON p.event_id=r.id WHERE ')
            rows=db.execute(selected+with_positions+' AND '.join(clauses)
                +' ORDER BY r.recorded_at,r.rowid LIMIT ?',(*parameters,self.reader.SCAN+1)).fetchall()
            messages=[]; recheck=[]; left=self.reader.CHARS; examined=0; split=False
            for row in rows[:self.reader.SCAN]:
                if left<=0 or len(messages)>=self.reader.PAGE:
                    break
                examined+=1
                boundary=(row['recorded_at'],row['ordinal'],0)
                event=_event(row)
                if (not self.reader.visible(event) or canonical_session_id(event)!=state.session
                        or not self.reader.earlier_chronological(event.recorded_at,row['ordinal'])
                        or not self.reader.admitted_raw(row)
                        or (not self.reader.authorized(row,event) if state.local else not self.reader.frozen_raw(row))):
                    continue
                start=state.boundary[2] if state.boundary is not None and row['ordinal']==state.boundary[1] else 0
                end=min(start+left,len(event.prompt))
                excerpt=event.prompt[start:end]
                item={'text':excerpt,'role':event.actor,'recorded_at':event.recorded_at,
                      'complete_message':(start,end)==(0,len(event.prompt)),'evidence_eligible':state.local}
                if state.local:
                    item['ref']=self.reader.offer(event.id,start,end,excerpt,(row['ordinal'],row['position']))
                if not state.local:
                    recheck.append((event.id,row['ordinal'],row['position'],start,end,
                                    sha256(excerpt.encode()).digest()))
                messages.append(item)
                left-=end-start
                self.reader.bytes_read+=len(excerpt.encode())
                if end<len(event.prompt):
                    boundary=(row['recorded_at'],row['ordinal'],end)
                    split=True
                    break
        with self.reader.snapshot() as db:
            if state.local:
                self.reader.evidence_in(db,[row['ref'] for row in messages])
            else:
                if not self.grant.matches(self.reader):
                    raise SourceReadDenied('Other dialogue authorization changed')
                for identifier,ordinal,position,start,end,digest in recheck:
                    row=self.reader.row(db,identifier); event=_event(row)
                    if (not self.reader.frozen_raw(row) or row['ordinal']!=ordinal or row['position']!=position
                            or event.scope!=self.reader.request.scope or canonical_session_id(event)!=state.session
                            or event.session_id==self.reader.request.capture_session_id
                            or row['status']=='waiting_origin' or not self.reader.visible(event)
                            or not self.reader.earlier_chronological(event.recorded_at,row['ordinal'])
                            or sha256(event.prompt[start:end].encode()).digest()!=digest):
                        raise SourceReadDenied('Other dialogue changed during navigation')
        more=split or examined<len(rows)
        status=('more' if self.reader.can_read_page() else 'attempt_budget_exhausted') if more else 'history_end'
        token=secrets.token_urlsafe(18) if status=='more' else None
        if token is not None:
            self.cursors[token]=replace(state,boundary=boundary)
        return {'messages':messages,'next_cursor':token,'history_status':status}

    def _eligible_dialogue(self, db, view, clauses, parameters, session):
        # Bounded proof of at least one visible authorized raw event. A hidden,
        # unavailable, or unproved session receives the same generic denial.
        positioned=view.replace(' WHERE ',' LEFT JOIN memory_event_positions p ON p.event_id=r.id WHERE ')
        rows=db.execute('SELECT r.*,r.rowid AS ordinal,p.position,j.status'+positioned+' AND '.join(clauses)
            +' ORDER BY r.recorded_at,r.rowid LIMIT ?',(*parameters,self.reader.SCAN)).fetchall()
        return any(self.reader.visible(_event(row)) and canonical_session_id(_event(row))==session and self.reader.frozen_raw(row) for row in rows)
