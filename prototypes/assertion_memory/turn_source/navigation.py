"""Bounded same-dialogue graph reads for the synthetic source-turn adapter."""
from dataclasses import asdict
import secrets

from ..queue import _event
from ..thought_payload import decode_payload
from ..thought_store import Thought, _hash
from .reader import SourceReadDenied


class ClaimNavigation:
    MAX_CALLS = 8
    MAX_CANDIDATES = 64
    MAX_BYTES = 64_000
    MAX_THOUGHT_REFS = 40

    def __init__(self, reader, retrieval):
        self.reader = reader
        self.retrieval = retrieval
        self.calls = 0
        self.sent = 0
        self.charged = 0
        self.thought_refs = {}

    def _authorized_source(self, db, row):
        thought = Thought(**decode_payload(row['payload']))
        event_id = thought.origin.get('event_id')
        if (thought.origin.get('kind') != 'source_interpretation'
                or event_id == self.reader.primary.id or not isinstance(event_id, str)
                or thought.metadata.get('verification') != 'accepted'
                or thought.metadata.get('selection_context')
                or len(thought.text) > 4_000 or not 1 <= len(thought.bases) <= 12
                or _hash(asdict(thought)) != row['content_hash']
                or not any(basis.get('event_id') == event_id for basis in thought.bases)):
            return None
        for basis in thought.bases:
            if 'event_id' not in basis or 'thought_id' in basis or basis['event_id'] == self.reader.primary.id:
                return None
            raw = self.reader.row(db, basis['event_id'])
            event = _event(raw)
            if not self.reader.authorized(raw, event):
                return None
            span = basis.get('span')
            bounds = (0, len(event.prompt)) if span is None else (span['start'], span['end'])
            if not 0 <= bounds[0] < bounds[1] <= len(event.prompt):
                return None
            digest = _hash({'source': {k: raw[k] for k in raw.keys() if k not in ('ordinal','position','status')},
                            'span': span, 'text': event.prompt[bounds[0]:bounds[1]]})
            if digest != basis.get('content_hash'):
                return None
        return thought

    def _rows(self, db):
        # Validate every raw basis before exposing text to the host ranker.
        rows = db.execute('SELECT v.payload,v.content_hash FROM memory_thoughts h '
            'JOIN memory_thought_versions v ON v.thought_id=h.id AND v.version=h.current_version '
            'WHERE v.rowid<=? ORDER BY v.rowid DESC LIMIT ?',
            (self.reader.thought_watermark,self.MAX_CANDIDATES+1)).fetchall()
        candidates = []
        limited = len(rows) > self.MAX_CANDIDATES
        for row in rows[:self.MAX_CANDIDATES]:
            thought = self._authorized_source(db, row)
            if thought is not None:
                candidates.append(thought)
        return candidates, limited

    def _current(self, db, identity, version):
        row=db.execute('SELECT v.payload,v.content_hash FROM memory_thoughts h '
            'JOIN memory_thought_versions v ON v.thought_id=h.id AND v.version=h.current_version '
            'WHERE h.id=? AND h.current_version=?', (identity,version)).fetchone()
        return None if row is None else self._authorized_source(db,row)

    def _sources(self, ref):
        expected=self.thought_refs.get(ref)
        if expected is None:
            raise SourceReadDenied('Unissued thought reference')
        identity,version=expected
        issued=[]
        limited=False
        with self.reader.snapshot() as db:
            thought=self._current(db,identity,version)
            if thought is None:
                raise SourceReadDenied('Authorized thought changed during navigation')
            for basis in thought.bases:
                row=self.reader.row(db,basis['event_id']); event=_event(row)
                span=basis.get('span')
                start,end=(0,len(event.prompt)) if span is None else (span['start'],span['end'])
                excerpt=event.prompt[start:end]
                size=len(excerpt.encode())
                if (self.sent+size>self.MAX_BYTES or self.reader.bytes_read+size>self.reader.MAX_BYTES
                        or len(self.reader.refs)>=self.reader.MAX_REFS):
                    limited=True
                    break
                self.sent+=size
                self.reader.bytes_read+=size
                token=self.reader.offer(event.id,start,end,excerpt,(row['ordinal'],row['position']))
                issued.append({'ref':token,'text':excerpt,'role':event.actor})
        with self.reader.snapshot() as db:
            if self._current(db,identity,version)!=thought:
                raise SourceReadDenied('Authorized thought changed during navigation')
            self.reader.evidence_in(db,[row['ref'] for row in issued])
        return {'sources':issued,'coverage':'bounded' if limited else 'within_supported_window'}

    def call(self, name, args):
        self.calls += 1
        if self.calls > self.MAX_CALLS:
            raise SourceReadDenied('Navigation attempt budget exhausted')
        if name == 'thought_sources' and set(args)=={'ref'} and isinstance(args['ref'],str):
            return self._sources(args['ref'])
        if name != 'semantic_search' or not set(args) <= {'query','limit'} or self.retrieval is None:
            raise SourceReadDenied('Invalid navigation request')
        query, maximum = args.get('query'), args.get('limit',5)
        if not isinstance(query,str) or not 1 <= len(query.strip()) <= 1000 or type(maximum) is not int or not 1<=maximum<=5:
            raise SourceReadDenied('Invalid semantic search request')
        with self.reader.snapshot() as db:
            candidates, limited = self._rows(db)
        entries = {}
        for row in candidates:
            charge = len(row.text.encode())
            if self.charged + charge > self.MAX_BYTES:
                limited = True
                break
            self.charged += charge
            entries[row.id] = {'text':row.text,'questions':[]}
        ranked, _ = self.retrieval.rank_entries(query, entries, maximum) if entries else ([],{})
        lookup={row.id:row for row in candidates}
        candidates=[lookup[item.id] for item in ranked]
        with self.reader.snapshot() as db:
            for thought in candidates[:maximum]:
                if self._current(db,thought.id,thought.version) != thought:
                    raise SourceReadDenied('Authorized thought changed during navigation')
        material=[]
        for thought in candidates[:maximum]:
            size=len(thought.text.encode())
            if self.sent+size > self.MAX_BYTES or len(self.thought_refs)>=self.MAX_THOUGHT_REFS:
                limited=True
                break
            self.sent+=size
            ref=secrets.token_urlsafe(18)
            self.thought_refs[ref]=(thought.id,thought.version)
            material.append({'ref':ref,'text':thought.text,'version':thought.version})
        return {'thoughts':material, 'coverage':'bounded' if limited or len(candidates)>maximum else 'within_supported_window'}
