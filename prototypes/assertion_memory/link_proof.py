"""Compact source anchors shared by semantic verification and evidence disposition."""


MODIFYING_RELATIONS = frozenset({'exception_to','supersedes','contradicts'})


def interpreted_metadata(metadata):
    return metadata.get('interpretation', {}).get('status') == 'model_verified'


def requires_link_verification(kind, source_metadata, target_metadata):
    """The same immutable endpoint rule gates Worker and settled freshness."""
    return kind == 'depends_on' or (kind in MODIFYING_RELATIONS and
        (interpreted_metadata(source_metadata) or interpreted_metadata(target_metadata)))


def self_correction_proof(source, target, verdict):
    """Validate persisted correction anchors against their immutable references."""
    evidence = verdict.get('correction_evidence')
    if (verdict.get('status') != 'accepted' or verdict.get('basis') != 'self_correction'
            or source.get('actor') != target.get('actor') or not isinstance(evidence, dict)):
        return False
    for side, record in (('source', source), ('target', target)):
        span = evidence.get(side)
        if not isinstance(span, dict):
            return False
        origin = record['metadata'].get('event_id')
        if (span.get('role') not in ('user', 'assistant', 'tool')
                or (origin is not None and span.get('event_id') != origin)
                or not any(_contained_anchor(reference, {**span, 'relation':'statement'}, resolved=False)
                    for reference in record['metadata'].get('evidence_refs', []))):
            return False
    return True


def reported_decision_proof(source: dict, target: dict, verdict: dict, *, resolved: bool = True) -> dict | None:
    """Validate reported adoption proof without persisting source bodies.

    Verification requires resolved immutable source text. Evidence readers can validate
    the persisted compact anchors against the original compact assertion references.
    """
    evidence = verdict.get("decision_evidence")
    if (verdict.get("status") != "accepted" or verdict.get("basis") != "reported_decision"
            or not isinstance(evidence, dict)):
        return None
    source_refs = _decision_refs(source, evidence.get("source_refs"), resolved=resolved)
    target_refs = _decision_refs(target, evidence.get("target_refs"), resolved=resolved)
    if source_refs is None or target_refs is None:
        return None
    return {"basis": "reported_decision", "decision_evidence": {
        "source_refs": source_refs, "target_refs": target_refs}}


def _decision_refs(record: dict, spans: object, *, resolved: bool) -> list[dict] | None:
    """Contain every selected span and inherit original role and relation."""
    if not isinstance(spans, list) or not 1 <= len(spans) <= 64:
        return None
    references = record.get("metadata", {}).get("evidence_refs", [])
    if not isinstance(references, list):
        return None
    refs = []
    for span in spans:
        if not isinstance(span, dict):
            return None
        anchor = next((value for reference in references
                       if (value := _contained_anchor(reference, span, resolved=resolved)) is not None), None)
        if anchor is None:
            return None
        refs.append(anchor)
    origin = record.get("metadata", {}).get("event_id")
    if not any(ref["role"] == "user" and ref["relation"] == "statement"
               and (origin is None or ref["event_id"] == origin) for ref in refs):
        return None
    return refs


def _contained_anchor(reference: object, span: dict, *, resolved: bool) -> dict | None:
    if not isinstance(reference, dict):
        return None
    event, start, end = (span.get(key) for key in ("event_id", "start", "end"))
    left, right = reference.get("start"), reference.get("end")
    if (not isinstance(event, str) or not event or type(start) is not int or type(end) is not int
            or reference.get("event_id") != event or type(left) is not int or type(right) is not int
            or not 0 <= left <= start < end <= right):
        return None
    body = reference.get("text", reference.get("quote"))
    if (body is not None or resolved) and (not isinstance(body, str) or len(body) != right - left):
        return None
    anchor = {"event_id": event, "start": start, "end": end, "role": reference.get("role"),
              "relation": reference.get("relation", "statement")}
    # Persisted proof must keep the inherited attribution; verifier output cannot create it.
    if not resolved and any(span.get(key) != anchor[key] for key in ("role", "relation")):
        return None
    return anchor
