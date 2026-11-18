"""Technical visible-dialogue policy for automatic Codex capture only.

Read actor/role/channel/provenance, never interpret an exec body. Raw sources and
exact thought versions remain immutable audit. Other explicit integrations may
still supply attributed tool evidence.
"""
from __future__ import annotations
import json
from .thought_payload import decode_payload
from dataclasses import asdict, is_dataclass

POLICY = 'codex-visible-dialogue-only-v1'
_TRANSPORTS = {'codex-native-client', 'codex-desktop'}
_PRIVATE_CHANNELS = {'analysis', 'reasoning'}
_TOOL_TYPES = {'function_call', 'function_call_output', 'tool_call', 'tool_result',
               'custom_tool_call', 'custom_tool_call_output'}


def event_eligibility(event):
    raw = asdict(event) if is_dataclass(event) else dict(event)
    metadata = raw.get('metadata') or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    source, actor = raw.get('source', ''), raw.get('actor', '')
    automatic = (isinstance(source, str) and source.startswith('codex://')
        or metadata.get('transport') in _TRANSPORTS or metadata.get('ingest_provider') == 'codex')
    reason = None
    if automatic:
        role = metadata.get('role', raw.get('role', actor))
        channel = metadata.get('channel', raw.get('channel'))
        content_type = metadata.get('content_type', raw.get('content_type'))
        types = [metadata.get(key, raw.get(key)) for key in ('type', 'message_type', 'payload_type', 'response_item_type')]
        if role == 'tool' or actor == 'tool' or (isinstance(actor, str) and actor.startswith('tool:')):
            reason = 'automatic_codex_tool'
        elif channel in _PRIVATE_CHANNELS or content_type == 'reasoning' or role in ('analysis', 'reasoning'):
            reason = 'automatic_codex_private_channel'
        elif any(value in _TOOL_TYPES for value in types if isinstance(value, str)):
            reason = 'automatic_codex_tool_payload'
        elif role in ('system', 'developer') or actor in ('system', 'developer', 'reasoning'):
            reason = 'automatic_codex_non_dialogue_role'
    return {'allowed': reason is None, 'reason': reason, 'policy': POLICY}


