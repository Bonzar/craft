"""Answer at a selected thought and its immediate semantic neighborhood."""
from __future__ import annotations

import hashlib
import json

from .model import ModelError, SemanticUnavailable
from .thought_engine import ThoughtDetailNeeded, empty_material
from .thought_engine_contract import APPLICABILITY, exact_fields, semantic_text

READINESS = APPLICABILITY + '''
The candidate is the selected thought in full. Other records describe only its
immediate neighbors, or explicitly found additional material. This is not a dump
of search candidates. Respect questions as distinct semantic purposes.
A neighbor_option is a navigation description. description_complete=false means
its remaining content has NOT been read. It cannot establish missing facts or
absence of exceptions. Known qualifications/conflicts are supplied immediately.
Return ready only if the candidate itself already answers the original question
with all material conditions and conflicts accounted for. A potentially material
incomplete linked neighbor permits drill to read that known location. If the
candidate answers a concrete part but a specific premise or fact remains
unconfirmed, return lookup with a focused, searchable question for that gap.
Use build with question=null only when existing visible evidence supports a
materially fuller answer. Do not build just to repeat the candidate and note
lack of confirmation.
Only text fields are eligible support quotations; descriptions are routing hints.
Never introduce source IDs, node aliases, indices or addresses in your response.
'''


GAP_READINESS = READINESS + '''
The bounded independent lookup is now finished; do not request another search.
The missing question is labeled gap_question. Inspect only fully opened text and
raw quotations. Choose build with question=null ONLY if the additional evidence
actually resolves that gap. Otherwise choose partial with the exact unresolved
question, even if the new text is topically similar. Missing evidence does not
establish that the underlying fact is false. Do not put new facts in your reason.
'''


class PartialMemoryAnswer(SemanticUnavailable):
    # The selected thought supports part of the question, not a new thought.
    def __init__(self, thought, missing_question):
        self.thought = thought
        self.missing_question = missing_question
        super().__init__('The checked material does not confirm part of the question.')


def _key(thought):
    return thought.id, thought.version


def _record(engine, thought, *, role, full=False, option=None):
    content = dict(engine._content(thought))
    content.pop('question', None)
    content['questions'] = list(engine.question_texts(thought))
    stale = not engine._fresh(thought)
    unverified = thought.metadata.get('verification') == 'legacy_unverified' or stale
    if stale:
        content['material_availability'] = {'coverage': 'partial',
            'uncertainty': 'Supporting versions have changed; this old wording is a routing hint, not current grounding.'}
    if (full or (option or {}).get('description_complete')) and not unverified:
        return {'role': role, **content, **{key: option[key] for key in ('relations', 'version_status')
                if option is not None and key in option}}
    text = content.pop('text')
    content.pop('details', None)  # A neighbor description does not recursively open its own neighbors.
    content.update(option or {'description': text, 'description_complete': True})
    return {'role': 'neighbor_option', **content}


def _material(engine, question, selected, neighborhood, additional):
    records, bindings, full_keys = {}, {}, {_key(row) for row in (selected, *additional)}
    catalogs = [neighborhood(engine, row, question) for row in (selected, *additional)]
    for catalog in catalogs:
        focus = catalog['focus']
        key = _key(focus)
        records[key] = _record(engine, focus, role='candidate' if key == _key(selected) else 'material', full=True)
        bindings[key] = engine._basis(focus)
        notices = [{'reason': row.get('reason', 'unavailable_neighbor')} for row in catalog.get('omissions', ())]
        records[key]['relations'] = [{field: relation.get(field) for field in ('kind', 'direction', 'meaning')}
                                     for relation in catalog.get('relations', ())]
        records[key]['neighborhood_availability'] = {'complete': catalog.get('complete', False), 'omissions': notices}
        for neighbor, option in zip(catalog['neighbors'], catalog['model_options']):
            key = _key(neighbor)
            if key in full_keys or key in records:
                continue
            records[key] = _record(engine, neighbor, role='material', option=option)
            bindings[key] = engine._basis(neighbor)
    packet, pinned = list(records.values()), list(bindings.values())
    # Legacy wording remains a hint. Only exact admitted raw bases of an
    # explicitly opened thought can supply its original meaning here.
    for thought in (selected, *additional):
        if thought.metadata.get('verification') != 'legacy_unverified':
            continue
        for basis in thought.bases:
            if 'event_id' not in basis:
                continue
            raw = engine._raw_packet(basis['event_id'])
            packet, pinned = engine._merge_material(packet, pinned,
                [record for record, _ in raw], [binding for _, binding in raw])
    return packet, pinned


def _missing(question, reason, fingerprint, *, incomplete=False):
    error = ThoughtDetailNeeded(question, reason)
    error.packet_fingerprint = fingerprint
    error.incomplete = incomplete
    return error


def support_fingerprint(records, bindings):
    """Identify exact support and coverage, independent of routing and pair order."""
    if len(records) != len(bindings):
        raise ModelError('support records and bindings must remain paired')
    pairs = [json.dumps([binding, {key: value for key, value in record.items() if key != 'role'}],
                        ensure_ascii=False, sort_keys=True) for record, binding in zip(records, bindings)]
    return hashlib.sha256(json.dumps(sorted(pairs), ensure_ascii=False).encode()).hexdigest()


