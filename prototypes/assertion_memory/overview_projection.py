"""Bounded model projections; canonical source and receipt audit remains separate."""
from __future__ import annotations

import json
import re

from .model import ModelError, ModelRequest, PROMPT_SERIALIZATION, format_prompt
from .dialogue import MAX_MODEL_INPUT_CHARACTERS

LIMIT = MAX_MODEL_INPUT_CHARACTERS
MAX_REVIEW_CHARACTERS = 32_000
REPAIR_SERIALIZATION = 'structured-review-repair-v1'


class CitationOutputError(ModelError):
    """An actual citation failed exact identity or the existing syntax grammar.

    The original message remains available to callers/debugging. Writer retry
    feedback uses only the fixed category and count, never this message.
    """
    def __init__(self, message, failure, invalid_members=1):
        self.failure = failure
        self.invalid_members = invalid_members
        super().__init__(message)


class RequestFamilyBudgetError(ModelError):
    """A structural packing failure, without a verdict on semantic composability."""
    def __init__(self, sizes, requests, max_draft):
        names = ('writer', 'writer_retry', 'reviewer_reserved', 'review_contract_repair_reserved')
        self.diagnostic = {'stage': 'preflight_family', 'limit': LIMIT, 'max_draft': max_draft,
            'prompt_serialization': PROMPT_SERIALIZATION, 'family_sizes': dict(zip(names, sizes)),
            'oversized_members': [name for name, size in zip(names, sizes) if size > LIMIT],
            'components': {name: _request_components(request) for name, request in zip(names, requests)}}
        super().__init__('overview complete writer/reviewer/repair family exceeds bounded request budget; ' +
            ', '.join(name+'='+str(size) for name, size in zip(names, sizes))+'; limit='+str(LIMIT))


def _request_components(request):
    """Content-free component counts; originals remain in the acquired proof."""
    components = {'instructions_characters': len(request.instructions), 'task_characters': len(request.task)}
    for row in request.records:
        kind = ('canonical_records' if not row.get('role') else
                'contributions' if row['role'] in ('verified_contribution', 'verified_view') else row['role'])
        item = components.setdefault(kind, {'count': 0, 'json_characters': 0})
        item['count'] += 1
        item['json_characters'] += len(json.dumps(dict(row), ensure_ascii=False, separators=(',', ':')))
    return components


_BRACKETS = re.compile(r'\[([^\[\]]*)\]')
_NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
_NUMERIC = re.compile(r'\s*'+_NUMBER+r'(?:\s*[,;]\s*'+_NUMBER+r')+\s*')
_LITERAL = re.compile(
    r'(?P<ticks>`+).*?(?P=ticks)'
    r'|(?P<fence>~{3,})[^\n]*\n.*?^(?P=fence)[ \t]*$'
    r'|"(?:\\.|[^"\\])*"'
    r"|(?<!\w)'(?:\\.|[^'\\])*'"
    r'|“[^”]*”|‘[^’]*’|«[^»]*»'
    r'|!?\[[^\[\]]*\]\((?:\\.|[^)\\])*\)'
    r'|!?\[[^\[\]]*\]\[[^\[\]]*\]'
    r'|^ {0,3}\[[^\[\]]+\]:[^\n]*'
    r'|\\\[(?:\\.|[^\n])*?\\\]', re.S|re.M)


def _inside(spans,start,end):
    return any(left<=start and end<=right for left,right in spans)


def _citation_tokens(text,allowed):
    """Literal context is data regardless of marker text or source-ID overlap."""
    spans=[match.span() for match in _LITERAL.finditer(text)]
    matches=list(_BRACKETS.finditer(text))
    covered={position for match in matches for position in (match.start(),match.end()-1)}
    for position,char in enumerate(text):
        if char in '[]' and position not in covered:
            if not _inside(spans,position,position+1):
                raise CitationOutputError('malformed unresolved overview citation syntax', 'citation_syntax')
    for match in matches:
        raw=match.group(1)
        ids=tuple(re.sub(r'^(?:source:|id:|@)','',key.strip(),flags=re.I).strip()
                  for key in re.split(r'[,;]',raw))
        line=text[text.rfind('\n',0,match.start())+1:match.start()]
        checkbox=raw.casefold()=='x' and re.fullmatch(r'\s*(?:[-*+]|\d+[.)])\s+',line)
        literal=(_inside(spans,*match.span()) or not raw.strip() or
                 _NUMERIC.fullmatch(raw) or checkbox)
        if not literal:
            yield match,ids


