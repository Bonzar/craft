"""Deterministic subprocess boundary for automatic-overview protocol tests.

This is deliberately not evidence of semantic model quality. IDs always come
from the actual supplied provider packet, never from a preloaded database.
"""
from __future__ import annotations

import json
import os
import sys
import time


def _editorial_title(context):
    def topic(question):
        if question.startswith('What does ') and question.endswith(' require?'):
            return 'Reported '+question[len('What does '):-len(' require?')]+' requirements'
        return 'Reported context: '+question.rstrip('?')
    if context['purpose']=='navigation_index' and context['members']:
        return 'Topics: '+'; '.join(topic(member['question']) for member in context['members'])
    return topic(context['question'])


def respond(payload):
    task, rows = payload['task'], payload['records']
    if task == 'Name memory view':
        context=rows[0]
        return {'title':_editorial_title(context),'reference_ids':context['reference_ids'],
                'reason':'Synthetic nominal phrase grounded in the complete supplied questions; no domain acceptance.'}
    if task == 'Review memory view name':
        context=rows[0]
        accepted=context['title']==_editorial_title(context)
        return {'accepted':accepted,'title':context['title'],'reference_ids':context['reference_ids'],
                'input_fingerprint':context['input_fingerprint'],'context_sha256':context['context_sha256'],
                'reason':'Independent synthetic derivation from all supplied member questions matches.' if accepted else
                         'Candidate is not the complete supplied question map/nominal phrase.'}
    if task.startswith('Extract attributable'):
        target = rows[0]
        return {'assertions': [{'text': target['text'], 'quote': target['text'], 'kind': 'assertion'}]}
    if task in ('Verify contextual assertion interpretations', 'Verify semantic evidence links'):
        return {'verdicts': [{'index': row['index'], 'status': 'accepted', 'reason': 'Exact synthetic fixture.'}
                for row in rows if row.get('role') in ('interpretation', 'proposed_link')]}
    if task.startswith('Propose evidence links'):
        return {'links': [], 'branches': []}
    if task == 'Discover reusable memory themes':
        sources = [row for row in rows if row.get('role') == 'source']
        themes = []
        for name, marker in [('release policy', 'Release'), ('office supplies', 'Stationery')]:
            ids = [row['id'] for row in sources if marker.lower() in row['text'].lower()]
            if ids:
                themes.append({'question': 'What does ' + name + ' require?', 'source_ids': ids})
        assigned = {key for theme in themes for key in theme['source_ids']}
        return {'themes': themes, 'unassigned_source_ids': [r['id'] for r in sources if r['id'] not in assigned]}
    if task == 'Plan memory retrieval for a question':
        question = rows[0]['question']
        match = next((r['id'] for r in rows if r.get('role') == 'view' and r.get('question') == question), None)
        return {'reuse_view_id': match, 'related_view_ids': [], 'subquestions': [],
                'discover_topics': False, 'reason': 'Synthetic whole-scope protocol.'}
    if task == 'Review source material coverage':
        return {'source_coverage': {'status': 'accepted', 'reason': 'Fixture extraction copies its entire target.', 'omissions': []}}
    if task == 'Review thematic source membership':
        marker = 'Stationery' if 'office supplies' in rows[0]['question'] else 'Release'
        return {'source_ids': [row['id'] for row in rows if row.get('role') == 'candidate'
                               and marker.casefold() in row['text'].casefold()],
                'unresolved_source_ids': [], 'reason': 'Only the matching fixture topic belongs.'}
    if task == 'Plan reusable memory theme hierarchy':
        return {'groups': [{'question': 'What are the shared interfaces?',
                            'depends_on': [r['id'] for r in rows if r.get('role') == 'view']}]}
    if task == 'Review grounded memory contribution':
        return {'grounding_status': 'accepted', 'coverage': 'packet_claims', 'global_coverage': 'not_assessed',
                'reason': 'Synthetic packet copied exactly.', 'issues': [], 'context_requests': [], 'global_requests': []}
    if task.startswith('Review'):
        return {'status': 'accepted', 'coverage': 'all_claims', 'reason': 'Synthetic packet copied exactly.',
                'issues': [], 'context_requests': [], 'unknown_needs': []}
    if task.startswith('Verify') or task.startswith('Repair'):
        raise ValueError('Unsupported provider protocol task: ' + task)
    semantic = [r.get('summary', r.get('text', '')) for r in rows
                if r.get('role') in ('verified_view', 'verified_contribution')]
    raw = [r['text'] + ' [source:' + r['id'] + ']' for r in rows if r.get('id') and r.get('text')
           and r.get('role') not in ('verified_view', 'verified_contribution')]
    if not semantic and not raw:
        raise ValueError('Writer received no supplied source records: ' + task)
    return '\n'.join([*semantic, *raw])


def main():
    payload = json.loads(sys.stdin.read().split('\n\n', 1)[1])
    log = os.environ.get('AUTOMATIC_OVERVIEW_PROVIDER_LOG')
    if log:
        with open(log, 'a') as handle:
            handle.write(json.dumps({'pid': os.getpid(), 'worker_pid': os.getppid(), **payload}) + '\n')
    gate = os.environ.get('AUTOMATIC_OVERVIEW_GATE_PATH')
    if gate and payload['task'] == os.environ.get('AUTOMATIC_OVERVIEW_GATE_TASK'):
        try:
            descriptor = os.open(gate + '.claimed', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
            deadline = time.monotonic() + 60
            while not os.path.exists(gate + '.release'):
                if time.monotonic() > deadline:
                    raise TimeoutError('Synthetic provider barrier was not released')
                time.sleep(.02)
    result = respond(payload)
    print(result if isinstance(result, str) else json.dumps(result))


if __name__ == '__main__':
    main()
