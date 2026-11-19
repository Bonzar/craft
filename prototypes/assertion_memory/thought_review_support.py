"""Reveal the exact provenance of selected fragments for semantic review."""
from collections import defaultdict, deque
from dataclasses import asdict

from .queue import _event
from .thought_source_policy import thought_eligibility
from .thought_store import _span


def _identity(basis):
    return basis['thought_id'], basis['version']


class ReviewSupport:
    def __init__(self, engine, catalog, selections):
        self.engine = engine
        self.events = {}
        self.originals = {}
        self.opened = {}
        self.queued = defaultdict(set)
        self.visited = defaultdict(set)
        self.existing_raw = self._existing_raw(catalog)
        selected = defaultdict(set)
        for option in sorted({option for group in selections for option in group}):
            basis = catalog.bases[catalog.fragments[option].record_index]
            if 'thought_id' in basis:
                selected[_identity(basis)].add(option)
        self.opened.update(dict.fromkeys(selected))
        for identity, options in selected.items():
            self._open(identity, options)

    @staticmethod
    def _existing_raw(catalog):
        options = {}
        for fragment in catalog.fragments:
            basis = catalog.bases[fragment.record_index]
            if 'event_id' in basis:
                offset = basis.get('span', {}).get('start', 0)
                options.setdefault((basis['event_id'], offset + fragment.start, offset + fragment.end), fragment.option)
        return options

    def _raw(self, basis, options):
        event_id = basis['event_id']
        if event_id not in self.events:
            with self.engine.store.transaction() as connection:
                row = connection.execute('SELECT * FROM memory_raw_events WHERE id=?', (event_id,)).fetchone()
            raw = asdict(_event(row)) if row is not None else None
            self.events[event_id] = raw if raw is not None and self.engine._raw_allowed(raw) else None
        raw = self.events[event_id]
        if raw is None:
            return
        text = raw['prompt']
        span = basis.get('span') or {'start': 0, 'end': len(text)}
        exact = _span(text, span)
        key = (event_id, span['start'], span['end'])
        _, linked, _ = self.originals.setdefault(key, ({'role': 'original_evidence',
            'origin': self.engine._model_origin(self.engine._raw_origin(raw)),
            **({'source_option': self.existing_raw[key]} if key in self.existing_raw else {'text': exact})},
            set(), dict(basis, span=span)))
        linked.update(options)

    def _open(self, identity, options):
        pending = deque([(identity, options)])
        while pending:
            key, support_options = pending.popleft()
            unseen = support_options - self.visited[key]
            if not unseen:
                continue
            self.visited[key].update(unseen)
            thought = self.engine.thoughts.get(*key)
            if thought is None or not thought_eligibility(
                    self.engine.thoughts, thought,
                    memo=getattr(self.engine.thoughts, '_source_policy_memo', None))['allowed']:
                continue
            for basis in thought.bases:
                if 'event_id' in basis:
                    self._raw(basis, unseen)
                    continue
                target = _identity(basis)
                if target in self.opened:
                    pending.append((target, unseen))
                else:
                    self.queued[target].update(unseen - self.visited[target])

    def open(self, option):
        identity = self.options()[option]
        self.opened[identity] = True
        self._open(identity, self.queued.pop(identity))

    def options(self):
        return [key for key, origins in self.queued.items() if origins - self.visited[key]]

    def records(self):
        originals = [dict(record, supports_fragments=sorted(options))
                     for record, options, _ in self.originals.values()]
        linked = [{'role': 'linked_evidence_option', 'option': index,
                   'supports_fragments': sorted(self.queued[key] - self.visited[key])}
                  for index, key in enumerate(self.options())]
        return [*originals, *linked]

    def bindings(self):
        return [basis for _, _, basis in self.originals.values()] + [
            {'thought_id': key[0], 'version': key[1]} for key in self.opened if self.opened[key]]
