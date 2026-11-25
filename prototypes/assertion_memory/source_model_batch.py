"""Combine initial construction requests from already admitted source jobs."""
from concurrent.futures import Future
from dataclasses import dataclass
import json
from threading import Condition

from .model import ModelContextExceeded, ModelError, ModelRequest
from .thought_processing_selection import canonical_session_id
from .thought_source_policy import event_eligibility

COLLECTION_SECONDS = 0.1
MAX_BATCH_CHARACTERS = 48000
MAX_EXPLICIT_BATCH_CHARACTERS = 256000


def pending_sources(queue, session_id, event_ids, *, capture_batch_id=None):
    events = []
    now = queue._time()
    for event_id in event_ids:
        event = queue.get_event(event_id)
        job = queue.get(event_id)
        if event is None or canonical_session_id(event) != session_id or not event_eligibility(event)['allowed']:
            raise ValueError(f'source event is outside the admitted session: {event_id}')
        if event.metadata.get('capture_batch_id') != capture_batch_id:
            raise ValueError('source event belongs to a different capture dispatch mode')
        if (job is None or job.event_id != event_id or job.phase != 'extract'
                or job.extracted_ids or not _is_ready(queue, job, now)):
            raise ValueError(f'source event is not ready for initial generation: {event_id}')
        events.append(event)
    if capture_batch_id is not None:
        return events  # exact producer order, not adjacent clocks
    return sorted(events, key=lambda event: (
        event.metadata.get('source_message_at') or event.recorded_at, event.id))


def _is_ready(queue, job, now):
    return (job.status == 'waiting_model'
            or (job.status == 'pending' and job.available_at <= now)
            or (job.status == 'running' and job.attempts < queue._max_attempts
                and queue._has_expired_claim(job.id, now)))


class PreparedSourceGroup:
    def __init__(self, requests, drafts):
        self.drafts = {bindings[0]['event_id']: (request, tuple(bindings), draft)
                       for (request, bindings), draft in zip(requests, drafts, strict=True)}

    def submit(self, request, bindings, generate):
        prepared = self.drafts.get(bindings[0]['event_id'])
        if prepared is None or prepared[:2] != (request, tuple(bindings)):
            raise ValueError('source request changed after group generation')
        return prepared[2]


@dataclass(frozen=True)
class SourceDraft:
    body: str
    run_id: str
    source: str


class SourceModelBatch:
    def __init__(self, capacity):
        self.capacity = capacity
        self.condition = Condition()
        self.pending = {}

    def submit(self, request, bindings, generate):
        result = Future()
        entry = (request, bindings, result)
        with self.condition:
            group = self.pending.setdefault(request.instructions, [])
            group.append(entry)
            if len(group) > 1:
                self.condition.notify_all()
                owner = False
            else:
                owner = True
                self.condition.wait_for(lambda: len(group) >= self.capacity, timeout=COLLECTION_SECONDS)
                self.pending.pop(request.instructions)
        if owner:
            self._deliver(group, generate)
        return result.result()

    def _deliver(self, group, generate):
        if len(group) == 1:
            group[0][2].set_result(None)
            return
        try:
            drafts = generate_source_drafts([(request, bindings) for request, bindings, _ in group], generate)
        except BaseException as error:
            # Every lane must be released even when the provider is cancelled.
            for _, _, result in group:
                result.set_exception(error)
        else:
            for (_, _, result), draft in zip(group, drafts, strict=True):
                result.set_result(draft)

def generate_source_drafts(group, generate, *, max_characters=MAX_BATCH_CHARACTERS, exact=False):
    records, bindings, schemas = [], [], {}
    for index, (request, source_bindings) in enumerate(group, 1):
        source = f'source_{index}'
        records.append({'source': source, 'records': request.records})
        bindings.extend(source_bindings)
        schemas[source] = request.response_schema
    if exact:
        records = _share_fragment_text(records)
    characters = len(json.dumps(records, ensure_ascii=False))
    if characters > max_characters:
        if exact:
            raise ModelError(f'Exact source group contains {characters} input characters, above {max_characters}')
        return [None] * len(group)
    instructions = (group[0][0].instructions + '\nApply these rules to each source independently. '
                    'Return a JSON object keyed by the supplied source names. Use only the '
                    'records of that source. Fragment numbers are local to each source. '
                    'Include every source, even if its thoughts are empty.')
    if exact:
        instructions += (' The first record supplies a shared text catalog. Each fragment has a '
                         'text_index into that catalog. Read its text there and cite only its local option.')
    request = ModelRequest('Construct thoughts for multiple source messages', records, instructions,
                           usage_task='Construct thought',
                           response_schema={'type': 'object', 'properties': schemas,
                                            'required': list(schemas), 'additionalProperties': False})
    try:
        body, run_id = generate(request, bindings)
        results = json.loads(body)
    except (ModelContextExceeded, json.JSONDecodeError) as error:
        if exact:
            raise ModelError(f'Exact source group provider failure: {type(error).__name__}') from error
        return [None] * len(group)
    if not isinstance(results, dict) or set(results) != set(schemas):
        if exact:
            received = len(results) if isinstance(results, dict) else 0
            raise ModelError(f'Exact source group expected {len(schemas)} labeled results and received {received}')
        return [None] * len(group)
    return [SourceDraft(json.dumps(results[source], ensure_ascii=False), run_id, source)
            if isinstance(results[source], dict) else None for source in schemas]


def _share_fragment_text(cases):
    """Represent identical text once while preserving every local fragment option."""
    text_ids = {}
    shared_cases = []
    for case in cases:
        records = []
        for record in case['records']:
            fragments = record.get('fragments')
            if fragments is not None:
                fragments = [{'option': fragment['option'],
                              'text_index': text_ids.setdefault(fragment['text'], len(text_ids))}
                             for fragment in fragments]
                record = dict(record, fragments=fragments)
            records.append(record)
        shared_cases.append(dict(case, records=records))
    return [{'fragment_texts': list(text_ids)}, *shared_cases]