def _origin_event(thought):
    origin = dict(thought.origin)
    legacy = thought.metadata.get('legacy', {}).get('original', {})
    metadata = legacy.get('metadata', {}) if isinstance(legacy, dict) else {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return {**origin, 'metadata': {**metadata, **{key: origin[key] for key in ('role', 'channel', 'content_type') if key in origin}}}


def thought_eligibility(thoughts, thought, *, memo=None):
    """Iteratively gate exact historical bases; safe for read-only audit consumers."""
    cache = memo if memo is not None else {}
    root = (thought.id, thought.version)
    pending, visiting = [(thought, False)], set()
    while pending:
        current, expanded = pending.pop()
        key = (current.id, current.version)
        if key in cache:
            continue
        children = []
        for basis in current.bases:
            if 'thought_id' in basis:
                child = thoughts.get(basis['thought_id'], basis['version'])
                if child is not None:
                    children.append(child)
        if not expanded:
            if key in visiting:
                raise ValueError('cyclic thought source derivation')
            visiting.add(key)
            pending.append((current, True))
            pending.extend((child, False) for child in children if (child.id, child.version) not in cache)
            continue
        visiting.discard(key)
        origin = event_eligibility(_origin_event(current))
        excluded = []
        if not origin['allowed']:
            excluded.append({'thought_id': current.id, 'version': current.version, 'reason': origin['reason']})
        with thoughts.store.transaction() as c:
            has_raw = c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_raw_events'").fetchone()
            event_ids = {basis['event_id'] for basis in current.bases if 'event_id' in basis}
            if current.origin.get('event_id'):
                event_ids.add(current.origin['event_id'])
            legacy = current.metadata.get('legacy', {}).get('original', {})
            legacy_metadata = legacy.get('metadata', {}) if isinstance(legacy, dict) else {}
            if isinstance(legacy_metadata, str):
                legacy_metadata = json.loads(legacy_metadata)
            if isinstance(legacy_metadata.get('event_id'), str):
                event_ids.add(legacy_metadata['event_id'])
            for event_id in event_ids if has_raw else ():
                row = c.execute('SELECT id,actor,source,metadata FROM memory_raw_events WHERE id=?', (event_id,)).fetchone()
                if row is not None:
                    result = event_eligibility(dict(row))
                    if not result['allowed']:
                        excluded.append({'event_id': event_id, 'reason': result['reason']})
        for child in children:
            excluded.extend(cache[(child.id, child.version)]['excluded_events'])
        excluded = list({json.dumps(row, sort_keys=True): row for row in excluded}.values())
        cache[key] = {'allowed': not excluded, 'reason': 'excluded_automatic_codex_source' if excluded else None,
                      'policy': POLICY, 'excluded_events': excluded}
    return cache[root]


def cleanup_batch(thoughts, *, limit=100):
    """Retire affected current families and complete excluded raw jobs, bounded rows."""
    if type(limit) is not int or limit < 1:
        raise ValueError('cleanup limit must be positive')
    from .thought_store import Thought
    processed, retired = 0, []
    memo = {}
    with thoughts.store.transaction(write=True) as c:
        c.execute('CREATE TABLE IF NOT EXISTS memory_thought_source_cleanup '
            '(policy TEXT PRIMARY KEY,head_after TEXT NOT NULL,head_end TEXT NOT NULL,raw_cursor INTEGER NOT NULL,raw_end INTEGER NOT NULL)')
        has_raw = c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_raw_events'").fetchone()
        end = c.execute('SELECT coalesce(max(rowid),0) FROM memory_raw_events').fetchone()[0] if has_raw else 0
        head_end = c.execute("SELECT coalesce(max(id),'') FROM memory_thoughts").fetchone()[0]
        c.execute('INSERT OR IGNORE INTO memory_thought_source_cleanup VALUES(?,?,?,0,?)', (POLICY, '', head_end, end))
        state = dict(c.execute('SELECT * FROM memory_thought_source_cleanup WHERE policy=?', (POLICY,)).fetchone())
        rows = c.execute('SELECT v.payload FROM memory_thoughts h JOIN memory_thought_versions v '
            'ON v.thought_id=h.id AND v.version=h.current_version WHERE h.id>? AND h.id<=? ORDER BY h.id LIMIT ?',
            (state['head_after'], state['head_end'], limit)).fetchall()
        for row in rows:
            thought = Thought(**decode_payload(row[0]))
            result = thought_eligibility(thoughts, thought, memo=memo)
            if not result['allowed']:
                proof = {'verification': 'accepted', 'expected_versions': {thought.id: thought.version},
                    'kind': 'technical_source_policy', 'policy': POLICY, 'excluded_events': result['excluded_events']}
                thoughts.retire(thought.id, 'Automatic Codex source is outside visible user/assistant dialogue.', proof)
                retired.append({'thought_id': thought.id, 'version': thought.version})
            state['head_after'] = thought.id
            processed += 1
        if len(rows) < limit:
            state['head_after'] = state['head_end']
        if processed < limit and has_raw:
            raw_rows = c.execute('SELECT rowid,id,actor,source,metadata FROM memory_raw_events WHERE rowid>? '
                'AND rowid<=? ORDER BY rowid LIMIT ?', (state['raw_cursor'], state['raw_end'], limit - processed)).fetchall()
            for row in raw_rows:
                result = event_eligibility(dict(row))
                if not result['allowed']:
                    c.execute("UPDATE memory_capture_jobs SET status='completed',phase='excluded',error=?,"
                        'lease_token=NULL,lease_until=NULL WHERE event_id=?', (POLICY + ': ' + result['reason'], row['id']))
                state['raw_cursor'] = row['rowid']
                processed += 1
            if not raw_rows:
                state['raw_cursor'] = state['raw_end']
        c.execute('UPDATE memory_thought_source_cleanup SET head_after=?,raw_cursor=? WHERE policy=?',
            (state['head_after'], state['raw_cursor'], POLICY))
        complete = state['head_after'] >= state['head_end'] and state['raw_cursor'] >= state['raw_end']
    return {'processed': processed, 'retired': retired, 'complete': complete, 'policy': POLICY}


def eligible_sql(alias='e'):
    """Equivalent raw-row guard, placed before context ORDER/LIMIT."""
    import re
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', alias):
        raise ValueError('raw source SQL alias must be an identifier')
    source, actor = alias + '.source', alias + '.actor'
    def meta(key):
        return "coalesce(json_extract(" + alias + ".metadata,'$." + key + "'),'')"
    automatic = (f"({source} LIKE 'codex://%' OR {meta('transport')} IN ('codex-native-client','codex-desktop') "
                 f"OR {meta('ingest_provider')}='codex')")
    invalid = [f"{meta('role')} IN ('tool','system','developer','analysis','reasoning')",
        f"{actor} IN ('tool','system','developer','reasoning')", f"{actor} LIKE 'tool:%'",
        f"{meta('channel')} IN ('analysis','reasoning')", f"{meta('content_type')}='reasoning'"]
    types = "('function_call','function_call_output','tool_call','tool_result','custom_tool_call','custom_tool_call_output')"
    invalid.extend(meta(key) + ' IN ' + types for key in ('type', 'message_type', 'payload_type', 'response_item_type'))
    return 'NOT (' + automatic + ' AND (' + ' OR '.join(invalid) + '))', []
