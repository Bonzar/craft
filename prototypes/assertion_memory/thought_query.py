"""Central graph search and answer preparation, with resumable missing-detail frames."""
from dataclasses import dataclass, field
import hashlib
import json

from . import query_timing as timing
from .model import SemanticUnavailable
from .thought_engine import ThoughtDetailNeeded, empty_material
from .thought_engine_contract import semantic_text
from .thought_navigation import navigate, neighborhood
from .thought_answering import answer_selected, support_fingerprint


@dataclass
class _Frame:
    question: str
    guidance: str | None = None
    explicit: tuple = ()
    selected: object = None
    initial: list = field(default_factory=list)
    raw: tuple = ((), ())
    additional: list = field(default_factory=list)
    packets: set = field(default_factory=set)
    requested: set = field(default_factory=set)
    frontier: str | None = None
    initialized: bool = False


def _key(thought):
    return thought.id, thought.version


def _frontier(engine, frame, selected):
    catalog = neighborhood(engine, selected, frame.question) if selected else None
    base = engine._frontier(frame.initial, selected.id if selected else None, *frame.raw)
    # Old readiness receipts cannot certify explicit coverage under the new contract.
    payload = ['central-graph-answer-v2', base,
               catalog['bindings'] if catalog else (), catalog['relations'] if catalog else (),
               catalog['omissions'] if catalog else ()]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _load(engine, frame):
    frame.initial = engine.retrieve(frame.question, frame.explicit)
    frame.raw = engine._pending_raw_material(frame.question)


def _checked_candidate(engine, frame):
    cached_candidates = [row for row in frame.initial if frame.question in engine.question_texts(row)
        and row.metadata.get('verification') == 'accepted' and not engine._is_parallel_alternative(row)
        and engine._fresh(row)]
    for cached in cached_candidates:
        frame.frontier = _frontier(engine, frame, cached)
        if engine._checked(cached, frame.question, frame.frontier, allow_unrecorded=False):
            engine._emit('reuse', thought=engine._basis(cached), reason='checked question and unchanged graph neighborhood')
            return cached
    return None


def reuse_without_generation(engine, question):
    frame = _Frame(semantic_text(question, 'question'))
    _load(engine, frame)
    return _checked_candidate(engine, frame)


def _locate(engine, frame):
    if frame.guidance is None:
        _load(engine, frame)
    cached = _checked_candidate(engine, frame)
    if cached is not None:
        return cached
    frame.initialized = True
    if not frame.initial and frame.raw[0]:
        frame.frontier = _frontier(engine, frame, None)
        return None
    if not frame.initial:
        raise empty_material('No admitted search starting points or pending source messages were found')
    route = navigate(engine, frame.question, frame.explicit, initial=frame.initial,
                     guidance=frame.guidance)
    discovered = (*route.inspected, *((route.thought,) if route.thought is not None else ()))
    frame.initial = list({_key(row): row for row in (*frame.initial, *discovered)}.values())
    if route.thought is None:
        if frame.raw[0]:
            frame.frontier = _frontier(engine, frame, None)
            engine._emit('pending_source_answer', reason=route.reason, bindings=frame.raw[1])
            return None
        raise SemanticUnavailable('Graph search is incomplete: ' + route.reason)
    frame.selected = route.thought
    frame.frontier = _frontier(engine, frame, frame.selected)
    return None


def _step(engine, frame):
    if frame.selected is not None:
        return answer_selected(engine, frame.question, frame.selected, neighborhood=neighborhood,
            additional=frame.additional, seen_packets=frame.packets, raw_material=frame.raw,
            allow_partial=frame.guidance is None)
    records, bases = frame.raw
    for thought in frame.additional:
        records, bases = engine._merge_material(records, bases,
            [{'role': 'material', **engine._content(thought)}], [engine._basis(thought)])
    return engine._construct(records, bases, question=frame.question, origin={'kind': 'inference'})[0]


def _request_detail(frames, frame, need):
    packet = getattr(need, 'packet_fingerprint', None) or support_fingerprint(
        [*frame.raw[0], *({'text': row.text, 'bases': row.bases} for row in frame.additional)],
        [*frame.raw[1], *({'thought_id': row.id, 'version': row.version} for row in frame.additional)])
    # A new question may answer this support; another drill needs changed evidence.
    if getattr(need, 'incomplete', False) or any(packet in active.requested for active in frames):
        raise SemanticUnavailable('Supporting detail remains unresolved: ' + need.reason) from need
    frame.requested.add(packet)
    full = [*frame.additional, *((frame.selected,) if frame.selected is not None else ())]
    frames.append(_Frame(need.question, guidance=need.reason, initial=list(frame.initial),
                         raw=frame.raw, additional=list({_key(row): row for row in full}.values())))


def answer(engine, question, explicit_ids=()):
    """An explicit stack avoids an accidental recursion/depth limit on detail queries."""
    question = semantic_text(question, 'question')
    frames = [_Frame(question, explicit=tuple(explicit_ids))]
    while frames:
        frame = frames[-1]
        try:
            result = None
            if not frame.initialized:
                result = _locate(engine, frame)
            if result is None:
                result = _step(engine, frame)
        except ThoughtDetailNeeded as need:
            _request_detail(frames, frame, need)
            continue
        with timing.span('answer_check_write'):
            engine._save_check(result, frame.question, frame.frontier)
        frames.pop()
        if not frames:
            return result
        parent = frames[-1]
        if _key(result) in {_key(row) for row in parent.additional}:
            raise SemanticUnavailable('The detail search returned already consumed material; completeness is not established')
        if frame.question == parent.question:
            # A detail search may already have answered the original question.
            # Recheck that answer with the old location as context, rather than copying it into a new thought.
            if parent.selected is not None:
                parent.additional.append(parent.selected)
            parent.selected = result
        else:
            parent.additional.append(result)
        engine._emit('detail_fulfilled', question=parent.question, missing_question=frame.question,
                     selected=[engine._basis(result)], reason=frame.guidance)
