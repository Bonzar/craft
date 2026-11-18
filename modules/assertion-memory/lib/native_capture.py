"""Forward-only, explicit-session Codex JSONL reader. No session directory discovery.

Native-client input requires paired protocol proof; explicit human turns also
admit unambiguous singleton occurrences at a confirmed boundary. Physical
file witnesses stay local; canonical message IDs/source URIs never contain them.
"""
import hashlib
import os
import json
from pathlib import Path
from urllib.parse import quote
from datetime import datetime

CHAT_EXCLUDED_TYPES = frozenset({'function_call', 'custom_tool_call', 'function_call_output', 'custom_tool_call_output', 'reasoning'})

MAX_RECORD = 512 * 1024
MAX_POLL = 2 * 1024 * 1024
# One bounded record may carry 8 images, each <=2 MiB decoded. Normal text
# remains at MAX_RECORD; only explicit native media envelopes get this bound.
MAX_MEDIA_RECORD = 8 * (((2 * 1024 * 1024 + 2) // 3) * 4) + MAX_RECORD + 65536
HUMAN = {'user', 'user_message', 'user_input', 'remote_ios', 'realtime', 'composer', 'send_user_message_async_question'}
class PendingSource(ValueError): pass


FORK_KEYS = {'forked_from_id', 'forked_from', 'parent_session_id', 'parent_thread_id', 'resumed_from'}


def _tail(stream):
    stream.seek(0)
    lines, last = 0, b''
    while True:
        block = stream.read(65536)
        if not block: break
        lines += block.count(b'\n')
        last = block[-1:]
    return stream.tell(), lines, bool(last and last != b'\n')


def _header(stream, raw):
    header = stream.readline(65537)
    if len(header) > 65536: raise ValueError('Unsupported native header')
    if not header.endswith(b'\n'): raise PendingSource('Native header is not yet complete')
    row = json.loads(header)
    meta = row.get('payload', {})
    if (row.get('type') != 'session_meta' or meta.get('id') != raw['session_id']
            or not isinstance(meta.get('cwd'), str)
            or Path(meta['cwd']).resolve() != Path(raw['cwd']).resolve()):
        raise ValueError('Native physical identity/cwd differs from hook registration')
    return header, meta


def register(store, raw):
    if not all(isinstance(raw.get(key), str) and raw[key].strip() for key in ('session_id', 'cwd', 'transcript_path')):
        raise ValueError('Persistent physical session identity/path required')
    path = Path(raw['transcript_path'])
    if not path.is_absolute() or not Path(raw['cwd']).is_absolute():
        raise ValueError('Native path/cwd must be absolute')
    if len(raw['session_id']) > 256:
        raise ValueError('Physical session identity exceeds supported bound')
    old = store.session(raw['session_id'])
    if old:
        if old['path'] != str(path) or Path(old['cwd']).resolve() != Path(raw['cwd']).resolve():
            raise ValueError('Registered physical session path/cwd changed; review required')
        return old
    state = {'session_id': raw['session_id'], 'path': str(path), 'cwd': raw['cwd'],
        'registration_source': raw.get('source'), 'cursor': 0, 'line': 0, 'current': None,
        'turn': None, 'identity': None, 'header_hash': None, 'skip_partial': False, 'gaps': [], 'witnesses': [], 'checkpoint': None, 'fork_requires_current_session_proof': False}
    if path.exists():
        with path.open('rb') as stream:
            try: header, meta = _header(stream, raw)
            except PendingSource:
                return store.register_session(state)
            stat = os.fstat(stream.fileno())
            state.update(identity=[stat.st_dev, stat.st_ino], header_hash=hashlib.sha256(header).hexdigest(),
                fork_requires_current_session_proof=any(meta.get(key) is not None for key in FORK_KEYS))
            if raw.get('source') != 'startup':
                state['cursor'], state['line'], state['skip_partial'] = _tail(stream)
            state.update(attachment_start_offset=state['cursor'],attachment_start_line=state['line'],
                attachment_partial_record=state['skip_partial'])
    registered = store.register_session(state)
    if registered['path'] != state['path'] or Path(registered['cwd']).resolve() != Path(state['cwd']).resolve():
        raise ValueError('Concurrent physical session registration differs; review required')
    return registered


def _allowed_start(payload, meta, roots=None):
    attribution = payload.get('turn_attribution')
    if not isinstance(attribution, dict) or attribution.get('initiating_agent_path'):
        return False
    trigger = attribution.get('turn_trigger')
    parent,root=attribution.get('parent_turn_id'),attribution.get('root_turn_id')
    if trigger == 'goal':
        return (isinstance(parent,str) and isinstance(root,str) and len(parent)<=256 and len(root)<=256
            and payload.get('root_turn_id') == root and (roots or {}).get(parent) == root)
    if parent:
        return False
    if trigger in HUMAN: return True
    turn = payload.get('turn_id')
    return (trigger is None and {'turn_trigger','parent_turn_id','initiating_agent_path','root_turn_id'} <= attribution.keys()
        and attribution.get('root_turn_id') == turn and payload.get('root_turn_id') == turn
        and meta.get('originator') == 'Codex Desktop' and meta.get('source') == 'vscode')



def _service_context(row):
    payload=row.get('payload',{})
    if row.get('type')!='response_item' or payload.get('type')!='message' or payload.get('role')!='user':return False
    meta=payload.get('internal_chat_message_metadata_passthrough');parts=payload.get('content')
    if not isinstance(meta,dict) or not isinstance(parts,list) or not parts:return False
    kinds=meta.get('content_item_kinds');sources=meta.get('content_item_metadata')
    if not isinstance(kinds,list) or not isinstance(sources,list) or len(kinds)!=len(parts) or len(sources)!=len(parts):return False
    return all(isinstance(part,dict) and part.get('type')=='input_text'
        and isinstance(kind,str) and isinstance(source,dict) and isinstance(source.get('provenance'),dict)
        and source['provenance'].get('type') in ('agents_md','harness')
        for part,kind,source in zip(parts,kinds,sources))


def _exclude(state,reason,count=1):
    excluded=state.setdefault('exclusions',{})
    excluded[reason]=excluded.get(reason,0)+count


def _remember_turn(state):
    turn=state['turn']
    state.setdefault('dialogue_turn_roots',{})[turn['id']]=turn.get('root_id') or turn['id']


def _candidate(row, session, turn, *, human=False):
    # Same persisted RI pairing contract as the existing optional native adapter.
    payload = row.get('payload', {})
    if payload.get('id') is not None and (not isinstance(payload['id'], str) or len(payload['id']) > 256): return None
    if row.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'user':
        meta, parts = payload.get('internal_chat_message_metadata_passthrough'), payload.get('content')
        if not isinstance(meta, dict) or meta.get('turn_id') != turn or meta.get('content_item_kinds') != ['user.text']:
            return None
        sources = meta.get('content_item_metadata')
        if (not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], dict)
                or sources[0].get('provenance') != {'type': 'user'}): return None
        if isinstance(parts, list) and len(parts) == 1 and isinstance(parts[0], dict) and parts[0].get('type') == 'input_text':
            return 'R', parts[0].get('text'), payload.get('id')
    if (row.get('type') == 'event_msg' and payload.get('type') == 'item_completed'
            and (payload.get('thread_id') == session or human and payload.get('thread_id') is None)
            and payload.get('turn_id') == turn):
        item = payload.get('item', {})
        if item.get('id') is not None and (not isinstance(item['id'], str) or len(item['id']) > 256): return None
        parts = item.get('content')
        if (item.get('type') == 'UserMessage' and isinstance(parts, list) and len(parts) == 1
                and isinstance(parts[0], dict) and parts[0].get('type') == 'text'):
            return 'I', parts[0].get('text'), item.get('id')
    return None


