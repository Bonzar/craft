"""Occurrence-level protocol proof shared by optional Desktop adapters."""
from __future__ import annotations

import hashlib


def candidate(row, session, turn, *, human=False):
    payload = row.get('payload', {})
    if row.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'user':
        meta, content = payload.get('internal_chat_message_metadata_passthrough'), payload.get('content')
        if not isinstance(meta, dict) or not isinstance(content, list) or len(content) != 1:
            return None
        sources = meta.get('content_item_metadata')
        if (meta.get('turn_id') != turn or meta.get('content_item_kinds') != ['user.text']
                or not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], dict)
                or sources[0].get('provenance') != {'type': 'user'}):
            return None
        part = content[0]
        if not isinstance(part, dict) or part.get('type') != 'input_text':
            return None
        if payload.get('id') is not None and not isinstance(payload['id'], str):
            return None
        return 'R', part.get('text'), payload.get('id')
    if (row.get('type') == 'event_msg' and payload.get('type') == 'item_completed'
            and (payload.get('thread_id') == session or human and payload.get('thread_id') is None)
            and payload.get('turn_id') == turn):
        item = payload.get('item')
        if not isinstance(item, dict) or item.get('type') != 'UserMessage':
            return None
        content = item.get('content')
        if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict) or content[0].get('type') != 'text':
            return None
        if item.get('id') is not None and not isinstance(item['id'], str):
            return None
        return 'I', content[0].get('text'), item.get('id')
    return None


def permitted_text(text):
    return (isinstance(text, str) and bool(text.strip())
            and not text.lstrip().startswith(('<environment_context>', '# AGENTS.md', '<codex_delegation>')))


def pairs(rows, session, turn, *, human=False):
    """RI/RIRI only; RR poisons that unmatched run instead of guessing by text."""
    pending, ambiguous, found = None, False, []
    for row in rows:
        item = candidate(row, session, turn, human=human)
        if not item or not permitted_text(item[1]):
            payload = row.get('payload', {})
            meta = payload.get('internal_chat_message_metadata_passthrough')
            unsupported_user = (row.get('type') == 'response_item' and payload.get('role') == 'user'
                and isinstance(meta, dict) and meta.get('turn_id') == turn
                or row.get('type') == 'event_msg' and payload.get('type') == 'item_completed'
                and payload.get('thread_id') == session and payload.get('turn_id') == turn
                and isinstance(payload.get('item'), dict) and payload['item'].get('type') == 'UserMessage')
            if unsupported_user and pending is not None:
                ambiguous = True
            continue
        kind, text, native_id = item
        current = {'row': row, 'text': text, 'id': native_id}
        if kind == 'R':
            if pending is not None:
                ambiguous = True
            pending = current
        elif pending is not None:
            if not ambiguous and pending['text'] == text:
                found.append((pending['row'], row, native_id, text))
            pending, ambiguous = None, False
        elif human:
            found.append((row, row, native_id, text))
    if human and pending is not None and not ambiguous:
        found.append((pending['row'], pending['row'], pending['id'], pending['text']))
    return found


def message_id(session, turn, row, native_id, path=None, identity=None):
    if isinstance(native_id, str) and native_id.strip():
        physical = native_id
    else:
        if path is None:
            raise ValueError('native byte occurrence requires a pinned physical path')
        from pathlib import Path
        stat = Path(path).stat() if identity is None else None
        device, inode = identity if identity is not None else (stat.st_dev, stat.st_ino)
        physical = f'file:{device}:{inode}:byte:{row["_memory_offset"]}'
    return 'native-user:' + session + ':' + turn + ':' + physical


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def source_clock(row, path):
    """Clock of this exact verified row, never session/task/mirror substitution."""
    if 'timestamp' not in row:
        return {}
    from .source_message_clock import normalize
    payload = row['payload']
    record_kind = (f'response_item/message/{payload.get("role")}' if row['type'] == 'response_item'
                   else 'event_msg/item_completed/UserMessage')
    return normalize({'source_message_at': row['timestamp'], 'source_message_clock_basis': {
        'kind': 'native-record', 'field': 'timestamp', 'value': row['timestamp'],
        'record_kind': record_kind, 'path': str(path), 'start': row['_memory_offset'],
        'end': row['_memory_end'], 'sha256': row['_memory_hash']}})