def citation_ids(text,allowed=()):
    """Extract actual citation groups with the same grammar used for rendering."""
    return [ids for _,ids in _citation_tokens(text,set(allowed))]


def render_citations(text,allowed):
    result=[]
    cursor=0
    for match,ids in _citation_tokens(text,set(allowed)):
        invalid=[key for key in ids if not key or key not in allowed]
        if invalid:
            raise CitationOutputError('unresolved overview citation: '+', '.join(invalid),
                                      'citation_identity', len(invalid))
        result.extend((text[cursor:match.start()],'['+', '.join(ids)+']'))
        cursor=match.end()
    return ''.join([*result,text[cursor:]])


def source_ids(rows, manifests=None):
    ids = {key for row in rows for key in ([row['id']] if row.get('id') and not row.get('role')
            else row.get('source_ids', []))}
    for row in rows:
        ids.update((manifests or {}).get(row.get('lineage_manifest_sha256'), ()))
    return ids


def unique_rows(rows):
    """A raw immutable body occurs once per request; semantic contributions stay separate."""
    result, seen = [], set()
    for row in rows:
        key = row.get('id') if not row.get('role') else None
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        result.append(row)
    return result


def compact_obligations(ledger, consumer, candidate_ids=()):
    consumed = set(candidate_ids)
    obligations = []
    for entry in ledger.obligations.values():
        row = {key: entry[key] for key in ('gap_id', 'operation', 'query', 'source_ids', 'limit',
                                          'status', 'served_ids', 'consumers')}
        row.update({key:entry[key] for key in ('include_source_evidence','proof_delivery_sha256') if key in entry})
        row['consumed_by_candidate'] = bool(entry['attempts']) and set(entry['served_ids']) <= consumed
        row['delivered_to_consumer'] = set(entry['served_ids']) <= ledger.deliveries.get(consumer, set())
        obligations.append(row)
    return {'role': 'retrieval_state', 'obligations': obligations, 'coverage': 'bounded_not_exhaustive'}


def repair_request(request, response, error):
    # format_prompt uses compact JSON, so a structured value needs no extra string layer.
    original = json.dumps(response, ensure_ascii=False, separators=(',', ':'))
    if len(original) > MAX_REVIEW_CHARACTERS:
        raise ModelError('overview review response exceeds reserved contract-repair budget')
    return ModelRequest(request.task, [*request.records, {'role': 'review_contract_feedback',
        'validation_error': str(error), 'original_response': json.loads(original)}], request.instructions +
        ' Preserve every original retrieval intent and unknown need. Keep gap_id, operation, query, '
        'search limit, refine, include_source_evidence and exact read IDs unchanged. Authorized additional reads are allowed. '
        'This is a STRUCTURAL repair, not a fresh semantic assessment. Copy every original '
        'source_gap_dispositions entry preserving event_id, run_id, disposition, source_omission, '
        'answer_qualified and qualification_quote exactly. Copy every relation_gap_dispositions '
        'entry preserving proposal_id, revision, disposition and answer_qualified exactly. '
        'Preserve source_omission inside every unknown_needs entry. Never turn answer_qualified=false '
        'into true, remove a gap entry, or change materiality to make the schema pass. '
        'Canonical source_ids for reads refer only to supplied canonical evidence IDs, not raw '
        'event_id, run_id, proposal_id or a guessed identifier. '
        'Use at most 2 context_requests, 2 deferred_context_requests and, for local review, '
        '4 global_requests. Local requests concern packet support; global_requests concern the '
        'whole question. Repair ambiguous ownership by meaning; never relabel every deferred request '
        'global or call unresolved packet grounding accepted. Copy unknown quotes exactly from '
        'overview_draft. Remove irrelevant tool commentary. Return JSON within '
        + str(MAX_REVIEW_CHARACTERS) + ' characters.',
        cache_context=request.cache_context)


def format_retry_request(request, diagnostic):
    """Keep grounded context exact; carry only output-contract categories and counts."""
    output=('the SAME summary/display_title JSON envelope; the summary field uses ' if 'Final output protocol: overview-display-pair-v1.' in request.instructions else 'nonempty plain text within ')
    return ModelRequest(request.task, request.records, request.instructions +
        ' Structural writer retry (not a semantic verdict). Regenerate from the SAME supplied '
        'grounded context. Validation metadata below contains no source facts. Return '+output+
        'max_encoded_characters JSON-string characters (including quotes and '
        'escaping). Preserve every material '
        'qualification, exception, attribution and supporting source meaning. Rewrite coherently '
        'and concisely; do not truncate prose or silently drop conditions to fit. Independent '
        'semantic review still applies. Do not produce source identifiers or citation addresses; '
        'the program retains packet references outside the prose. Validation metadata: ' +
        json.dumps(diagnostic, ensure_ascii=False, sort_keys=True), cache_context=request.cache_context)


