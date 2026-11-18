"""Optional raw-only native transcript watcher; no hooks or session discovery.

An explicit fast Desktop profile and explicit absolute transcript files are the
entire authority. CaptureQueue and Desktop native proof remain the capture path.
The watcher streams fixed byte slices and retains compact physical witnesses.
Unsupported proof remains visible; this adapter never calls a model.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import signal
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

from .desktop import _native_origin, load_config
from .desktop_fast import launch_worker, validate_config
from .derived_work import DerivedWorkQueue
from .view_name_work import NameWorkQueue
from .queue import CaptureQueue
from .storage import MemoryStore

SQLITE_PRIMARY_ERROR_MASK = 0xFF
RETRYABLE_SQLITE_ERRORS = frozenset((sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED))
WINDOW = 2 * 1024 * 1024


class IntakeGap(ValueError):
    """The bounded source no longer proves continuous native capture."""


class IntakeOwned(RuntimeError):
    """Another live process holds this database's intake lock."""


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _durable_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class NativeIntake:
    def __init__(self, config, transcript_paths):
        validate_config(config)
        if config.get('mode') != 'fast' or not config.get('native_session_ids'):
            raise ValueError('native intake requires fast mode and an explicit physical session allowlist')
        profile_paths = config.get('native_transcript_paths', [])
        if not isinstance(profile_paths, list) or any(not isinstance(path, str) or not Path(path).is_absolute() for path in profile_paths):
            raise ValueError('native_transcript_paths must contain explicit absolute paths')
        transcript_paths = [*transcript_paths, *profile_paths]
        if not transcript_paths or any(not Path(path).is_absolute() for path in transcript_paths):
            raise ValueError('native intake requires explicit absolute transcript paths')
        self.config = config
        # Keep the supplied real source path; never copy transcripts into a fake source.
        self.paths = sorted(set(str(Path(path).absolute()) for path in transcript_paths))
        self.root = Path(str(Path(config['db']).resolve()) + '.native-intake')
        self._last_worker_probe = None
        self._identities = {}
        self.state_path = self.root / 'cursor.json'
        self.lock = None
        self.owner_pid = None
        authority = {key: config.get(key) for key in ('db', 'mode', 'scope', 'cwd_roots', 'native_session_ids',
            'capture_after_offsets', 'logical_session_aliases', 'user_actor_id', 'bootstrap_assistant_context',
            'native_transcript_paths')}
        self.authority = _digest(json.dumps(authority, sort_keys=True).encode())

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root / 'owner.lock').open('a+')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.lock.close()
            self.lock = None
            raise IntakeOwned('native intake already has a live lock owner') from error
        self.owner_pid = os.getpid()
        self.lock.seek(0)
        self.lock.truncate()
        self.lock.write(json.dumps({'pid': self.owner_pid, 'paths': self.paths, 'authority': self.authority}) + '\n')
        self.lock.flush()
        os.fsync(self.lock.fileno())
        return self

    def __exit__(self, *unused):
        if self.lock is not None:
            # Never unlink the lock inode: a concurrent open must see this same lock.
            self.lock.close()
            self.lock = None
            self.owner_pid = None

    def _state(self):
        if not self.state_path.exists():
            return {'version': 2, 'authority': self.authority, 'paths': self.paths, 'files': {}}
        state = json.loads(self.state_path.read_text(encoding='utf-8'))
        if (state.get('version') not in (1, 2) or state.get('authority') != self.authority or state.get('paths') != self.paths):
            raise IntakeGap('native intake authority/paths changed; explicit cursor review required')
        return state

    @staticmethod
    def _range_hash(stream, start, end):
        digest = hashlib.sha256()
        stream.seek(start)
        remaining = end - start
        while remaining:
            part = stream.read(min(65536, remaining))
            if not part:
                raise IntakeGap('native transcript truncated inside retained proof')
            digest.update(part)
            remaining -= len(part)
        return digest.hexdigest()

    def _read(self, path, prior):
        from .native_stream import scanner, feed, resolve, SLICE, MAX_BODY
        import copy
        with Path(path).open('rb') as stream:
            stat = os.fstat(stream.fileno())
            header = stream.readline(65537)
            if len(header) > 65536 or not header.endswith(b'\n'):
                raise ValueError('unsupported native transcript header')
            first = json.loads(header)
            meta = first.get('payload', {})
            if (first.get('type') != 'session_meta' or meta.get('id') not in self.config['native_session_ids']
                    or not isinstance(meta.get('cwd'), str) or not Path(meta['cwd']).is_absolute()):
                raise ValueError('native transcript physical session identity is not allowed')
            cwd = Path(meta['cwd']).resolve()
            if not any(cwd == Path(root).resolve() or Path(root).resolve() in cwd.parents for root in self.config['cwd_roots']):
                raise ValueError('native transcript cwd is not allowed')
            identity = [stat.st_dev, stat.st_ino]
            self._identities[str(path)] = identity
            if prior:
                if prior['identity'] != identity or prior['header'] != _digest(header):
                    raise IntakeGap('native transcript replaced; explicit source review required')
                if stat.st_size < prior['end']:
                    raise IntakeGap('native transcript truncated; explicit source review required')
                if 'scan' not in prior:
                    # v1's digest covers exactly its retained replay range. Prove
                    # it before streaming from that start; never reset EOF/zero.
                    if self._range_hash(stream, prior['start'], prior['end']) != prior['digest']:
                        raise IntakeGap('native committed bytes changed during v1 upgrade')
                    next_state = {'identity': identity, 'header': _digest(header), 'end': prior['start'],
                                  'scan': scanner(prior['start']), 'turns': {}, 'current': None,
                                  'gaps': [], 'legacy_upgrade': True}
                else:
                    next_state = copy.deepcopy(prior)
                    for proof in self._retained(next_state):
                        if self._range_hash(stream, proof['start'], proof['end']) != proof['hash']:
                            raise IntakeGap('native retained proof bytes changed; explicit source review required')
            else:
                next_state = {'identity': identity, 'header': _digest(header), 'end': len(header),
                              'scan': scanner(len(header)), 'turns': {}, 'current': None, 'gaps': []}
            start = next_state['end']
            stream.seek(start)
            data = stream.read(SLICE)
            try:
                projected = feed(next_state['scan'], data)
            except (ValueError, UnicodeError, RecursionError) as error:
                raise IntakeGap('unsupported or malformed native JSON structure: ' + str(error)) from error
            rows = []
            for begin, end, row in projected:
                if not isinstance(row, dict):
                    continue
                projected_row = row
                row = resolve(stream, row, body_limit=1024)
                if not isinstance(row.get('payload'), dict):
                    continue
                payload = row['payload']
                relevant = (row.get('type') == 'event_msg' and payload.get('type') in ('task_started', 'item_completed', 'task_complete')
                            or row.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') in ('user', 'assistant'))
                if relevant:
                    if end - begin <= MAX_BODY * 2:
                        row = resolve(stream, projected_row)
                        row['_memory_hash'] = self._range_hash(stream, begin, end)
                    else:
                        # Explicit oversized/schema gap; never reread a giant
                        # interesting scalar as body or make it an origin witness.
                        row['_memory_hash'] = None
                        row['_memory_record_oversized'] = True
                    row['_memory_offset'], row['_memory_end'] = begin, end
                    rows.append(row)
            next_state['end'] = start + len(data)
            tail_start = max(len(header), next_state['end'] - 4096)
            next_state['checkpoint'] = {'start': tail_start, 'end': next_state['end'],
                                       'hash': self._range_hash(stream, tail_start, next_state['end'])}
            return meta, rows, stat.st_size > next_state['end'], next_state

    @staticmethod
    def _retained(state):
        for barrier in state.get('context_barriers', []):
            if barrier.get('source_hash'):
                yield {'start': barrier['source_range'][0], 'end': barrier['source_range'][1], 'hash': barrier['source_hash']}
        if state.get('checkpoint'):
            yield state['checkpoint']
        for turn in state.get('turns', {}).values():
            yield turn['start']
            for key in ('response',):
                if turn.get(key):
                    yield turn[key]
            yield from turn.get('proof_pair', [])
            yield from turn.get('assistants', [])

    @staticmethod
    def _witness(row):
        return {'start': row['_memory_offset'], 'end': row['_memory_end'], 'hash': row['_memory_hash']}

    def _row(self, path, witness):
        from .native_stream import MAX_BODY
        if witness['end'] - witness['start'] > MAX_BODY * 2:
            raise IntakeGap('interesting native record exceeds supported body bound')
        with Path(path).open('rb') as stream:
            stat = os.fstat(stream.fileno())
            if [stat.st_dev, stat.st_ino] != self._identities[str(path)]:
                raise IntakeGap('native transcript replaced during capture proof')
            if self._range_hash(stream, witness['start'], witness['end']) != witness['hash']:
                raise IntakeGap('native retained proof bytes changed')
            stream.seek(witness['start'])
            row = json.loads(stream.read(witness['end'] - witness['start']))
        return {**row, '_memory_offset': witness['start'], '_memory_end': witness['end'],
                '_memory_hash': witness['hash']}

    @staticmethod
    def _schema(connection):
        connection.execute("""CREATE TABLE IF NOT EXISTS native_occurrence_receipts (
            path TEXT NOT NULL, physical_session TEXT NOT NULL, turn_id TEXT NOT NULL,
            occurrence TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES memory_raw_events(id),
            body_hash TEXT NOT NULL, witnesses TEXT NOT NULL,
            PRIMARY KEY(path, physical_session, turn_id, occurrence))""")

    def _legacy_assistant(self, connection, path, meta, turn, row, text, message_id, clock):
        """Adopt only the exact physically proven immutable assistant event.

        Older intake did not label byte positions with unit/origin keys. Their
        absence is the only permitted provenance difference; never rewrite raw.
        """
        from urllib.parse import quote
        from .queue import _identity
        logical = self.config.get('logical_session_aliases', {}).get(meta['id'], meta['id'])
        source = (f"codex://session/{quote(meta['id'], safe='')}/turn/{quote(turn['id'], safe='')}/assistant/"
                  + quote(message_id, safe=''))
        event_id = _identity('event', logical, turn['id'], text, 'assistant', message_id)
        candidates = connection.execute('SELECT r.*, p.position FROM memory_raw_events r '
            'LEFT JOIN memory_event_positions p ON p.event_id=r.id WHERE r.id=? OR r.source=?',
            (event_id, source)).fetchall()
        if not candidates:
            return None
        expected = {'transport': 'codex-native-client' if turn['proof'].get('origin_kind') == 'native-client' else 'codex-desktop',
            'cwd': meta['cwd'], 'transcript_path': path, 'native_session_id': meta['id'],
            'native_turn_id': turn['id'], 'source_position': row['_memory_offset'], 'role': 'assistant'}
        if self.config.get('user_actor_id'):
            expected['declared_user_actor_id'] = self.config['user_actor_id']
        if len(candidates) != 1:
            raise IntakeGap('ambiguous legacy assistant physical occurrence at ' + str(row['_memory_offset']))
        old = candidates[0]
        metadata = json.loads(old['metadata'])
        from .source_message_clock import replay, FIELDS
        replay(metadata, clock)
        for key in FIELDS:
            metadata.pop(key, None)
        for key, value in (('source_position_unit', 'byte'), ('source_position_origin', path)):
            if key in metadata:
                if metadata[key] != value:
                    raise IntakeGap('conflicting legacy assistant ' + key + ' at ' + str(row['_memory_offset']))
                del metadata[key]
        if (old['id'] != event_id or old['session_id'] != logical or old['turn_id'] != turn['id']
                or old['prompt'] != text or old['actor'] != 'assistant' or old['source'] != source
                or old['scope'] != self.config.get('scope', 'default')
                or old['position'] != row['_memory_offset'] or metadata != expected):
            raise IntakeGap('unproven legacy assistant physical occurrence at ' + str(row['_memory_offset']))
        return old

    def _capture_occurrence(self, queue, path, meta, turn, occurrence, row, text, native_id, witnesses, *, assistant=False, barriers=None):
        from .native_occurrence import message_id, text_hash
        from .desktop_fast import _capture
        logical = self.config.get('logical_session_aliases', {}).get(meta['id'], meta['id'])
        raw = {'session_id': meta['id'], 'cwd': meta['cwd'], 'transcript_path': path,
               'turn_id': turn['id'], 'prompt': text,
               '_memory_position_unit': 'byte', '_memory_position_origin': path}
        if barriers and not assistant:
            raw['_memory_unavailable_context_before'] = list(barriers)
        mid = native_id if assistant else message_id(meta['id'], turn['id'], row, native_id, path, self._identities[str(path)])
        key = ('assistant:' if assistant else 'user:') + mid
        body_hash = text_hash(text)
        # Resolve the exact retained source bytes again at capture, so a writer
        # racing projection/classification cannot detach content from its proof.
        from .native_occurrence import candidate
        actual_rows = []
        for witness in witnesses:
            actual = self._row(path, witness)
            actual_rows.append(actual)
            if assistant:
                actual_text = actual['payload']['content'][0].get('text')
            else:
                proven = candidate(actual, meta['id'], turn['id'], human=turn['proof'].get('origin_kind') == 'human')
                actual_text = proven[1] if proven else None
            if actual_text != text:
                raise IntakeGap('native occurrence body changed during capture proof')
        from .native_occurrence import source_clock
        from .source_message_clock import replay
        # RI mirrors have distinct meanings/clocks. Select the retained input R,
        # even when it has no timestamp; never fall through to later completion I.
        selected = next((actual for actual in actual_rows if actual['type'] == 'response_item'), actual_rows[0])
        clock = source_clock(selected, path)
        with queue.store.transaction(write=True) as connection:
            self._schema(connection)
            receipt = connection.execute('SELECT * FROM native_occurrence_receipts WHERE path=? AND physical_session=? AND turn_id=? AND occurrence=?',
                (path, meta['id'], turn['id'], key)).fetchone()
            witness_json = json.dumps([{key: value for key, value in witness.items() if key != 'captured'} for witness in witnesses], sort_keys=True)
            if receipt:
                if receipt['body_hash'] != body_hash or receipt['witnesses'] != witness_json:
                    raise IntakeGap('native occurrence ID has contradictory physical witnesses')
                previous = queue.get_event(receipt['event_id'])
                replay(previous.metadata, clock)
                return 0
            actor = 'assistant' if assistant else self.config.get('user_actor_id') or ('codex-client' if turn['proof'].get('origin_kind') == 'native-client' else 'user')
            # Only the old exact physical recipe may be adopted. It must be the
            # proven first occurrence to use the historical task_started position.
            legacy = (self._legacy_assistant(connection, path, meta, turn, row, text, mid, clock)
                      if assistant else None)
            mirror_event = None
            if not assistant and len(witnesses) == 2 and turn['proof'].get('origin_kind') == 'human':
                response = witnesses[0]
                response_mid = message_id(meta['id'], turn['id'], {'_memory_offset': response['start']}, response.get('native_id'), path, self._identities[str(path)])
                lone = connection.execute('SELECT event_id, body_hash FROM native_occurrence_receipts WHERE path=? AND physical_session=? AND turn_id=? AND occurrence=?',
                    (path, meta['id'], turn['id'], 'user:' + response_mid)).fetchone()
                if lone:
                    if lone['body_hash'] != body_hash:
                        raise IntakeGap('human mirror contradicts captured physical occurrence')
                    previous = queue.get_event(lone['event_id'])
                    replay(previous.metadata, clock)
                    mirror_event = lone['event_id']
            if not assistant and mirror_event is None:
                from .desktop import event_identity
                legacy_source = event_identity(raw)[3]
                candidates = connection.execute('SELECT r.*, p.position FROM memory_raw_events r LEFT JOIN memory_event_positions p ON p.event_id=r.id '
                    'WHERE r.session_id=? AND r.turn_id=? AND r.prompt=? AND r.source=? AND NOT EXISTS '
                    '(SELECT 1 FROM native_occurrence_receipts nr WHERE nr.event_id=r.id)', (logical, turn['id'], text, legacy_source)).fetchall()
                for old in candidates:
                    metadata = json.loads(old['metadata'])
                    valid_position = old['position'] in [witness['start'] for witness in witnesses]
                    valid_position |= occurrence == 0 and old['position'] == turn['start']['start']
                    if (old['actor'] == actor and old['scope'] == self.config.get('scope', 'default') and old['source'] == legacy_source
                            and metadata.get('transcript_path') == path and metadata.get('native_session_id') == meta['id']
                            and metadata.get('native_turn_id') == turn['id'] and metadata.get('cwd') == meta['cwd']
                            and metadata.get('source_position') == old['position'] and valid_position):
                        replay(metadata, clock)
                        if legacy is not None:
                            raise IntakeGap('ambiguous legacy native occurrence adoption')
                        legacy = old
                # Same legacy URI with missing/mismatched physical evidence must
                # be reviewed rather than silently duplicated or guessed.
                if legacy is None and any(old['source'] == legacy_source for old in candidates):
                    turn.setdefault('migration_gaps', []).append('unproven legacy raw at occurrence ' + str(row['_memory_offset']))
                    return 0
            if mirror_event is not None:
                event_id, added = mirror_event, 0
            elif legacy is not None:
                if barriers and not json.loads(legacy['metadata']).get('unavailable_context_before'):
                    turn.setdefault('migration_gaps', []).append('legacy raw lacks excluded context barrier at ' + str(row['_memory_offset']))
                    return 0
                event_id, added = legacy['id'], 0
            else:
                from .queue import _identity
                existed = queue.get_event(_identity('event', logical, turn['id'], text, actor, mid)) is not None
                raw['_memory_message_id'] = mid
                event = _capture(queue, raw, self.config, turn['proof'], row['_memory_offset'],
                    assistant={'text': text, 'message_id': mid} if assistant else None, source_clock=clock)
                event_id, added = event.id, int(not existed)
            connection.execute('INSERT INTO native_occurrence_receipts VALUES (?,?,?,?,?,?,?)',
                (path, meta['id'], turn['id'], key, event_id, body_hash, witness_json))
            return added

    def _consume(self, queue, path, meta, rows, state):
        from .native_occurrence import candidate, permitted_text, text_hash
        captured = 0
        turns = state['turns']
        state.setdefault('context_barriers', [])
        for row in rows:
            payload = row['payload']
            if row.get('_memory_record_oversized'):
                state['gaps'].append('oversized interesting native record range ' + str(row['_memory_offset']) + ':' + str(row['_memory_end']))
                if row.get('type') == 'event_msg' and payload.get('type') == 'task_started':
                    state['current'] = None
                if payload.get('role') == 'user' or payload.get('type') == 'item_completed':
                    metadata = payload.get('internal_chat_message_metadata_passthrough', {})
                    turn_id = metadata.get('turn_id') if isinstance(metadata, dict) else None
                    turn = turns.get(turn_id or payload.get('turn_id') or state.get('current'))
                    if turn:
                        turn.pop('response', None)
                        turn['ambiguous'] = True
                if (payload.get('role') == 'assistant' and payload.get('recipient') in (None, 'all')
                        and (payload.get('channel') in ('final', 'commentary') or payload.get('phase') == 'final_answer')):
                    state['context_barriers'].append({'reason': 'excluded_visible_antecedent',
                        'physical_session_id': meta['id'], 'turn_id': state.get('current'), 'source_path': path,
                        'source_range': [row['_memory_offset'], row['_memory_end']], 'source_hash': None,
                        'role': 'assistant', 'unavailable_reason': 'oversized_source_range'})
                continue
            if row.get('type') == 'event_msg' and payload.get('type') == 'task_started':
                turn_id = payload.get('turn_id')
                if not isinstance(turn_id, str) or not turn_id:
                    state['gaps'].append('unsupported turn header at ' + str(row['_memory_offset']))
                    state['current'] = None
                    continue
                proof = _native_origin([], meta, row, meta['id'])
                previous = state.get('current')
                if previous in turns and not turns[previous].get('response') and not turns[previous].get('assistants'):
                    state['gaps'].extend(turns[previous].get('migration_gaps', []))
                    del turns[previous]
                turns[turn_id] = {'id': turn_id, 'start': self._witness(row), 'proof': proof,
                                  'count': 0, 'assistants': []}
                state['current'] = turn_id
                if len(turns) > 128:
                    raise IntakeGap('native outstanding turn proof backlog exceeds 128')
                continue
            turn_id = payload.get('turn_id') if row.get('type') == 'event_msg' else None
            metadata = payload.get('internal_chat_message_metadata_passthrough')
            if row.get('type') == 'response_item' and payload.get('role') == 'user' and isinstance(metadata, dict):
                turn_id = metadata.get('turn_id')
            turn = turns.get(turn_id or state.get('current'))
            excluded = not turn or turn['proof']['status'] == 'rejected' or turn['proof'].get('reason') == 'unknown_turn_origin'
            if excluded:
                if (row.get('type') == 'response_item' and payload.get('type') == 'message'
                        and payload.get('role') == 'assistant' and payload.get('recipient') in (None, 'all')
                        and (payload.get('channel') in ('final', 'commentary') or payload.get('phase') == 'final_answer')):
                    content = payload.get('content')
                    if (isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict)
                            and content[0].get('type') == 'output_text'
                            and (isinstance(content[0].get('text'), str) and content[0]['text'].strip()
                                 or isinstance(content[0].get('text'), dict) and '_native_oversized' in content[0]['text'])):
                        state['context_barriers'].append({'reason': 'excluded_visible_antecedent',
                            'physical_session_id': meta['id'], 'turn_id': turn['id'] if turn else None,
                            'source_path': path, 'source_range': [row['_memory_offset'], row['_memory_end']],
                            'source_hash': row['_memory_hash'], 'role': 'assistant'})
                        if len(state['context_barriers']) > 128:
                            raise IntakeGap('excluded context descriptor backlog exceeds 128')
                continue
            cutoff = self.config.get('capture_after_offsets', {}).get(meta['id'], 0)
            # A session can be attached while a turn is running. Membership is
            # decided by the occurrence, not by that turn's older start record.
            active = row['_memory_offset'] >= cutoff
            found = candidate(row, meta['id'], turn['id'], human=turn['proof'].get('origin_kind') == 'human')
            if found:
                kind, text, native_id = found
                if isinstance(text, dict) and '_native_oversized' in text:
                    state['gaps'].append('oversized interesting user body range ' + str(row['_memory_offset']) + ':' + str(row['_memory_end']))
                    turn.pop('response', None)
                    turn['ambiguous'] = kind == 'R'
                    continue
                if not permitted_text(text):
                    continue
                witness = self._witness(row)
                witness.update({'text_hash': text_hash(text), 'native_id': native_id})
                if kind == 'R':
                    turn['ambiguous'] = bool(turn.get('response')) and not turn['response'].get('captured')
                    turn['response'] = witness
                    continue
                response = turn.pop('response', None)
                ambiguous = turn.pop('ambiguous', False)
                if response is None and turn['proof'].get('origin_kind') == 'human':
                    if active:
                        captured += self._capture_occurrence(queue, path, meta, turn, turn['count'], row, text, native_id, [witness], barriers=state['context_barriers'])
                    state['context_barriers'] = []
                    turn['count'] += 1
                    continue
                if response is None or ambiguous or response['text_hash'] != witness['text_hash']:
                    state['gaps'].append('unpaired/ambiguous native user occurrence at ' + str(row['_memory_offset']))
                    continue
                turn['proof_pair'] = [response, witness]
                if turn['proof'].get('origin_kind') == 'native-client':
                    turn['proof'] = {**turn['proof'], 'status': 'verified', 'reason': 'persisted_native_user_input',
                                     'position': turn['start']['start']}
                if active and response['start'] >= cutoff:
                    captured += self._capture_occurrence(queue, path, meta, turn, turn['count'] - bool(response.get('captured')), row, text, native_id, [response, witness], barriers=state['context_barriers'])
                state['context_barriers'] = []
                turn['count'] += not response.get('captured', False)
            elif (row.get('type') == 'response_item' and payload.get('role') == 'user'
                    and isinstance(metadata, dict) and metadata.get('turn_id') == turn['id']
                    and metadata.get('content_item_kinds') == ['user.text']):
                state['gaps'].append('unsupported native user schema at ' + str(row['_memory_offset']))
                turn['ambiguous'] = True
            elif (row.get('type') == 'event_msg' and payload.get('type') == 'item_completed'
                    and payload.get('thread_id') == meta['id'] and payload.get('turn_id') == turn['id']
                    and isinstance(payload.get('item'), dict) and payload['item'].get('type') == 'UserMessage'):
                state['gaps'].append('unsupported completed native user schema at ' + str(row['_memory_offset']))
                turn['ambiguous'] = True
            elif (row.get('type') == 'response_item' and payload.get('role') == 'assistant'
                    and payload.get('type') == 'message' and payload.get('channel') in (None, 'final', 'commentary')
                    and payload.get('recipient') in (None, 'all')):
                content = payload.get('content')
                if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict) or content[0].get('type') != 'output_text':
                    state['gaps'].append('unsupported visible assistant schema at ' + str(row['_memory_offset']))
                    continue
                text = content[0].get('text')
                if isinstance(text, dict):
                    state['gaps'].append('oversized interesting assistant body range ' + str(row['_memory_offset']) + ':' + str(row['_memory_end']))
                    continue
                if not isinstance(text, str) or not text.strip():
                    continue
                if payload.get('channel') is None and payload.get('phase') != 'final_answer':
                    # task_complete corroboration may arrive later.
                    turn['assistants'].append(self._witness(row))
                elif turn['proof']['status'] == 'verified':
                    native_id = payload.get('id') or str(row['_memory_offset'])
                    bootstrap = self.config.get('bootstrap_assistant_context', {}).get(meta['id'])
                    if active or bootstrap == {'turn_id': turn['id'], 'message_id': native_id}:
                        captured += self._capture_occurrence(queue, path, meta, turn, -1, row, text, native_id, [self._witness(row)], assistant=True)
                else:
                    turn['assistants'].append(self._witness(row))
            if turn['proof']['status'] == 'verified' and turn.get('assistants'):
                retained = []
                for witness in turn['assistants']:
                    assistant_row = self._row(path, witness)
                    ap = assistant_row['payload']
                    text = ap['content'][0]['text']
                    if ap.get('channel') is None and ap.get('phase') != 'final_answer' and not (payload.get('type') == 'task_complete' and payload.get('last_agent_message') == text):
                        retained.append(witness)
                        continue
                    native_id = ap.get('id') or str(witness['start'])
                    bootstrap = self.config.get('bootstrap_assistant_context', {}).get(meta['id'])
                    if witness['start'] >= cutoff or bootstrap == {'turn_id': turn['id'], 'message_id': native_id}:
                        captured += self._capture_occurrence(queue, path, meta, turn, -1, assistant_row, text, native_id, [witness], assistant=True)
                turn['assistants'] = retained
                if len(retained) > 128:
                    raise IntakeGap('native outstanding assistant proof backlog exceeds 128')
        for turn in turns.values():
            response = turn.get('response')
            if (turn['proof'].get('origin_kind') == 'human' and response and not response.get('captured')
                    and not turn.get('ambiguous')):
                row = self._row(path, response)
                found = candidate(row, meta['id'], turn['id'], human=turn['proof'].get('origin_kind') == 'human')
                if found and permitted_text(found[1]):
                    if response['start'] >= self.config.get('capture_after_offsets', {}).get(meta['id'], 0):
                        captured += self._capture_occurrence(queue, path, meta, turn, turn['count'], row, found[1], found[2], [response], barriers=state['context_barriers'])
                    state['context_barriers'] = []
                    response['captured'] = True
                    turn['count'] += 1
        if sum(1 for _ in self._retained(state)) > 256:
            raise IntakeGap('native retained proof backlog exceeds 256 witnesses')
        if len(state.get('context_barriers', [])) > 128:
            raise IntakeGap('excluded context descriptor backlog exceeds 128')
        if len(state['gaps']) > 128:
            raise IntakeGap('native unresolved source gaps exceed 128; review required')
        return captured

    def poll(self):
        if self.lock is None or self.owner_pid != os.getpid():
            raise IntakeOwned('poll requires this process to hold the native intake lock')
        state = self._state()
        sources = [(path, self._read(path, state['files'].get(path))) for path in self.paths]
        changed = any(source[3] != state['files'].get(path) for path, source in sources)
        probe = self.config.get('auto_process', True) and (self._last_worker_probe is None or time.monotonic() - self._last_worker_probe >= 30)
        processing = {'scheduled': False, 'reason': 'no new raw input or pending-work probe'}
        captured, pending_work = 0, False
        if changed or probe:
            Path(self.config['db']).parent.mkdir(parents=True, exist_ok=True)
            with MemoryStore(self.config['db']) as store:
                queue = CaptureQueue(store)
                for path, (meta, rows, backlog, next_state) in sources:
                    # Receipts and raws commit together. External cursor advances
                    # afterwards; replay finds the exact immutable receipt.
                    with store.transaction(write=True):
                        captured += self._consume(queue, path, meta, rows, next_state)
                    state['files'][path] = next_state
                if self.config.get('auto_process', True) and (captured or probe):
                    pending_work = (queue._seconds_until_work(with_model=True) is not None
                        or DerivedWorkQueue(store).seconds_until_work(with_model=True) is not None
                        or NameWorkQueue(store).seconds_until_work(with_model=True) is not None)
            if changed:
                state['version'] = 2
                _durable_json(self.state_path, state)
            if pending_work:
                processing = launch_worker(self.config)
            if captured or probe:
                self._last_worker_probe = time.monotonic()
        gaps, pending = [], False
        for _, (_, _, _, file_state) in sources:
            gaps.extend(file_state.get('gaps', []))
            pending |= file_state['scan']['start'] != file_state['scan']['offset']
            for turn in file_state['turns'].values():
                pending |= bool(turn.get('response') or turn.get('assistants'))
                gaps.extend(turn.get('migration_gaps', []))
        return {'status': 'watching', 'captured': captured, 'pid': self.owner_pid, 'processing': processing,
                'backlog': any(source[2] for _, source in sources), 'pending': pending, 'gaps': gaps}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--transcript', type=Path, action='append', required=True,
                        help='Explicit native JSONL path; repeat for each allowed physical transcript')
    parser.add_argument('--interval', type=float, default=1.0)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    if not math.isfinite(args.interval) or args.interval < .1:
        parser.error('--interval must be finite and at least 0.1 seconds')
    stopping = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    try:
        with NativeIntake(load_config(args.config), args.transcript) as watcher:
            while not stopping.is_set():
                try:
                    result = watcher.poll()
                except sqlite3.OperationalError as error:
                    code = getattr(error, 'sqlite_errorcode', None)
                    if args.once or code is None or code & SQLITE_PRIMARY_ERROR_MASK not in RETRYABLE_SQLITE_ERRORS:
                        raise
                    print(json.dumps({'status': 'retrying', 'reason': 'database_busy',
                        'pid': watcher.owner_pid, 'error': str(error)}), file=sys.stderr, flush=True)
                    stopping.wait(args.interval)
                    continue
                if args.once or result['captured']:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                if args.once:
                    break
                stopping.wait(args.interval)
        return 0
    except (OSError, ValueError, KeyError, TypeError, IntakeOwned, sqlite3.Error) as error:
        print(f'native-intake stopped: {type(error).__name__}: {error}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