def _enqueue(store, state, row, role, text, provider_id, position, actor, config, locator, *, artifact_ids=None):
    if not isinstance(text, str) or not text.strip(): return False
    turn = state['turn']['id']
    provider = ('codex:' + role + ':' + turn + ':' + provider_id) if isinstance(provider_id, str) and provider_id else None
    mid = store.occurrence_id(state['session_id'], locator, provider)
    source = 'codex://session/' + quote(state['session_id'], safe='') + '/turn/' + quote(turn, safe='') + '/message/' + quote(mid, safe='')
    if len(source) > 2048: raise ValueError('Canonical source identity exceeds supported bound')
    arguments = {'session_id': state['session_id'], 'message_id': mid, 'actor_id': actor,
        'role': role, 'text': text, 'source': source, 'scope': config.get('scope', 'default')}
    if artifact_ids:arguments['artifact_ids']=artifact_ids
    timestamp = row.get('timestamp')
    if isinstance(timestamp, str):
        try:
            instant = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            if instant.utcoffset() is not None:
                arguments['source_message_at'] = timestamp
                arguments['source_message_clock_basis'] = {'kind': 'producer', 'field': 'timestamp',
                    'value': timestamp, 'source': source, 'message_id': mid}
        except ValueError: pass
    return store.enqueue(arguments, local_position=position)