def _lookup_gap(engine, question, gap, bindings):
    excluded = {basis['thought_id'] for basis in bindings if 'thought_id' in basis}
    rows = engine.retrieve(gap, limit=5, exclude_ids=excluded)
    candidates = [row for row in rows if row.id not in excluded and engine._fresh(row)
                  and row.metadata.get('verification') != 'legacy_unverified'
                  and not engine._is_parallel_alternative(row)]
    fresh_records = [_record(engine, row, role='gap_evidence', full=True) for row in candidates]
    fresh_bases = [engine._basis(row) for row in candidates]
    raw_records, raw_bases = engine._pending_raw_material(gap, existing=bindings)
    fresh_records, fresh_bases = engine._merge_material(fresh_records,fresh_bases,raw_records,raw_bases)
    engine._emit('answer_gap_lookup', question=question, bindings=fresh_bases,
                 details={'missing_question':gap,'additional_candidates':len(fresh_bases)})
    return fresh_records, fresh_bases


def answer_selected(engine, question, selected, *, neighborhood, additional=(), seen_packets=None,
                    raw_material=((), ()), allow_partial=False):
    """Use one selected location; navigation owns any subsequent missing question.

    The caller may retain seen_packets across navigation/resume calls. There is
    no fixed round/depth limit; an unchanged packet cannot create new knowledge.
    The caller also owns question checks/cache receipts after successful return.
    """
    question = semantic_text(question, 'question')
    additional = tuple(row for row in additional if _key(row) != _key(selected))
    records, bindings = _material(engine, question, selected, neighborhood, additional)
    records, bindings = engine._merge_material(records, bindings, *raw_material)
    fingerprint = support_fingerprint(records, bindings)
    if seen_packets is not None:
        if fingerprint in seen_packets:
            raise _missing(question, 'The selected neighborhood is unchanged; no new supporting detail was found',
                           fingerprint, incomplete=True)
        seen_packets.add(fingerprint)
    engine._emit('answer_neighborhood', question=question, bindings=bindings,
                 selected=[engine._basis(selected)], details={'packet_fingerprint': fingerprint})
    can_ready = (selected.metadata.get('verification') != 'legacy_unverified'
                 and not engine._is_parallel_alternative(selected) and engine._fresh(selected))
    if can_ready:
        decision = engine._call('Check ready thought', [{'role': 'question', 'text': question}, *records], bindings, READINESS)
        exact_fields(decision, {'verdict', 'question', 'text'})
        reason = semantic_text(decision.get('text'))
        if decision.get('verdict') == 'ready':
            return selected
        if decision.get('verdict') == 'drill':
            raise _missing(semantic_text(decision.get('question'), 'missing question'), reason, fingerprint)
        lookup = decision['verdict'] in ('lookup','partial') or (
            decision['verdict'] == 'build' and decision.get('question') is not None)
        if lookup and allow_partial and selected.metadata.get('verification') == 'accepted':
            gap = semantic_text(decision.get('question'), 'missing question')
            fresh_records,fresh_bases = _lookup_gap(engine,question,gap,bindings)
            if not fresh_records:
                raise PartialMemoryAnswer(selected,gap)
            records,bindings = engine._merge_material(records,bindings,fresh_records,fresh_bases)
            check = engine._call('Check ready thought',
                [{'role':'question','text':question},{'role':'gap_question','text':gap},*records],
                bindings,GAP_READINESS)
            if check['verdict'] != 'build' or check['question'] is not None:
                raise PartialMemoryAnswer(selected,gap)
        elif lookup and decision['verdict'] != 'build':
            raise _missing(semantic_text(decision.get('question'), 'missing question'),reason,fingerprint)
        elif decision['verdict'] != 'build':
            raise ModelError('invalid answer readiness outcome')
    if not any(record.get('text') for record in records):
        reason = 'The selected descriptions have no admitted full source content'
        opened = {_key(row) for row in (selected, *additional)}
        if any((basis['thought_id'], basis['version']) not in opened for basis in bindings if 'thought_id' in basis):
            raise _missing(question, reason, fingerprint)
        raise empty_material(reason)
    try:
        return engine._construct(records, bindings, question=question, origin={'kind': 'inference'}, target=None)[0]
    except ThoughtDetailNeeded as need:
        need.packet_fingerprint = fingerprint
        raise


def can_refresh(engine, thought):
    """Related facts do not rewrite the historical speech act of a source leaf."""
    origin = thought.origin
    return (bool(engine.question_texts(thought)) and not thought.metadata.get('selection_context')
            and not origin.get('event_id')
            and origin.get('kind') not in ('source_interpretation', 'legacy_assertion'))


def whole_refresh_scope(engine, thought):
    return ({'role': 'whole_refresh_scope', 'prior_content': thought.text,
             'questions': list(engine.question_texts(thought)),
             'prior_verification': thought.metadata.get('verification', 'unknown'),
             'qualifications': engine._content(thought).get('qualifications', {})}, engine._basis(thought))


def refresh_thought(engine, thought, *, additional=()):
    """Rebuild one whole derived thought using the same verified publication path."""
    if not can_refresh(engine, thought):
        return thought
    from .thought_publication import _fresh_material
    pending, external, seen = list(thought.bases), [], set()
    while pending:
        basis = pending.pop()
        if basis.get('thought_id') == thought.id:
            key = basis['version']
            if key in seen:
                continue
            seen.add(key)
            previous = engine.thoughts.get(thought.id, key)
            if previous is None:
                raise ThoughtDetailNeeded('Which original grounds support this whole thought?',
                                          'A pinned historical self-basis is unavailable')
            pending.extend(previous.bases)
        else:
            external.append(basis)
    external.extend(engine._basis(row) for row in additional if row.id != thought.id)
    scope = whole_refresh_scope(engine, thought)
    _, records, bases = _fresh_material(engine, [({'text': thought.text, 'question': None}, external)],
                                        thought.origin, whole_refresh=scope)
    origin = {**thought.origin, 'kind': 'inference'}
    return engine._construct(records, bases, origin=origin, target=thought)[0]
