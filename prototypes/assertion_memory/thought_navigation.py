"""Read-only semantic navigation; identities and graph addresses stay in code."""
from __future__ import annotations

from dataclasses import dataclass

from . import query_timing as timing
from .model import ModelContextExceeded, ModelError
from .thought_engine_contract import exact_fields, semantic_text
from .thought_source_policy import thought_eligibility
from .thought_store import Thought

TASK = 'Navigate thought graph'
DEPENDENT_PAGE_SIZE = 100
INSTRUCTIONS = '''Find the thought whose content is useful for the original question.
The starting points are search results, not the complete graph. Inspect one thought
at a time. Its incoming and outgoing direct relations can lead to a more useful
area, even when a starting point does not answer the question itself. A description
may be only the exact first paragraph; description_complete reports this honestly.
The current thought is supplied in full. Use action inspect to open a starting
point or neighbor, focus to
choose a visible thought as the appropriate answer location, or unknown when
further progress is unsupported. A focus is a location, not a claim its answer is
already verified. Return exactly action, question, quote, text.
When a visible description already identifies the required location, focus
directly. Inspection is for missing navigation detail, not mandatory confirmation
of text already visible. The separate answer stage reads and checks the focus.
Select existing thoughts using a substantive verbatim quotation from their visible questions,
description/text, actor or recording timestamp, never IDs, versions, indices, or invented short aliases.
recorded_at is when this thought version was recorded, not when its event occurred.
The quotation must identify one thought unambiguously. The program performs the
initial semantic search once; further discovery follows direct graph relations.
Search is not an available action. For inspect/focus supply quote and null
question; for unknown use both null. text explains the choice. Do not cycle between
unchanged frontiers. Source, scope and temporal exclusions are real boundaries;
never assume excluded or uninspected material has been checked.'''


@dataclass(frozen=True)
class NavigationResult:
    thought: Thought | None
    inspected: tuple[Thought, ...]
    reason: str


def _key(thought):
    return thought.id, thought.version


def _ref(thought):
    return {'thought_id': thought.id, 'version': thought.version}


def _questions(engine, thought):
    reader = getattr(engine, 'question_texts', None)
    if reader is not None:
        return list(reader(thought))
    reader = getattr(engine.thoughts, 'questions', None)
    values = reader(thought) if reader is not None else getattr(thought, 'questions', None)
    return list(values) if values else [thought.question] if thought.question else []


def _admit(engine, thought, *, pinned=False):
    """Never reopen retired families or bypass a frozen selection via get(version)."""
    selected = engine.thoughts.get(thought.id)
    if selected is None:
        return 'inactive_or_outside_selection'
    if not pinned and selected.version != thought.version:
        return 'selected_version_changed'
    if not thought_eligibility(engine.thoughts, thought,
            memo=getattr(engine.thoughts, '_source_policy_memo', {}))['allowed']:
        return 'excluded_source'
    saved = thought.metadata.get('selection_context')
    if saved is not None and saved != getattr(engine.thoughts, 'context', {}):
        return 'different_selection_context'
    eligible = getattr(engine.thoughts, '_eligible', None)
    if eligible is not None:
        with engine.store.transaction() as connection:
            if not eligible(thought, connection):
                return 'outside_selection'
    return None


def _option(engine, thought, relations=()):
    description = thought.text.split('\n\n', 1)[0]
    selected = engine.thoughts.get(thought.id)
    status = 'selected_version' if selected is not None and selected.version == thought.version else 'pinned_historical_version'
    return {'questions': _questions(engine, thought), 'description': description, 'version_status': status,
            'support_current': engine._fresh(thought),
            'recorded_at': thought.created_at, 'actor': thought.origin.get('actor'),
            'description_complete': description == thought.text,
            'description_total_characters': len(thought.text),
            'relations': [{key: relation.get(key) for key in ('kind', 'direction', 'meaning')}
                          for relation in relations]}