def _flush_user(store, state, config, *, final=False):
    """A proven human trigger permits a lone R; native-client/fork R still needs I."""
    turn = state.get('turn') or {}
    pending = turn.get('pending')
    if not pending or pending.get('emitted'): return 0
    if pending.get('photo'):
        if final:state['gaps'].append('user_photo_occurrence_not_proven')
        return 0
    if (turn.get('human') and not state.get('fork_requires_current_session_proof')
            and not turn.get('ambiguous')):
        pending['emitted'] = True
        turn['verified'] = True
        _remember_turn(state)
        return int(_enqueue(store, state, pending.get('row', {}), 'user', pending['text'], pending['id'],
            pending.get('position'), config.get('actor_id', 'user'), config,
            'user:' + turn['id'] + ':byte:' + str(pending.get('offset', 0))))
    if final:
        gap = ('fork_input_without_current_session_proof' if state.get('fork_requires_current_session_proof')
               else 'unverified_native_user_at_turn_end')
        state['gaps'].append(gap)
    return 0



def _assistant_mode(payload):
    """Explicit visible channel/phase wins; private phases are never corroborated."""
    if payload.get('type')!='message' or payload.get('role')!='assistant':return None
    channel,phase=payload.get('channel'),payload.get('phase')
    if channel in ('analysis','reasoning') or phase in ('analysis','reasoning'):return None
    if payload.get('recipient') not in (None,'all') or channel not in (None,'final','commentary'):return None
    if channel=='final' or phase=='final_answer':return 'final'
    if channel=='commentary' or phase=='commentary':return 'commentary'
    return 'pending'


def _chat_exclusion(payload):
    if payload.get('type') in CHAT_EXCLUDED_TYPES:
        return 'non_dialogue_' + payload['type']
    if payload.get('type') != 'message':
        return None
    if payload.get('role') in ('system', 'developer', 'tool'):
        return 'non_dialogue_role_' + payload['role']
    if payload.get('role') == 'assistant' and _assistant_mode(payload) is None:
        return 'assistant_private_or_unproven_route'
    return None


