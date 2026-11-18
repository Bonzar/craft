"""Bounded JSONL projection. Source strings become byte spans, never tool bodies.

JSON grammar outside strings is checked by json.loads on the projection. Original
string grammar/UTF-8 is checked incrementally before substitution. A discriminator
may follow an arbitrarily large scalar. Limits constrain schema complexity, not
record/turn byte length. State contains keys, offsets and decoder tails only.
"""
from __future__ import annotations

import codecs
import json
import re

CHUNK = 64 * 1024
SLICE = 256 * 1024
MAX_BODY = 256 * 1024
MAX_PROJECTION = 64 * 1024
MAX_STRINGS = 2048
_SPECIAL = re.compile(b'["\\\\\\x00-\\x1f]')


def scanner(start):
    return {'start': start, 'offset': start, 'out': '', 'strings': [], 'token': None, 'pending': None}


def _flush(state, key=False):
    pending = state['pending']
    if pending is None:
        return
    if key:
        if pending['small'] is None:
            raise ValueError('native JSON key exceeds supported schema bound')
        # json.loads validates key escapes (including unicode escape digits).
        key_text = json.loads('"' + pending['small'] + '"')
        state['out'] += json.dumps(key_text, ensure_ascii=True)
    else:
        if len(state['strings']) >= MAX_STRINGS:
            raise ValueError('native JSON schema exceeds supported string count')
        state['out'] += '"@' + str(len(state['strings'])) + '"'
        state['strings'].append([pending['start'], pending['end']])
    state['pending'] = None


def _string_bytes(token, data):
    decoder = codecs.getincrementaldecoder('utf-8')('strict')
    decoder.setstate((bytes.fromhex(token['utf8']), 0))
    decoded = decoder.decode(data, final=False)
    token['utf8'] = decoder.getstate()[0].hex()
    if token['small'] is not None:
        if len(token['small']) + len(decoded) > 1024:
            token['small'] = None
        else:
            token['small'] += decoded


def feed(state, data):
    """Consume a bounded byte slice, returning completed projected records."""
    records = []
    i = 0
    while i < len(data):
        token = state['token']
        if token is not None:
            # After backslash, validate one escape or four hexadecimal digits.
            if token['escape']:
                byte = data[i]
                if token['escape'] == 'u':
                    if byte not in b'0123456789abcdefABCDEF':
                        raise ValueError('invalid native JSON unicode escape')
                    token['digits'] -= 1
                    if not token['digits']:
                        token['escape'] = ''
                elif byte == ord('u'):
                    token['escape'], token['digits'] = 'u', 4
                elif byte in b'"\\/bfnrt':
                    token['escape'] = ''
                else:
                    raise ValueError('invalid native JSON string escape')
                _string_bytes(token, data[i:i + 1])
                i += 1
                state['offset'] += 1
                continue
            match = _SPECIAL.search(data, i)
            end = match.start() if match else len(data)
            if end > i:
                _string_bytes(token, data[i:end])
                state['offset'] += end - i
                i = end
            if i == len(data):
                break
            byte = data[i]
            if byte == 34:
                if token['utf8']:
                    raise ValueError('incomplete native UTF-8 string')
                state['pending'] = {'start': token['start'], 'end': state['offset'] + 1,
                                    'small': token['small']}
                state['token'] = None
            elif byte == 92:
                if token['utf8']:
                    raise ValueError('invalid native UTF-8 before escape')
                _string_bytes(token, b'\\')
                token['escape'] = 'next'
            else:
                raise ValueError('unescaped control in native JSON string')
            i += 1
            state['offset'] += 1
            continue
        byte = data[i]
        if state['pending'] is not None:
            if byte in b' \t\r':
                i += 1
                state['offset'] += 1
                continue
            _flush(state, key=byte == 58)
        if byte == 34:
            state['token'] = {'start': state['offset'], 'small': '', 'utf8': '', 'escape': '', 'digits': 0}
        elif byte == 10:
            out = state['out']
            if out.strip():
                def object_pairs(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError('duplicate native JSON object key')
                        result[key] = value
                    return result
                def constant(value):
                    raise ValueError('non-JSON native scalar ' + value)
                value = json.loads(out, object_pairs_hook=object_pairs, parse_constant=constant)
                def spans(node):
                    if isinstance(node, str) and node.startswith('@'):
                        return {'_native_span': state['strings'][int(node[1:])]}
                    if isinstance(node, list):
                        return [spans(child) for child in node]
                    if isinstance(node, dict):
                        return {key: spans(child) for key, child in node.items()}
                    return node
                records.append((state['start'], state['offset'] + 1, spans(value)))
            next_offset = state['offset'] + 1
            state.clear()
            state.update(scanner(next_offset))
            i += 1
            continue
        elif byte in b' \t\r':
            pass
        else:
            if byte > 127 or byte < 32 and byte not in b'\t\r':
                raise ValueError('invalid native JSON structure')
            state['out'] += chr(byte)
            if len(state['out']) > MAX_PROJECTION:
                raise ValueError('native JSON schema exceeds projection bound')
        i += 1
        state['offset'] += 1
    return records


def resolve(stream, node, *, body_limit=MAX_BODY):
    """Resolve strings only after framing; large values stay explicit spans."""
    if isinstance(node, dict) and set(node) == {'_native_span'}:
        start, end = node['_native_span']
        if end - start > body_limit:
            return {'_native_oversized': [start, end]}
        stream.seek(start)
        return json.loads(stream.read(end - start))
    if isinstance(node, dict):
        return {key: resolve(stream, value, body_limit=body_limit) for key, value in node.items()}
    if isinstance(node, list):
        return [resolve(stream, value, body_limit=body_limit) for value in node]
    return node