def _dependents(thoughts, thought):
    reader = getattr(thoughts, 'direct_dependents', None)
    if reader is None:
        # Frozen selections expose selected versions, while the live dependency
        # index describes current heads. Inspect only their direct basis fields.
        yield from (row for row in thoughts.list_thoughts()
                    if any(b.get('thought_id') == thought.id
                           for b in row.bases))
        return
    after = ''
    while True:
        page = reader(thought.id, limit=DEPENDENT_PAGE_SIZE, after_id=after)
        yield from page
        if len(page) < DEPENDENT_PAGE_SIZE:
            return
        next_after = page[-1].id
        if next_after <= after:
            raise ModelError('direct dependent pagination made no progress')
        after = next_after


def _adjacent(engine, thought):
    current_focus = engine.thoughts.get(thought.id)
    if current_focus is not None and current_focus.version != thought.version:
        yield current_focus, {'kind': 'current_version', 'direction': 'outgoing',
            'from_ref': _ref(thought), 'to_ref': _ref(current_focus),
            'meaning': 'The current selected version of this same thought is available for discovery.'}
    for basis in thought.bases:
        if 'thought_id' in basis:
            ref = {'thought_id': basis['thought_id'], 'version': basis['version']}
            current = engine.thoughts.get(ref['thought_id'])
            changed = current is not None and current.version != ref['version']
            yield engine.thoughts.get(ref['thought_id'], ref['version']), {
                'kind': 'basis', 'direction': 'outgoing', 'from_ref': _ref(thought),
                'to_ref': ref, 'meaning': basis.get('meaning'), 'target_span': basis.get('span')}
            if not changed:
                continue
            yield current, {
                'kind': 'basis_current_head' if changed else 'basis', 'direction': 'outgoing', 'from_ref': _ref(thought),
                'to_ref': _ref(current) if current else ref, 'pinned_target_ref': ref,
                'meaning': ('The saved basis points to an older version; the current head is shown for discovery. '
                    + (basis.get('meaning') or '')) if changed else basis.get('meaning'),
                'target_span': None if changed else basis.get('span'), 'pinned_target_span': basis.get('span')}
    for parent in _dependents(engine.thoughts, thought):
        for basis in parent.bases:
            if basis.get('thought_id') == thought.id:
                changed = basis.get('version') != thought.version
                yield parent, {'kind': 'basis_current_head' if changed else 'basis', 'direction': 'incoming', 'from_ref': _ref(parent),
                    'to_ref': _ref(thought), 'pinned_target_ref': {'thought_id': thought.id, 'version': basis.get('version')},
                    'meaning': ('This parent refers to an older version of the current thought; its support needs checking. '
                        + (basis.get('meaning') or '')) if changed else basis.get('meaning'),
                    'target_span': None if changed else basis.get('span')}
    for link in engine.thoughts.links(thought.id):
        source, target = (link['source_id'], link['source_version']), (link['target_id'], link['target_version'])
        if _key(thought) not in (source, target):
            continue
        other = target if source == _key(thought) else source
        yield engine.thoughts.get(*other), {'kind': 'semantic',
            'direction': 'outgoing' if source == _key(thought) else 'incoming',
            'from_ref': {'thought_id': source[0], 'version': source[1]},
            'to_ref': {'thought_id': target[0], 'version': target[1]},
            'link_id': link['id'], 'meaning': link['meaning'],
            'source_span': link.get('source_span'), 'target_span': link.get('target_span')}
        current = engine.thoughts.get(other[0])
        if current is not None and current.version != other[1]:
            yield current, {'kind': 'semantic_current_head',
                'direction': 'outgoing' if source == _key(thought) else 'incoming',
                'from_ref': _ref(thought), 'to_ref': _ref(current),
                'pinned_related_ref': {'thought_id': other[0], 'version': other[1]},
                'meaning': 'The saved relation references an older version; its current head is shown for discovery, not as a revalidated relation. '
                    + link['meaning']}