def _process(store, state, meta, row, offset, position, config):
    payload = row.get('payload', {})
    if not isinstance(payload, dict):
        state['gaps'].append('invalid_record')
        state['turn']=None
        return 0
    if row.get('type') == 'event_msg' and payload.get('type') == 'task_started':
        queued = _flush_user(store, state, config, final=True)
        state['witnesses'] = [row['_witness']]
        turn = payload.get('turn_id')
        allowed = _allowed_start(payload, meta, state.get('dialogue_turn_roots'))
        attribution = payload.get('turn_attribution') or {}
        goal=allowed and attribution.get('turn_trigger')=='goal'
        state['turn'] = {'id': turn, 'root_id': attribution.get('root_turn_id') if goal else turn,
            'allowed': allowed, 'human': allowed and attribution.get('turn_trigger') in HUMAN,
            'verified': goal, 'pending': None, 'ambiguous': False, 'assistant': None} if isinstance(turn, str) and turn and len(turn) <= 256 else None
        if goal and state['turn'] is not None:
            _remember_turn(state)
        return queued
    turn = state.get('turn')
    if row.get('type')=='compacted':
        _exclude(state,'harness_compaction')
        return 0
    if not turn or not turn['allowed']: return 0
    human_singleton = turn.get('human') and not state.get('fork_requires_current_session_proof')
    if _service_context(row):
        _exclude(state,'harness_service_context')
        return 0
    from .user_projection import is_photo_response,stage,completion_matches,render
    if is_photo_response(row):
        try:photo=stage(row,turn['id'],store)
        except (ValueError,TypeError,RecursionError):
            state['gaps'].append('unsupported_user_photo_schema');turn['ambiguous']=True
            return 0
        prior=turn.get('pending');turn['ambiguous']=bool(prior and not prior.get('emitted'))
        source_row={key:row[key] for key in ('timestamp','_witness') if key in row}
        turn['pending']={'photo':photo,'id':payload.get('id'),'row':source_row,'offset':offset,'position':position,'emitted':False}
        state['witnesses']=state['witnesses'][:1]+[row['_witness']]
        return 0
    pending_photo=turn.get('pending')
    if (pending_photo and pending_photo.get('photo') and row.get('type')=='event_msg'
            and payload.get('type')=='item_completed' and isinstance(payload.get('item'),dict)
            and payload['item'].get('type')=='UserMessage'):
        turn['pending']=None
        if turn.get('ambiguous') or not completion_matches(pending_photo['photo'],row,state['session_id'],turn['id'],human=human_singleton):
            state['gaps'].append('unsupported_user_photo_proof');turn['ambiguous']=False
            return 0
        projected=render(pending_photo['photo']);turn['verified']=True;_remember_turn(state);state['witnesses'].append(row['_witness'])
        _exclude(state,'generated_user_image_framing',pending_photo['photo']['generated_frame_parts'])
        for omission in projected['omissions']:state['gaps'].append('unsupported_user_photo_media:'+omission)
        return int(_enqueue(store,state,pending_photo['row'],'user',projected['text'],pending_photo['id'] or payload['item'].get('id'),
            pending_photo['position'],config.get('actor_id','user'),config,'user:'+turn['id']+':byte:'+str(pending_photo['offset']),
            artifact_ids=projected['artifact_ids']))
    candidate = _candidate(row, state['session_id'], turn['id'], human=human_singleton)
    if candidate:
        kind, text, native_id = candidate
        if (not isinstance(text, str) or not text.strip()
                or text.lstrip().startswith(('<environment_context>', '# AGENTS.md', '<codex_delegation>'))):
            turn['ambiguous'] = True
            return 0
        if kind == 'R':
            prior = turn.get('pending')
            turn['ambiguous'] = bool(prior and not prior.get('emitted'))
            turn['pending'] = {'text': text, 'id': native_id, 'row': row, 'offset': offset,
                               'position': position, 'emitted': False}
            state['witnesses'] = state['witnesses'][:1] + [row['_witness']]
            return 0
        pending = turn.get('pending')
        turn['pending'] = None
        if (pending is None and turn.get('human') and not turn.get('ambiguous')
                and (human_singleton or payload.get('thread_id') == state['session_id'])):
            # A singleton completion proves content under a human turn, not its producer clock.
            turn['verified'] = True
            _remember_turn(state)
            state['witnesses'].append(row['_witness'])
            return int(_enqueue(store, state, {}, 'user', text, native_id, position,
                config.get('actor_id', 'user'), config, 'user:' + turn['id'] + ':byte:' + str(offset)))
        if pending is None or turn['ambiguous'] or pending['text'] != text:
            turn['ambiguous'] = False
            state['gaps'].append('unpaired_or_ambiguous_user')
            return 0
        turn['verified'] = True
        _remember_turn(state)
        state['witnesses'].append(row['_witness'])
        if pending.get('emitted'): return 0
        # R is the original source. I corroborates occurrence identity; its completion
        # timestamp must never substitute for an absent R/source timestamp.
        return int(_enqueue(store, state, pending.get('row', {}), 'user', text, pending['id'] or native_id,
            pending.get('position'), config.get('actor_id', 'user'), config,
            'user:' + turn['id'] + ':byte:' + str(pending.get('offset', 0))))
    if (row.get('type') == 'response_item' and payload.get('role') == 'user'
            or row.get('type') == 'event_msg' and payload.get('type') == 'item_completed'
            and isinstance(payload.get('item'), dict) and payload['item'].get('type') == 'UserMessage'):
        turn['ambiguous'] = True
        state['gaps'].append('unsupported_user_schema')
        return 0
    boundary = (row.get('type') == 'response_item' and (payload.get('type') in
                ('function_call', 'custom_tool_call', 'function_call_output', 'custom_tool_call_output')
                or _assistant_mode(payload) in ('final','commentary'))
                or row.get('type') == 'event_msg' and payload.get('type') == 'task_complete')
    queued = _flush_user(store, state, config, final=payload.get('type') == 'task_complete') if boundary else 0
    exclusion = _chat_exclusion(payload) if row.get('type') == 'response_item' else None
    if exclusion:
        _exclude(state, exclusion)
        return queued
    if not turn['verified']:
        if row.get('type') == 'response_item' and _assistant_mode(payload) in ('final','commentary'):
            state['gaps'].append('visible_assistant_before_user_proof')
        return queued
    if row.get('type') == 'response_item':
        kind = payload.get('type')
        if kind == 'message' and payload.get('role') == 'assistant':
            mode=_assistant_mode(payload)
            if mode is None:
                _exclude(state,'assistant_private_or_unproven_route')
                return queued
            parts = payload.get('content')
            if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], dict) or parts[0].get('type') != 'output_text':
                state['gaps'].append('unsupported_visible_assistant_schema')
                return queued
            text = parts[0].get('text')
            if mode == 'pending':
                turn['assistant'] = {'row': row, 'offset': offset, 'position': position}
            else:
                if mode=='final' and turn.get('assistant'):
                    turn['assistant']=None
                    _exclude(state,'unproven_assistant_context_superseded_by_final')
                return queued + int(_enqueue(store, state, row, 'assistant', text, payload.get('id'), position,
                    'assistant', config, 'assistant:' + turn['id'] + ':byte:' + str(offset)))
    if row.get('type') == 'event_msg' and payload.get('type') == 'task_complete':
        pending = turn.get('assistant')
        if pending and _assistant_mode(pending['row']['payload']) is None:
            turn['assistant']=None
            _exclude(state,'assistant_private_or_unproven_route')
            return queued
        if pending and pending['row']['payload']['content'][0].get('text') == payload.get('last_agent_message'):
            assistant = pending['row']
            turn['assistant'] = None
            return queued + int(_enqueue(store, state, assistant, 'assistant', payload['last_agent_message'],
                assistant['payload'].get('id'), pending['position'], 'assistant', config,
                'assistant:' + turn['id'] + ':byte:' + str(pending['offset'])))
    return queued


