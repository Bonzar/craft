"""Independent, bounded-memory source-fixture oracle; refuses non-fixture paths.

Only status-relevant edges are retained; the million-edge count is streamed. This
oracle implements the explicitly reviewed fixture outcomes, not viewer helpers.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def fixture_connection(manifest: dict):
    path = Path(manifest['database']).resolve()
    if manifest.get('synthetic') is not True or path.name != 'source.sqlite3' or not (path.parent / '.viewer-qa-owned').is_file():
        raise ValueError('Only marked synthetic QA fixtures may be inspected directly')
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    connection.execute('PRAGMA query_only=ON')
    return connection


def digest_ids(values) -> str:
    return hashlib.sha256(('\n'.join(sorted(values)) + '\n').encode()).hexdigest()


def expected_slice(manifest: dict, *, mode: str, at: str, history: bool = False, undated: str = 'separate', captured_at: str = '2026-11-11T23:00:00.000000+00:00', explicit_cancellation: bool = False) -> dict:
    if mode not in {'current', 'known_then', 'known_now'} or undated not in {'separate','include','only'}:
        raise ValueError('Unknown temporal selector')
    cutoff = instant(at if mode == 'known_then' else captured_at)
    effective = instant(captured_at if mode == 'current' else at)
    ids, undated_ids, records = set(), set(), {}
    declared_cancelled = set()
    with fixture_connection(manifest) as db:
        for key, valid, recorded, scope, encoded in db.execute('SELECT assertion_id,valid_at,recorded_at,scope,metadata FROM memory_assertions'):
            metadata = json.loads(encoded)
            availability = [instant(recorded)]
            if metadata.get('source_available_at'):
                availability.append(instant(metadata['source_available_at']))
            if metadata.get('interpretation', {}).get('interpreted_at'):
                availability.append(instant(metadata['interpretation']['interpreted_at']))
            if max(availability) > cutoff:
                continue
            if valid is None:
                undated_ids.add(key)
            if mode == 'known_now':
                include = valid is not None and instant(valid) <= effective and undated != 'only'
                include = include or valid is None and undated in {'include','only'}
                if not include:
                    continue
            ids.add(key)
            records[key] = (scope, metadata.get('kind'), valid)
            if metadata.get('viewer_status', {}).get('disposition') == 'cancelled' and metadata['viewer_status'].get('published') is True:
                declared_cancelled.add(key)
        replacements, conflicts = {}, set()
        for origin, target, kind, recorded, encoded in db.execute("SELECT from_id,to_id,kind,recorded_at,metadata FROM memory_edges WHERE kind IN ('supersedes','contradicts','exception_to')"):
            if origin not in ids or target not in ids or instant(recorded) > cutoff:
                continue
            source_scope, source_kind, source_valid = records[origin]
            target_scope, target_kind, _ = records[target]
            metadata = json.loads(encoded)
            if 'proposal' in {source_kind,target_kind} or 'inference' in {source_kind,target_kind}:
                continue
            if kind == 'contradicts':
                conflicts.update((origin,target))
            elif kind == 'supersedes' and not metadata.get('condition') and source_scope == target_scope:
                effective_values = [source_valid, metadata.get('effective_at')]
                if any(value is not None and instant(value) > effective for value in effective_values):
                    continue
                if target_kind in {None,'assertion'} or source_kind == target_kind:
                    replacements.setdefault(origin,set()).add(target)
                else:
                    conflicts.update((origin,target))
        def reaches(start, goal):
            pending, seen = [start], set()
            while pending:
                key = pending.pop()
                if key == goal:
                    return True
                if key not in seen:
                    seen.add(key)
                    pending.extend(replacements.get(key,()))
            return False
        superseded = set()
        for origin, targets in replacements.items():
            for target in targets:
                if reaches(target,origin):
                    conflicts.update((origin,target))
                else:
                    superseded.add(target)
        hidden = superseded | (declared_cancelled if explicit_cancellation else set())
        visible = ids if history else ids-hidden
        edge_count = visible_edge_count = 0
        for origin,target,recorded in db.execute('SELECT from_id,to_id,recorded_at FROM memory_edges'):
            if origin in ids and target in ids and instant(recorded) <= cutoff:
                edge_count += 1
                visible_edge_count += origin in visible and target in visible
    return {'ids':ids,'visible_ids':visible,'undated_ids':undated_ids,'superseded_ids':superseded,
            'conflict_ids':conflicts,'declared_cancelled_ids':declared_cancelled,
            'counts':{'nodes':len(ids),'edges':edge_count,'visible_nodes':len(visible),'visible_edges':visible_edge_count},
            'ids_sha256':digest_ids(ids),'visible_ids_sha256':digest_ids(visible)}
