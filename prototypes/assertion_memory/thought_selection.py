"""Frozen canonical scope/time selections, including reusable historical answers."""
from __future__ import annotations
import json
import re
from .thought_payload import decode_payload
from .storage import _timestamp


class ThoughtSelection:
    """Select once before model work; never consult legacy semantic tables.

    Scope membership comes from exact bases, not topic similarity. A scoped query
    cannot consume a composite that also contains facts from another scope.
    Missing valid time is deliberately unknown, matching MemoryStore's API.
    """
    def __init__(self, thoughts, *, scope=None, scopes=None, valid_as_of=None,
                 recorded_as_of=None, max_snapshot_versions=10000, thought_ids=None, lexical_retrieval=None,
                 session_id=None, current_only=False):
        if scope is not None and scopes is not None:
            raise ValueError('use scope or scopes, not both')
        values = scopes if scopes is not None else (scope,) if scope is not None else None
        if isinstance(values, str):
            values = (values,)
        if values is not None and (not isinstance(values, (list, tuple, set)) or
                any(not isinstance(value, str) or not value.strip() for value in values)):
            raise ValueError('scopes must contain nonempty text')
        if type(max_snapshot_versions) is not int or max_snapshot_versions < 1:
            raise ValueError('snapshot version budget must be positive')
        self._session_id = session_id
        self.thoughts, self.store = thoughts, thoughts.store
        if lexical_retrieval is None:
            from .model import CandidateSearch
            from .thought_retrieval import ThoughtRetrieval
            lexical_retrieval = ThoughtRetrieval(CandidateSearch())
        self._lexical_retrieval = lexical_retrieval
        self.context = {'scopes': sorted(set(values)) if values is not None else None,
            'valid_as_of': _timestamp(valid_as_of) if valid_as_of is not None else None,
            'recorded_as_of': _timestamp(recorded_as_of) if recorded_as_of is not None else None}
        self._selected, self._observed_heads, self._nodes, self._attributes = {}, {}, {}, {}
        self._max_snapshot_versions = max_snapshot_versions
        self._question_proofs = {}
        with self.store.transaction() as c:
            from .thought_store import Thought
            if thought_ids is not None and (not isinstance(thought_ids,(list,tuple))
                    or any(not isinstance(key,str) or not key for key in thought_ids)):
                raise ValueError('thought_ids must contain existing identity strings')
            predicate = ' WHERE h.id IN (SELECT value FROM json_each(?))' if thought_ids is not None else ''
            arguments = (json.dumps(thought_ids),max_snapshot_versions+1) if thought_ids is not None else (max_snapshot_versions+1,)
            rows = c.execute('SELECT v.payload FROM memory_thoughts h JOIN memory_thought_versions v '
                'ON v.thought_id=h.id AND v.version=h.current_version'+predicate+' ORDER BY h.id LIMIT ?',arguments).fetchall()
            if len(rows) > max_snapshot_versions:
                raise ValueError('canonical selection exceeds the explicit snapshot version budget')
            heads = [Thought(**decode_payload(row[0])) for row in rows]
            consumed = 0
            for head in heads:
                self._observed_heads[head.id] = head.version
                if current_only:
                    versions = [head]
                else:
                    rows = c.execute('SELECT payload FROM memory_thought_versions WHERE thought_id=? '
                        'ORDER BY version DESC LIMIT ?', (head.id, max_snapshot_versions - consumed + 1)).fetchall()
                    consumed += len(rows)
                    if consumed > max_snapshot_versions:
                        raise ValueError('canonical selection exceeds the explicit snapshot version budget')
                    versions = [Thought(**decode_payload(row[0])) for row in rows]
                self._nodes.update({(row.id, row.version): row for row in versions})
                for candidate in versions:
                    if self._eligible(candidate, c):
                        self._selected[candidate.id] = candidate
                        from .thought_questions import proof_rows
                        self._question_proofs[(candidate.id,candidate.version)]=proof_rows(c,candidate.id,candidate.version)
                        break

    def _node(self, key):
        if key not in self._nodes:
            if len(self._nodes) >= self._max_snapshot_versions:
                raise ValueError('canonical selection exceeds the explicit dependency snapshot budget')
            node = self.thoughts.get(*key)
            if node is not None:
                self._nodes[key] = node
        return self._nodes.get(key)

    def _raw_attributes(self, c, basis):
        exists = c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_raw_events'").fetchone()
        row = c.execute('SELECT scope,metadata,session_id FROM memory_raw_events WHERE id=?', (basis['event_id'],)).fetchone() if exists else None
        if row is None:
            return set(), None, set()
        metadata = json.loads(row['metadata'])
        valid = metadata.get('valid_at')
        sessions = set()
        if self._session_id is not None:
            from .thought_processing_selection import canonical_session_id
            sessions = {row['session_id'], canonical_session_id({'session_id':row['session_id'],'metadata':metadata})}
        return {row['scope']}, _timestamp(valid) if valid is not None else None, sessions

    def _derived_attributes(self, thought, c):
        """Iterative postorder traversal supports any finite stored version depth."""
        root = (thought.id, thought.version)
        pending, visiting = [(root, False)], set()
        while pending:
            key, expanded = pending.pop()
            if key in self._attributes:
                continue
            node = self._node(key)
            if node is None:
                self._attributes[key] = (set(), None, set())
                continue
            children = [(basis['thought_id'], basis['version']) for basis in node.bases if 'thought_id' in basis]
            if not expanded:
                if key in visiting:
                    raise ValueError('cyclic canonical derivation')
                visiting.add(key)
                pending.append((key, True))
                pending.extend((child, False) for child in children if child not in self._attributes)
                continue
            visiting.discard(key)
            scopes, times, sessions = set(), [], set()
            origin_session = node.origin.get('session_id')
            if self._session_id is not None and isinstance(origin_session, str) and origin_session:
                sessions.add(origin_session)
            own_scope = node.origin.get('scope')
            if isinstance(own_scope, str) and own_scope:
                scopes.add(own_scope)
            for child in children:
                child_scopes, valid, child_sessions = self._attributes[child]
                sessions.update(child_sessions)
                scopes.update(child_scopes)
                times.append(valid)
            event_ids = {basis['event_id'] for basis in node.bases if 'event_id' in basis}
            for event_id in event_ids:
                raw_scopes, valid, session = self._raw_attributes(c, {'event_id': event_id})
                scopes.update(raw_scopes)
                times.append(valid)
                sessions.update(session)
            origin_event = node.origin.get('event_id')
            if self._session_id is not None and origin_event and origin_event not in event_ids:
                _, _, session = self._raw_attributes(c, {'event_id': origin_event})
                sessions.update(session)
            own_valid = node.origin.get('valid_at', node.metadata.get('valid_at'))
            if own_valid is not None:
                valid = _timestamp(own_valid)
            else:
                valid = max(times) if times and all(value is not None for value in times) else None
            self._attributes[key] = scopes, valid, sessions
        return self._attributes[root]

    def _eligible(self, thought, c):
        from .thought_source_policy import thought_eligibility
        if not thought_eligibility(self.thoughts, thought, memo=self.thoughts._source_policy_memo)['allowed']:
            return False
        saved = thought.metadata.get('selection_context')
        if saved is not None and saved != self.context:
            return False
        cutoff = self.context['recorded_as_of']
        if saved is None and cutoff is not None:
            clocks = [thought.created_at, thought.origin.get('recorded_at')]
            if any(value and _timestamp(value) > cutoff for value in clocks):
                return False
        scopes, valid, sessions = self._derived_attributes(thought, c)
        if self._session_id is not None and self._session_id not in sessions:
            return False
        wanted = self.context['scopes']
        if wanted is not None and (not scopes or not scopes.issubset(set(wanted))):
            return False
        cutoff = self.context['valid_as_of']
        if cutoff is not None and saved is None and (valid is None or valid > cutoff):
            return False
        return True

    def get(self, thought_id, version=None):
        return self.thoughts.get(thought_id, version) if version is not None else self._selected.get(thought_id)

    def list_thoughts(self, *, limit=None, after_id=None, **_):
        result = [self._selected[key] for key in sorted(self._selected) if key > (after_id or '')]
        return result if limit is None else result[:limit]

    def publish(self, text, question=None, origin=None, bases=(), metadata=None,
                thought_id=None, expected_version=None):
        pinned = []
        for raw in bases:
            basis = dict(raw)
            if 'thought_id' in basis:
                key = basis['thought_id']
                if key not in self._observed_heads:
                    raise ValueError('historical basis was not observed in this selection')
                basis['observed_head_version'] = self._observed_heads[key]
                if 'version' not in basis:
                    basis['version'] = self._selected[key].version
            pinned.append(basis)
        metadata = {**(metadata or {}), 'selection_context': self.context}
        original = self._selected.get(thought_id)
        if original is not None and original.metadata.get('selection_context') != self.context:
            metadata['selection_target'] = {'thought_id': original.id, 'version': original.version}
            thought_id, expected_version = None, None
        thought = self.thoughts.publish(text, question, origin, pinned, metadata, thought_id, expected_version)
        self._selected[thought.id] = thought
        self._observed_heads[thought.id] = thought.version
        self._nodes[(thought.id, thought.version)] = thought
        return thought

    def questions(self, thought):
        from .thought_questions import question_texts
        selected=self._selected.get(thought.id)
        if selected is None or selected.version!=thought.version:return []
        return question_texts(thought,self._question_proofs.get((thought.id,thought.version),[]),self.context)

    def question_checked(self, thought, question, frontier):
        from .thought_questions import checked
        return checked(self._question_proofs.get((thought.id,thought.version),[]),question,frontier,self.context)

    def save_question_check(self, thought, question, frontier):
        from .thought_questions import save_check,proof_rows
        with self.store.transaction(write=True) as c:
            if self._selected.get(thought.id)!=thought:
                raise ValueError('question applicability is outside the frozen selection')
            save_check(c,thought,question,frontier,self.context)
            self._question_proofs[(thought.id,thought.version)]=proof_rows(c,thought.id,thought.version)

    def index_entries(self):
        return [{'id':row.id,'version':row.version,'text':row.text,'questions':self.questions(row)}
                for row in self.list_thoughts()]

    def versions(self, thought_id):
        return self.thoughts.versions(thought_id)

    def search_text(self, query, limit=10):
        entries={row['id']:row for row in self.index_entries()}
        ranked,_=self._lexical_retrieval.rank_entries(query,entries,limit)
        return [self._selected[row.id] for row in ranked]

    def links(self, thought_id=None):
        cutoff = self.context['recorded_as_of']
        return [link for link in self.thoughts.links(thought_id)
                if link['source_id'] in self._selected and link['target_id'] in self._selected
                and self._selected[link['source_id']].version == link['source_version']
                and self._selected[link['target_id']].version == link['target_version']
                and (cutoff is None or (link.get('created_at') and _timestamp(link['created_at']) <= cutoff))]