def _exclude_prior_tool_gaps(state):
    retained=[]
    for gap in state.get('gaps', []):
        if gap in ('unsupported_tool_output_projection', 'unattributed_or_unsupported_tool_output') or gap.startswith('unsupported_tool_media:'):
            _exclude(state, 'prior_non_dialogue_tool_gap')
        else:
            retained.append(gap)
    state['gaps']=retained


def poll(store, session_id, config):
    state = store.session(session_id)
    if state is None: raise ValueError('Unregistered session')
    path = Path(state['path'])
    if not path.exists(): return {'queued': 0, 'status': 'waiting_transcript'}
    queued = 0
    with path.open('rb') as stream, store.db:
        try: header, meta = _header(stream, {'session_id': session_id, 'cwd': state['cwd']})
        except PendingSource: return {'queued': 0, 'status': 'waiting_header'}
        stat = os.fstat(stream.fileno())
        identity = [stat.st_dev, stat.st_ino]
        digest = hashlib.sha256(header).hexdigest()
        if state['identity'] is None:
            state.update(identity=identity, header_hash=digest,
                fork_requires_current_session_proof=any(meta.get(key) is not None for key in FORK_KEYS))
            if state['registration_source'] != 'startup':
                state['cursor'], state['line'], state['skip_partial'] = _tail(stream)
            state.update(attachment_start_offset=state['cursor'],attachment_start_line=state['line'],
                attachment_partial_record=state['skip_partial'])
        if state['identity'] != identity or state['header_hash'] != digest or stat.st_size < state['cursor']:
            raise ValueError('Native source replaced or truncated; review required')
        for witness in state.get('witnesses', []) + ([state['checkpoint']] if state.get('checkpoint') else []):
            stream.seek(witness['start'])
            data = stream.read(witness['end'] - witness['start'])
            if hashlib.sha256(data).hexdigest() != witness['sha256']:
                raise ValueError('Retained native proof bytes changed; review required')
        _exclude_prior_tool_gaps(state)
        stream.seek(state['cursor'])
        start = state['cursor']
        while stream.tell() - start < MAX_POLL:
            offset = stream.tell()
            line = stream.readline(MAX_MEDIA_RECORD + 1)
            if not line: break
            if state['skip_partial']:
                # A proven pre-attachment partial record is outside capture authority.
                # Discard it incrementally BEFORE applying new-record size/schema limits.
                state['cursor']=stream.tell()
                if line.endswith(b'\n'):
                    state['skip_partial']=False;state['line']+=1
                    _exclude(state,'pre_attachment_partial_record')
                continue
            if len(line) > MAX_MEDIA_RECORD:
                digest=hashlib.sha256(line)
                while line and not line.endswith(b'\n'):
                    line=stream.readline(MAX_RECORD+1);digest.update(line)
                if not line:stream.seek(offset);break
                state['gaps'].append('oversized_record');state['turn']=None
                state.setdefault('gap_witnesses',[]).append({'reason':'oversized_record','start':offset,
                    'end':stream.tell(),'sha256':digest.hexdigest()})
            elif not line.endswith(b'\n'):
                stream.seek(offset)
                break
            else:
                try:
                    row = json.loads(line)
                    if not isinstance(row,dict):raise ValueError('Native record must be an object')
                except (ValueError, UnicodeDecodeError):
                    state['gaps'].append('invalid_record')
                    state['turn'] = None
                    row = None
                payload=row.get('payload',{}) if isinstance(row,dict) else {}
                media_envelope=(isinstance(row,dict) and row.get('type')=='response_item'
                    and isinstance(payload,dict) and payload.get('type') in ('function_call_output','custom_tool_call_output')
                    and isinstance(payload.get('output'),list) and any(isinstance(v,dict)
                        and v.get('type') in ('input_image','image') for v in payload['output']))
                if isinstance(row,dict):
                    from .user_projection import is_photo_response
                    media_envelope=media_envelope or is_photo_response(row)
                excluded_chat_item=(isinstance(row,dict) and (row.get('type')=='compacted'
                    or row.get('type')=='response_item' and isinstance(payload,dict) and _chat_exclusion(payload) is not None))
                if len(line)>MAX_RECORD and not media_envelope and not excluded_chat_item:
                    state['gaps'].append('oversized_record');state['turn']=None
                    state.setdefault('gap_witnesses',[]).append({'reason':'oversized_record','start':offset,
                        'end':stream.tell(),'sha256':hashlib.sha256(line).hexdigest()})
                elif isinstance(row, dict):
                    row['_witness'] = {'start': offset, 'end': stream.tell(), 'sha256': hashlib.sha256(line).hexdigest()}
                    queued += _process(store, state, meta, row, offset, state['line'], config)
            state['cursor'] = stream.tell()
            state['line'] += 1
        saved_offset = stream.tell()
        checkpoint_start = max(0, state['cursor'] - 4096)
        stream.seek(checkpoint_start)
        state['checkpoint'] = {'start': checkpoint_start, 'end': state['cursor'],
            'sha256': hashlib.sha256(stream.read(state['cursor'] - checkpoint_start)).hexdigest()}
        stream.seek(saved_offset)
        state['gaps'] = state['gaps'][-64:]
        if 'gap_witnesses' in state:state['gap_witnesses']=state['gap_witnesses'][-64:]
        store.save_session(state)
    return {'queued': queued, 'status': 'registered', 'gaps': state['gaps'], 'backlog': state['cursor'] < stat.st_size}