@timing.timed('relation_neighborhood')
def neighborhood(engine, thought, question):
    """Full direct +1 material and separate compact semantic/code catalogs.

    bindings addresses [focus, *neighbors]. model_options addresses neighbors only.
    relations and omissions are code-only. complete describes enumerated topology,
    not whether a model has read every full neighbor or every node in the graph.
    """
    reason = _admit(engine, thought, pinned=True)
    if reason:
        raise ModelError('navigation focus unavailable: ' + reason)
    neighbors, relations, omissions = {}, [], []
    for candidate, relation in _adjacent(engine, thought):
        reason = 'missing_exact_version' if candidate is None else _admit(engine, candidate, pinned=True)
        if reason:
            omissions.append({'relation': relation, 'reason': reason})
            continue
        if _key(candidate) != _key(thought):
            neighbors[_key(candidate)] = candidate
        relations.append(relation)
    rows = tuple(neighbors.values())
    by_endpoint = {}
    for relation in relations:
        for endpoint in (relation['from_ref'], relation['to_ref']):
            key = (endpoint['thought_id'], endpoint['version'])
            by_endpoint.setdefault(key, []).append(relation)
    options = tuple(_option(engine, row, by_endpoint.get(_key(row), ())) for row in rows)
    result = {'focus': thought, 'neighbors': rows,
              'bindings': tuple(_ref(row) for row in (thought, *rows)),
              'relations': tuple(relations), 'model_options': options,
              'complete': True, 'omissions': tuple(omissions)}
    engine._emit('navigation_neighborhood', question=question, focus=_ref(thought),
                 bindings=result['bindings'], relations=result['relations'], omissions=result['omissions'])
    return result


def _matches(text, quote):
    if not isinstance(text, str):
        return []
    spans, start = [], 0
    while (start := text.find(quote, start)) >= 0:
        spans.append({'start': start, 'end': start + len(quote)})
        start += 1
    return spans


def _resolve(quote, visible):
    found = {}
    selected_versions = {_key(thought) for thought, record, _ in visible
                         if record.get('version_status') == 'selected_version'}
    for thought, record, pinned in visible:
        spans = []
        for field in ('text', 'description', 'recorded_at', 'actor'):
            spans.extend({'field': field, **span} for span in _matches(record.get(field, ''), quote))
        for index, question in enumerate(record.get('questions', [])):
            spans.extend({'field': 'questions', 'question_index': index, **span}
                         for span in _matches(question, quote))
        if spans:
            entry = found.setdefault(_key(thought), [thought, pinned, []])
            entry[2].extend(span for span in spans if span not in entry[2])
    matches = list(found.values())
    if len(matches) > 1 and len({item[0].id for item in matches}) == 1:
        current = [item for item in matches if _key(item[0]) in selected_versions]
        if len(current) == 1:
            return current
    return matches


def _decision(response):
    exact_fields(response, {'action', 'question', 'quote', 'text'})
    action = response['action']
    if action not in ('inspect', 'focus', 'unknown'):
        raise ModelError('unknown thought navigation action')
    semantic_text(response['text'], 'navigation reason')
    for field, required in (('question', False), ('quote', action in ('inspect', 'focus'))):
        if required:
            semantic_text(response[field], 'navigation ' + field)
        elif response[field] is not None:
            raise ModelError('unexpected navigation ' + field)
    return action