def preflight_family(writer, reviewer, max_draft=8000):
    """Reserve draft, one structural writer retry, and one review contract repair."""
    # The draft is already present in reviewer. The placeholder reserves its serialized size.
    reserved = ModelRequest(reviewer.task, [dict(row, text='x' * max_draft)
        if row.get('role') == 'overview_draft' else row for row in reviewer.records],
        reviewer.instructions, cache_context=reviewer.cache_context)
    # Structured review inventories have their own output budget; prose size is
    # not a bound on how many source/relation qualifications must be retained.
    repair = repair_request(reserved, {'x': '\\' * ((MAX_REVIEW_CHARACTERS - 8) // 2)}, 'x' * 256)
    # String lengths fit ssize_t. Longest diagnostic category/stage and counts reserve
    # all retry feedback without ever retaining an invalid answer in the request family.
    retry = format_retry_request(writer, {'failure': 'citation_identity', 'stage': 'citations',
        'characters': 2**63-1, 'encoded_characters': 2**63-1,
        'max_encoded_characters': max_draft+2, 'invalid_members': 2**63-1})
    requests = (writer, retry, reserved, repair)
    sizes = [len(format_prompt(request)) for request in requests]
    if max(sizes) > LIMIT:
        raise RequestFamilyBudgetError(sizes, requests, max_draft)
    return sizes



def plan_writer_family(writer_builder, reviewer, max_draft=8000, *, preflight=preflight_family):
    """Largest encoded draft allowance for the complete, unchanged request family.

    Rebuild the writer for every candidate: its budget wording and digit width
    are occupied input too. Only RequestFamilyBudgetError is a packing verdict;
    malformed proof or other operational errors must retain their classification.
    The bound excludes the two JSON string quotes, as preflight_family does.
    """
    writer = writer_builder(max_draft)
    try:
        preflight(writer, reviewer, max_draft)
        return writer, max_draft
    except RequestFamilyBudgetError:
        pass
    # A genuine irreducible closure retains the exact smallest-family diagnostic.
    writer = writer_builder(1)
    preflight(writer, reviewer, 1)
    best = (writer, 1)
    low, high = 2, max_draft - 1
    while low <= high:
        bound = (low + high) // 2
        writer = writer_builder(bound)
        try:
            preflight(writer, reviewer, bound)
        except RequestFamilyBudgetError:
            high = bound - 1
        else:
            best = (writer, bound)
            low = bound + 1
    return best


def upstream_parts(contributions, evidence, links, scopes, cutoffs):
    """Consume only caller-verified summaries with lineage inside this frozen proof."""
    records = {row['id']: row for row in evidence}
    edge_ids = {edge['id'] for edge in links}
    parts = []
    for item in contributions:
        ids = item.get('source_ids', [])
        pinned = item.get('cutoffs', {key: item.get(key) for key in cutoffs})
        if (item.get('role') != 'verified_view' or not item.get('version_id') or
                not isinstance(item.get('summary'), str) or not item['summary'].strip() or
                not isinstance(ids, (list, tuple)) or not set(ids) <= records.keys() or
                not set(item.get('required_edge_ids', [])) <= edge_ids or
                any(pinned.get(key) != value for key, value in cutoffs.items()) or
                scopes and not set(item.get('scopes', [])) <= set(scopes)):
            raise ModelError('upstream semantic contribution does not match frozen authorized proof')
        # The caller already verified this immutable publication and its frozen
        # membership. Preserve legacy prose; its brackets do not select grounds.
        text = item['summary']
        parts.append({'key':'view:'+str(item['version_id']), 'final':text,
                      'records':[records[key] for key in ids],
                      'links':[edge for edge in links if edge['id'] in item.get('required_edge_ids', [])],
                      'upstream':dict(item), 'writer_hash':'upstream',
                      'review':{'status':'accepted','request_sha256':item.get('check_id'),
                                'reason':'Accepted upstream semantic version'}})
    return parts
