"""Bounded, explicit retrieval over the already authorized ordered raw dialogue.

No summaries stand in for sources. Every supplied body carries original offsets;
search uses the same candidate ranker as assertion retrieval when one is supplied.
"""
from __future__ import annotations

import json
from collections.abc import Callable

from .capture import RawEvent
from .model import CandidateSearch, ModelError

CHUNK_CHARACTERS = 6000
CHUNK_OVERLAP = 256
MAX_RECOVERY_ROUNDS = 6
MAX_COVERAGE_CHUNKS = 32


def authorized_context(event: RawEvent, context: list[RawEvent]) -> list[RawEvent]:
    """The queue supplies verified transcript order, not timestamp sort order."""
    result = []
    for source in context:
        if source.session_id == event.session_id:
            result.append(source)
        if source.id == event.id:
            break
    if not any(source.id == event.id for source in result):
        # Without the target's position the caller cannot authorize antecedents.
        return [event]
    return result


def chunks(text: str):
    for start in range(0, len(text), CHUNK_CHARACTERS - CHUNK_OVERLAP):
        yield start, min(len(text), start + CHUNK_CHARACTERS)
        if start + CHUNK_CHARACTERS >= len(text):
            break


class ContextWindow:
    def __init__(self, event: RawEvent, context: list[RawEvent], initial: list[dict],
                 record: Callable, *, budget: int, candidates: CandidateSearch | None = None):
        self.event = event
        self.events = authorized_context(event, context)
        self.sources = {source.id: source for source in self.events}
        self.positions = {source.id: index for index, source in enumerate(self.events)}
        self.record = record
        self.budget = budget
        self.candidates = candidates or CandidateSearch()
        self.spans = {}
        self.pinned = {event.id}
        self.audit = []
        self.recovery_rounds = 0
        self._index = None
        for row in initial:
            if (row['id'] in self.sources and not row.get('context_unavailable')
                    and not self.sources[row['id']].metadata.get('_source_unavailable')):
                source = self.sources[row['id']]
                end = min(len(source.prompt), CHUNK_CHARACTERS) if source.id == event.id and len(source.prompt) > budget else len(source.prompt)
                self.spans[source.id] = [(0, end)]
        self._fit()

    def _source_record(self, source: RawEvent, start: int, end: int) -> dict:
        row = self.record(source, is_target=source.id == self.event.id)
        row.update(text=source.prompt[start:end], source_span={'start': start, 'end': end},
                   source_length=len(source.prompt), dialogue_position=self.positions[source.id])
        row['context_incomplete'] = self.spans.get(source.id) != [(0, len(source.prompt))]
        if source.id == self.event.id:
            position = self.positions[source.id]
            row['previous_event_id'] = self.events[position - 1].id if position else None
        return row

    def records(self) -> list[dict]:
        target, history = [], []
        for source in self.events:
            spans = self.spans.get(source.id)
            if spans:
                destination = target if source.id == self.event.id else history
                destination.extend(self._source_record(source, start, end) for start, end in spans)
            elif history and history[-1].get('context_unavailable'):
                history[-1].update(last_event_id=source.id, last_position=self.positions[source.id])
                history[-1]['event_count'] += 1
            else:
                history.append({'id': source.id, 'context_unavailable': True, 'first_event_id': source.id,
                                'last_event_id': source.id, 'first_position': self.positions[source.id],
                                'last_position': self.positions[source.id], 'event_count': 1,
                                'source_length': len(source.prompt)})
        return [*target, *history]

    @property
    def truncated(self) -> bool:
        return any(self.spans.get(source.id) != [(0, len(source.prompt))] for source in self.events)

    def _fit(self) -> bool:
        while len(json.dumps(self.records(), ensure_ascii=False)) > self.budget:
            evictable = [source.id for source in self.events if source.id in self.spans and source.id not in self.pinned]
            if not evictable:
                return False
            del self.spans[evictable[0]]
        return True

    def _read(self, event_id: str, start: int, end: int) -> dict:
        source = self.sources.get(event_id)
        if source is None or source.metadata.get('_source_unavailable'):
            return {'operation': 'read', 'event_id': event_id, 'status': 'unavailable',
                    'reason': 'Source is outside the authorized preceding dialogue.'}
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source.prompt):
            return {'operation': 'read', 'event_id': event_id, 'status': 'unavailable',
                    'reason': 'Requested source bounds are invalid.', 'source_length': len(source.prompt)}
        if end - start > CHUNK_CHARACTERS:
            return {'operation': 'read', 'event_id': event_id, 'status': 'unavailable',
                    'reason': 'Request a smaller exact source span.', 'max_characters': CHUNK_CHARACTERS,
                    'source_length': len(source.prompt)}
        previous = {key: list(value) for key, value in self.spans.items()}
        pinned = set(self.pinned)
        merged = []
        for left, right in sorted([*self.spans.get(event_id, []), (start, end)]):
            if merged and left <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
            else:
                merged.append((left, right))
        self.spans[event_id] = merged
        self.pinned.add(event_id)
        if not self._fit():
            self.spans, self.pinned = previous, pinned
            return {'operation': 'read', 'event_id': event_id, 'status': 'unavailable',
                    'reason': 'Selected exact source spans exceed the bounded context budget.'}
        return {'operation': 'read', 'event_id': event_id, 'start': start, 'end': end, 'status': 'supplied',
                'source_length': len(source.prompt)}

    @property
    def exhausted(self) -> bool:
        return self.recovery_rounds >= MAX_RECOVERY_ROUNDS

    def recover(self, requests: object) -> list[dict]:
        self.recovery_rounds += 1
        if not isinstance(requests, list) or not 1 <= len(requests) <= 4:
            raise ModelError('context_requests must contain between one and four requests')
        results = []
        for request in requests:
            if not isinstance(request, dict):
                raise ModelError('context request must be an object')
            operation = request.get('operation')
            if operation == 'read':
                results.append(self._read(request.get('event_id'), request.get('start'), request.get('end')))
            elif operation == 'search':
                query, limit = request.get('query'), request.get('limit', 3)
                if not isinstance(query, str) or not query.strip() or len(query) > 1000 or type(limit) is not int or not 1 <= limit <= 4:
                    raise ModelError('context search requires a bounded query and limit 1..4')
                if self._index is None:
                    self._index = {f'{source.id}:{start}:{end}': (source, start, end)
                                   for source in self.events if source.id != self.event.id and not source.metadata.get('_source_unavailable')
                                   for start, end in chunks(source.prompt)}
                matches = self.candidates.rank(query, {key: source.prompt[start:end]
                    for key, (source, start, end) in self._index.items()}, limit=limit)
                results.append({'operation': 'search', 'query': query, 'status': 'completed',
                                'method': getattr(self.candidates, 'identity', type(self.candidates).__name__),
                                'matches': [match.id for match in matches]})
                for match in matches:
                    source, start, end = self._index[match.id]
                    results.append(self._read(source.id, start, end))
            else:
                raise ModelError('unsupported context request operation')
        self.audit.extend(results)
        return results

    def validate_references(self, claim: dict) -> None:
        for ref in claim['evidence_refs']:
            self.pinned.add(ref['event_id'])
            if not any(start <= ref['start'] < ref['end'] <= end
                       for start, end in self.spans.get(ref['event_id'], [])):
                raise ModelError('evidence quote was not inside an actually supplied source span')

    def coverage_records(self, claims: list[dict]) -> list[dict] | None:
        ids = {ref['event_id'] for claim in claims for ref in claim['evidence_refs']}
        result = []
        for event_id in sorted(ids, key=self.positions.get):
            source = self.sources[event_id]
            if self.spans.get(event_id) == [(0, len(source.prompt))]:
                continue
            for start, end in chunks(source.prompt):
                if any(left <= start and end <= right for left, right in self.spans.get(event_id, [])):
                    continue
                row = self._source_record(source, start, end)
                row['coverage_scan'] = True
                result.append(row)
                if len(result) > MAX_COVERAGE_CHUNKS:
                    return None
        return result
