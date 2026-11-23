"""Paginated authorized graph reads for the synthetic source-turn adapter."""
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..queue import _event
from ..thought_payload import decode_payload
from ..thought_processing_selection import canonical_session_id, source_predicate
from ..thought_store import Thought, _hash
from .reader import SourceReadDenied


@dataclass(frozen=True)
class OwnerScopeGrant:
    """Trusted composition assertion for an independently verified single-owner scope."""
    database: Path
    scope: str
    _identity: tuple[int,int] = field(init=False,repr=False)

    def __post_init__(self):
        if not self.database.is_absolute() or not self.scope.strip():
            raise ValueError('Owner scope grant requires an absolute database path and nonempty scope')
        path=self.database.resolve(strict=True)
        stat=path.stat()
        object.__setattr__(self,'database',path)
        object.__setattr__(self,'_identity',(stat.st_dev,stat.st_ino))

    def matches(self, reader):
        stat=reader.path.stat()
        return (self.database==reader.path and self.scope==reader.request.scope
                and self._identity==(stat.st_dev,stat.st_ino))


class ClaimNavigation:

    def __init__(self, reader, retrieval, owner_scope=None):
        self.reader = reader
        self.retrieval = retrieval
        self.owner_scope = owner_scope
        self.calls = 0
        self.sent = 0
        self.charged = 0
        self.cursors = {}
        if reader is not None:  # Offline registry-only handshake has no database reader.
            reader.navigation=self
            reader.owner_scope=owner_scope

    @property
    def cross_scope(self):
        return self.reader.grant_matches(self.owner_scope)

    def _authorized_source(self, db, row, *, cross=False, session=None):
        thought = Thought(**decode_payload(row['payload']))
        event_id = thought.origin.get('event_id')
        if (thought.origin.get('kind') != 'source_interpretation'
                or event_id == self.reader.primary.id or not isinstance(event_id, str)
                or thought.metadata.get('verification') != 'accepted'
                or thought.metadata.get('selection_context')
                or _hash(asdict(thought)) != row['content_hash']
                or not any(basis.get('event_id') == event_id for basis in thought.bases)):
            return None
        local = True
        for basis in thought.bases:
            if 'event_id' not in basis or 'thought_id' in basis or basis['event_id'] == self.reader.primary.id:
                return None
            raw = self.reader.row(db, basis['event_id'])
            event = _event(raw)
            if not self.reader.admitted_raw(raw):
                return None
            if session is not None and canonical_session_id(event)!=session:
                return None
            if not self.reader.authorized(raw, event):
                if (not cross or not self.cross_scope
                        or raw['status']=='waiting_origin' or not self.reader.visible(event)
                        or event.scope!=self.reader.request.scope
                        or event.session_id==self.reader.request.capture_session_id
                        or canonical_session_id(event)==self.reader.request.canonical_session_id
                        or not self.reader.earlier_chronological(event.recorded_at,raw['ordinal'])
                        or not self.reader.frozen_raw(raw)):
                    return None
                local=False
            span = basis.get('span')
            bounds = (0, len(event.prompt)) if span is None else (span['start'], span['end'])
            if not 0 <= bounds[0] < bounds[1] <= len(event.prompt):
                return None
            digest = _hash({'source': {k: raw[k] for k in raw.keys() if k not in ('ordinal','position','status')},
                            'span': span, 'text': event.prompt[bounds[0]:bounds[1]]})
            if digest != basis.get('content_hash'):
                return None
        if (not self.reader.admitted_thought(thought,row['content_hash'])
                or not local and not self.reader.frozen_thought(thought,row['content_hash'])):
            return None
        return thought, local

    def _rows(self, db, session):
        # Validate every raw basis before exposing text to the host ranker.
        # Coarse DB scope/session filter precedes the complete candidate scan.
        # Python validates every source byte, the honest role, ordering and the exact stored hash.
        predicate,args=source_predicate((session,) if session is not None else None,'r')
        own = '' if self.cross_scope else ' AND r.session_id=?'
        rows = db.execute('SELECT v.payload,v.content_hash FROM memory_thoughts h '
            'JOIN memory_thought_versions v ON v.thought_id=h.id AND v.version=h.current_version '
            'WHERE v.rowid<=? AND EXISTS(SELECT 1 FROM json_each(v.payload,\'$.bases\') b '
            'JOIN memory_raw_events r ON r.id=json_extract(b.value,\'$.event_id\') '
            'WHERE r.scope=? AND '+predicate+own+') ORDER BY v.rowid DESC',
            (self.reader.thought_watermark,self.reader.request.scope,*args,
              *((self.reader.request.capture_session_id,) if own else ())))
        candidates = []
        for row in rows:
            thought = self._authorized_source(db,row,cross=True,session=session)
            if thought is not None:
                candidates.append(thought)
        return candidates

    def _current(self, db, identity, version, *, cross=False, session=None):
        # Resolve exactly the immutable version shown, never the moving head.
        row=db.execute('SELECT payload,content_hash FROM memory_thought_versions '
            'WHERE thought_id=? AND version=?', (identity,version)).fetchone()
        return None if row is None else self._authorized_source(db,row,cross=cross,session=session)

    def resolve_thought(self, db, issued):
        if issued.kind!='thought':
            raise SourceReadDenied('Expected issued thought reference')
        current=self._current(db,issued.identity,issued.version,cross=True)
        if current is None:
            raise SourceReadDenied('Pinned thought is no longer authorized')
        thought,local=current
        if (bytes.fromhex(_hash(asdict(thought)))!=issued.digest or thought.text!=issued.text
                or local!=(issued.context_session is None)):
            raise SourceReadDenied('Pinned thought content or access changed')
        record={'role':'context' if local else 'external_dialogue_context',
                'text':issued.text,'thought_id':issued.identity,'version':issued.version,
                'whole_reference':True,'context_only':not local}
        head=db.execute('SELECT current_version FROM memory_thoughts WHERE id=?',(thought.id,)).fetchone()
        if head is None:
            raise SourceReadDenied('Pinned thought was retired')
        basis={'thought_id':thought.id,'version':thought.version,'observed_head_version':head[0]} if local else {}
        return None,record,basis

    def _next(self, kind, state):
        token=secrets.token_urlsafe(18)
        self.cursors[token]=(kind,state)
        return token

    def _resume(self, kind, args):
        token=args.get('cursor')
        if (set(args)!={'cursor'} or not isinstance(token,str)
                or token not in self.cursors or self.cursors[token][0]!=kind):
            raise SourceReadDenied('Invalid navigation cursor')
        return self.cursors.pop(token)[1]

    def _sources(self, state):
        ref,index,offset=state
        expected=self.reader.refs.get(ref)
        if expected is None or expected.kind!='thought':
            raise SourceReadDenied('Unissued thought reference')
        identity,version=expected.identity,expected.version
        issued=[]
        left=self.reader.CHARS
        with self.reader.snapshot() as db:
            current=self._current(db,identity,version,cross=True)
            if current is None:
                raise SourceReadDenied('Authorized thought changed during navigation')
            self.resolve_thought(db,expected)
            thought,local=current
            while index<len(thought.bases) and left and len(issued)<self.reader.PAGE:
                basis=thought.bases[index]
                row=self.reader.row(db,basis['event_id']); event=_event(row)
                span=basis.get('span')
                start,end=(0,len(event.prompt)) if span is None else (span['start'],span['end'])
                start+=offset
                stop=min(end,start+left)
                excerpt=event.prompt[start:stop]
                size=len(excerpt.encode())
                self.sent+=size
                self.reader.bytes_read+=size
                token=self.reader.offer(event.id,start,stop,excerpt,(row['ordinal'],row['position']),
                    context_session=None if local else canonical_session_id(event))
                issued.append({'ref':token,'text':excerpt,'role':event.actor,'evidence_eligible':local,
                               'span':{'start':start,'end':stop}})
                left-=stop-start
                if stop==end:
                    index+=1;offset=0
                else:
                    offset+=stop-start
        with self.reader.snapshot() as db:
            self.resolve_thought(db,expected)
            self.reader.evidence_in(db,[row['ref'] for row in issued])
        more=index<len(thought.bases)
        token=self._next('thought_sources',(ref,index,offset)) if more else None
        return {'sources':issued,'coverage':'more' if more else 'within_supported_window','next_cursor':token}

    def _search_page(self, state):
        candidates,index,maximum,session=state
        page=candidates[index:index+maximum]
        with self.reader.snapshot() as db:
            for thought,local in page:
                if self._current(db,thought.id,thought.version,cross=True,session=session) != (thought,local):
                    raise SourceReadDenied('Authorized thought changed during navigation')
        material=[]
        for thought,local in page:
            self.sent+=len(thought.text.encode())
            item={'thought_id':thought.id,'text':thought.text,'version':thought.version}
            item['ref']=self.reader.offer_thought(thought,local=local)
            item['evidence_eligible']=local
            material.append(item)
        index+=len(page)
        more=index<len(candidates)
        token=self._next('semantic_search',(candidates,index,maximum,session)) if more else None
        return {'thoughts':material,'coverage':'more' if more else 'within_supported_window','next_cursor':token}

    def call(self, name, args):
        self.calls += 1
        if name not in ('thought_sources','semantic_search'):
            raise SourceReadDenied('Invalid navigation request')
        if 'cursor' in args:
            state=self._resume(name,args)
            return self._sources(state) if name=='thought_sources' else self._search_page(state)
        if name == 'thought_sources':
            if set(args)!={'ref'} or not isinstance(args['ref'],str):
                raise SourceReadDenied('Invalid thought sources request')
            return self._sources((args['ref'],0,0))
        if not set(args) <= {'query','limit','session_id'} or self.retrieval is None:
            raise SourceReadDenied('Invalid navigation request')
        query, maximum = args.get('query'), args.get('limit',5)
        if not isinstance(query,str) or not query.strip() or type(maximum) is not int or not 1<=maximum<=5:
            raise SourceReadDenied('Invalid semantic search request')
        session=args.get('session_id')
        if 'session_id' in args and (not isinstance(session,str) or not session.strip() or len(session)>256):
            raise SourceReadDenied('Invalid session filter')
        if session=='current':
            session=self.reader.request.canonical_session_id
        if not self.cross_scope and session is not None and session!=self.reader.request.canonical_session_id:
            return {'thoughts':[],'coverage':'within_supported_window','next_cursor':None}
        with self.reader.snapshot() as db:
            candidates = self._rows(db,session)
        # Ranker exceptions may contain inputs: protect diagnostics before any
        # authorized foreign content reaches it, even if no ref is issued yet.
        if any(not local for _,local in candidates) and self.reader.diagnostic_scope is not None:
            self.reader.diagnostic_scope.restricted=True
        entries={row.id:{'text':row.text,'questions':[]} for row,_ in candidates}
        self.charged+=sum(len(row['text'].encode()) for row in entries.values())
        ranked, _ = self.retrieval.rank_entries(query,entries,len(entries)) if entries else ([],{})
        lookup={row.id:(row,local) for row,local in candidates}
        candidates=[lookup[item.id] for item in ranked]
        return self._search_page((candidates,0,maximum,session))
