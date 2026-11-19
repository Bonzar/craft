"""Compact facts from the runs of one message, without an extra model call."""
import json
from datetime import datetime

PAGE_SIZE = 6000
TEXT_PREVIEW = 850
LINK_PREVIEW_COUNT = 12


def _read_trace(service, run_id, section):
    args = {'run_id': run_id, 'section': section, 'limit': PAGE_SIZE}
    pieces = []
    while True:
        page = service.run_history(**args)
        pieces.append(page['content'])
        if page['response_complete']:
            return json.loads(''.join(pieces))
        args = page['continuation']['arguments']


def collect_report(service, result):
    references = {}
    relations = {}
    usage = {key: 0 for key in ('calls','unknown_calls','input_tokens',
                               'cached_input_tokens','output_tokens','model_ms','processing_ms')}
    for run_id in result['run_ids']:
        run = _read_trace(service, run_id, 'run')
        if run['finished_at']:
            delta = datetime.fromisoformat(run['finished_at']) - datetime.fromisoformat(run['started_at'])
            usage['processing_ms'] += round(delta.total_seconds() * 1000)
        _count_calls(usage, run['model_calls'])
        for event in _read_trace(service, run_id, 'events'):
            details = event['data']['details']
            if event['stage'] == 'publication':
                ref = details['thought']
                references[(ref['thought_id'],ref['version'])] = None
            if event['stage'] == 'semantic_relations':
                relations.update((link['id'],link) for link in details['outputs'])

    source = service.queue.get_event(result['event_id'])
    for identity in result['source_thought_ids']:
        if not any(key[0] == identity for key in references):
            thought = service.thoughts.get(identity)
            if thought is not None:
                references[(thought.id, thought.version)] = None
    return {
        'message': {'actor': source.actor, 'text': source.prompt[:TEXT_PREVIEW],
                    'source_message_at': source.metadata.get('source_message_at'),
                    'recorded_at': source.recorded_at},
        'thoughts': [_thought(service, *key) for key in references],
        'link_count': len(relations), 'links': list(relations.values())[:LINK_PREVIEW_COUNT], 'usage': usage,
    }


def _count_calls(totals, calls):
    for call in calls:
        totals['calls'] += 1
        totals['model_ms'] += call['duration_ms'] or 0
        clock = json.loads(call['usage']) if isinstance(call['usage'], str) else call['usage']
        tokens = (clock or {}).get('tokens') or {}
        if not all(type(tokens.get(key)) is int for key in ('input_tokens','output_tokens')):
            totals['unknown_calls'] += 1
        for key in ('input_tokens','cached_input_tokens','output_tokens'):
            totals[key] += tokens.get(key) or 0


def _thought(service, identity, version):
    row = service.thoughts.get(identity, version)
    if row is None:
        return {'thought_id': identity, 'version': version, 'unavailable': True}
    return {'thought_id': identity, 'version': version, 'kind': row.origin.get('kind'),
            'title': row.metadata.get('title'), 'text': row.text[:TEXT_PREVIEW],
            'questions': service.thoughts.questions(row), 'ground_count': len(row.bases)}
