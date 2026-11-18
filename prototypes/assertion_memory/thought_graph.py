"""Read-only graph export of canonical thoughts and their exact version bases.

The graph-viewer-source aliases are a presentation boundary. Every semantic node
has the same thought shape; raw episodes are provenance, not semantic nodes.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import asdict
from urllib.parse import quote

SCHEMA = 'canonical-thought-graph/v1'
PROTOCOL = 'canonical-thought-graph-v1'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def version_node_id(thought_id, version):
    """Escape canonical identity rather than collapsing distinct pinned versions."""
    return 'thought-version:' + quote(thought_id, safe='') + ':' + str(version)


def _reference(thought):
    return {'thought_id': thought.id, 'version': thought.version}


def _raw_events(store, thoughts):
    ids = {basis['event_id'] for row in thoughts.values() for basis in row.bases if 'event_id' in basis}
    ids.update(row.origin['event_id'] for row in thoughts.values() if row.origin.get('event_id'))
    result = {}
    with store.store.transaction() as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_raw_events'").fetchone()
        if not exists:
            return result
        for event_id in sorted(ids):
            row = connection.execute('SELECT * FROM memory_raw_events WHERE id=?', (event_id,)).fetchone()
            if row is not None:
                result[event_id] = dict(row)
    return result


def _collect(store, include_history):
    heads = store.list_thoughts()
    current = {(row.id, row.version) for row in heads}
    pending = list(heads)
    if include_history:
        pending.extend(version for row in heads for version in store.versions(row.id))
    links = store.links()
    for link in links:
        for side in ('source', 'target'):
            row = store.get(link[side + '_id'], link[side + '_version'])
            if row is None:
                raise ValueError('semantic link has a missing pinned thought endpoint')
            pending.append(row)
    thoughts = {}
    from .thought_source_policy import thought_eligibility
    policy_memo = {}
    while pending:
        row = pending.pop()
        key = (row.id, row.version)
        if key in thoughts:
            continue
        if not thought_eligibility(store, row, memo=policy_memo)['allowed']:
            continue
        thoughts[key] = row
        for basis in row.bases:
            if 'thought_id' not in basis:
                continue
            child = store.get(basis['thought_id'], basis['version'])
            if child is None:
                raise ValueError('thought has a missing pinned basis version')
            pending.append(child)
    return thoughts, current, links


def _sessions(thoughts, raw):
    members, unresolved, parents = {}, {}, {}
    for key, row in thoughts.items():
        sessions = {row.origin['session_id']} if row.origin.get('session_id') else set()
        unknown = False
        children = []
        event_ids = {basis['event_id'] for basis in row.bases if 'event_id' in basis}
        if row.origin.get('event_id'):
            event_ids.add(row.origin['event_id'])
        for event_id in event_ids:
            event = raw.get(event_id)
            if event is not None and event.get('session_id'):
                sessions.add(event['session_id'])
            else:
                unknown = True
        for basis in row.bases:
            if 'thought_id' in basis:
                child = (basis['thought_id'], basis['version'])
                children.append(child)
                parents.setdefault(child, set()).add(key)
        members[key] = sessions
        unresolved[key] = unknown or not (sessions or children)
    queue = deque(thoughts)
    while queue:
        child = queue.popleft()
        for parent in parents.get(child, ()):
            updated = members[parent] | members[child]
            unknown = unresolved[parent] or unresolved[child]
            if updated != members[parent] or unknown != unresolved[parent]:
                members[parent], unresolved[parent] = updated, unknown
                queue.append(parent)
    return members, unresolved


def _selected(thoughts, members, session_id):
    if session_id is None:
        return set(thoughts)
    selected = {key for key in thoughts if session_id in members[key]}
    pending = list(selected)
    while pending:
        key = pending.pop()
        for basis in thoughts[key].bases:
            if 'thought_id' not in basis:
                continue
            child = (basis['thought_id'], basis['version'])
            if child not in selected:
                selected.add(child)
                pending.append(child)
    return selected


def _display_text(row, limit=80):
    title = row.text.splitlines()[0]
    if len(title) <= limit:
        return title, title
    boundary = title.rfind(' ', 0, limit)
    return title[:boundary if boundary > 0 else limit].rstrip() + '…', title


def _node(row, current, sessions, unresolved, raw, session_id, questions):
    refs = []
    for basis in row.bases:
        event = raw.get(basis.get('event_id'))
        if event is None or not event['prompt']:
            continue
        span = basis.get('span') or {'start': 0, 'end': len(event['prompt'])}
        refs.append({'episode_id': event['id'], **span})
    label, title = _display_text(row)
    return {'id': version_node_id(row.id, row.version), 'label': label, 'full_title': title,
            'label_method': 'content_excerpt', 'entity_type': 'thought',
            'thought_ref': _reference(row), 'text': row.text, 'questions': questions,
            'kind': None, 'actor': row.origin.get('actor'), 'source': row.origin.get('source'),
            'scope': row.origin.get('scope'), 'recorded_at': row.created_at,
            'episode_id': refs[0]['episode_id'] if refs else None, 'evidence_refs': refs,
            'valid_at': None, 'valid_precision': 'unknown', 'is_current': current,
            'session_ids': sorted(sessions),
            'session_membership': 'partial' if sessions and unresolved else 'known' if sessions else 'unknown',
            'session_context_only': session_id is not None and session_id not in sessions,
            'origin': row.origin, 'bases': row.bases, 'metadata': row.metadata,
            'thought': dict({key:value for key,value in asdict(row).items() if key != 'question'}, questions=questions)}


def _edges(thoughts, links, selected):
    edges = []
    for key in sorted(selected):
        row = thoughts[key]
        for ordinal, basis in enumerate(row.bases):
            if 'thought_id' not in basis:
                continue
            child = (basis['thought_id'], basis['version'])
            edges.append({'id': 'thought-basis:' + _digest([key, ordinal, basis]),
                'from_id': version_node_id(*key), 'to_id': version_node_id(*child),
                'kind': 'thought_basis', 'relation_type': 'derivation',
                'recorded_at': row.created_at, 'basis': basis.get('meaning'),
                'source_ref': _reference(row), 'target_ref': {'thought_id': child[0], 'version': child[1]},
                'target_span': basis.get('span'), 'provenance': basis})
    for link in links:
        source = (link['source_id'], link['source_version'])
        target = (link['target_id'], link['target_version'])
        if source not in selected or target not in selected:
            continue
        edges.append({'id': 'thought-link:' + link['id'],
            'from_id': version_node_id(*source), 'to_id': version_node_id(*target),
            'kind': 'thought_relation', 'relation_type': 'semantic', 'basis': link['meaning'],
            'recorded_at': link.get('created_at'), 'recorded_at_known': bool(link.get('created_at')),
            'source_ref': {'thought_id': source[0], 'version': source[1]},
            'target_ref': {'thought_id': target[0], 'version': target[1]},
            'source_span': link.get('source_span'), 'target_span': link.get('target_span'),
            'provenance': link})
    return sorted(edges, key=lambda row: row['id'])


def export_graph(thought_store, *, session_id=None, include_history=False, provider_id='unified-thoughts'):
    """Export one pinned read snapshot, with no legacy semantic-table fallback.

    Session membership is documentary ancestry, not a claim that every ancestor
    was constructed during that session. Unknown history remains explicitly unknown.
    """
    if session_id is not None and (not isinstance(session_id, str) or not session_id.strip()):
        raise ValueError('session_id must be nonempty text')
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ValueError('provider_id must be nonempty text')
    if type(include_history) is not bool:
        raise ValueError('include_history must be boolean')
    with thought_store.store.transaction():
        thoughts, current, links = _collect(thought_store, include_history)
        raw = _raw_events(thought_store, thoughts)
        members, unresolved = _sessions(thoughts, raw)
        selected = _selected(thoughts, members, session_id)
        nodes = [_node(thoughts[key], key in current, members[key], unresolved[key], raw, session_id,
                       thought_store.questions(thoughts[key]))
                 for key in sorted(selected)]
        event_ids = {ref['episode_id'] for node in nodes for ref in node['evidence_refs']}
        episodes = [{'id': key, 'text': raw[key]['prompt'], 'session_id': raw[key]['session_id'],
                     'actor': raw[key]['actor'], 'source': raw[key]['source'],
                     'scope': raw[key]['scope'], 'recorded_at': raw[key]['recorded_at'],
                     'metadata': json.loads(raw[key]['metadata'])} for key in sorted(event_ids)]
        document = {'schema': SCHEMA, 'provider_id': provider_id, 'protocol': PROTOCOL,
            'nodes': nodes, 'edges': _edges(thoughts, links, selected), 'episodes': episodes,
            'overviews': [], 'topic_links': [],
            'selector': {'session_id': session_id, 'include_history': include_history},
            'semantic_model': 'thought', 'session_membership_policy': 'exact-raw-and-origin-ancestry-v1',
            'unknowns': ['session_provenance_unknown'] if any(unresolved[key] for key in selected) else []}
        document['source_revision'] = _digest(document)
        return document