class _Navigation:
    """Invocation-local progress state; canonical thoughts are never mutated."""
    def __init__(self, engine, question, explicit_ids, initial=None, guidance=None):
        self.engine, self.question = engine, question
        self.focus = None
        self.focus_pinned = False
        self.inspected = {}
        self.states = set()
        self.notice = None
        self.guidance = guidance
        rows = engine.retrieve(question, explicit_ids=explicit_ids) if initial is None else initial
        self.seeds = [row for row in rows if _admit(engine, row) is None]
        self.engine._emit('navigation_search', question=question,
                          bindings=tuple(_ref(row) for row in self.seeds), outcome=None)

    def visible(self):
        records = [{'role': 'question', 'text': self.question}]
        if self.guidance:
            records.append({'role': 'missing_detail_reason', 'text': self.guidance})
        visible = {}
        if self.focus is not None:
            catalog = neighborhood(self.engine, self.focus, self.question)
            content = self.engine._content(self.focus)
            content.pop('question', None)
            content['questions'] = _questions(self.engine, self.focus)
            content['version_status'] = _option(self.engine, self.focus)['version_status']
            content['recorded_at'] = self.focus.created_at
            content['actor'] = self.focus.origin.get('actor')
            records.append({'role': 'current_thought', **content})
            visible[_key(self.focus)] = (self.focus, content, self.focus_pinned)
            for thought, option in zip(catalog['neighbors'], catalog['model_options']):
                visible[_key(thought)] = (thought, option, True)
        for thought in self.seeds:
            visible.setdefault(_key(thought), (thought, _option(self.engine, thought), False))
        for thought, record, _ in visible.values():
            if self.focus is None or _key(thought) != _key(self.focus):
                records.append({'role': 'option', **record, 'inspected': _key(thought) in self.inspected})
        if self.notice:
            records.append({'role': 'navigation_notice', 'text': self.notice})
        return records, list(visible.values())

    def finish(self, reason, thought=None):
        self.engine._emit('navigation_complete', reason=reason,
                          thought=_ref(thought) if thought is not None else None,
                          inspected=tuple(_ref(row) for row in self.inspected.values()))
        return NavigationResult(thought, tuple(self.inspected.values()), reason)

    def run(self):
        while True:
            if self.focus is not None:
                reason = _admit(self.engine, self.focus, pinned=self.focus_pinned)
                if reason:
                    return self.finish('navigation_source_changed: ' + reason)
            records, visible = self.visible()
            state = (_key(self.focus) if self.focus is not None else None,
                     tuple(sorted(_key(row) for row, _, _ in visible)), tuple(sorted(self.inspected)),
                     self.notice is not None)
            if state in self.states:
                return self.finish('unchanged_navigation_frontier')
            self.states.add(state)
            response = self.engine._call(TASK, records,
                [_ref(row) for row, _, _ in visible], INSTRUCTIONS)
            action = _decision(response)
            if action == 'unknown':
                return self.finish(response['text'])
            result = self.choose(action, response, visible)
            if result is not None:
                return result

    def choose(self, action, response, visible):
        matches = _resolve(response['quote'], visible)
        if len(matches) != 1:
            self.engine._discard_provisional()
            self.notice = ('The previous quotation matched multiple thoughts; use a uniquely identifying visible quotation.'
                if matches else 'The previous quotation was not visible; use an exact visible quotation.')
            self.engine._emit('navigation_choice_repair', reason=self.notice)
            return None
        thought, pinned, spans = matches[0]
        reason = _admit(self.engine, thought, pinned=pinned)
        if reason:
            return self.finish('navigation_source_changed: ' + reason)
        self.engine._emit('navigation_choice', action=action, thought=_ref(thought),
                          quote=response['quote'], spans=spans, reason=response['text'])
        if action == 'focus':
            return self.finish(response['text'], thought)
        self.focus, self.focus_pinned = thought, pinned
        self.inspected.setdefault(_key(thought), thought)
        self.notice = None


def navigate(engine, question, explicit_ids=(), *, initial=None, guidance=None):
    """Locate useful content without a fixed graph depth or model-round budget."""
    question = semantic_text(question, 'question')
    navigation = _Navigation(engine, question, explicit_ids, initial, guidance)
    try:
        return navigation.run()
    except ModelContextExceeded:
        return navigation.finish('provider_context_capacity_exceeded; graph_navigation_incomplete')
