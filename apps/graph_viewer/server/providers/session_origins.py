"""Read-only provider boundary for proven assertion origin and session identity.

Assertion-memory ownership is its validated extraction ledger, never incidental
metadata/evidence/episode fields. JSON IR uses its documented episode_id link.
"""
import json
from ..common import timestamp


def session_origins(origin, cutoff, *, include_events=False):
    tables={row[0] for row in origin.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    sessions,event_sessions,owners={},{},{}
    if 'memory_raw_events' not in tables:
        return (sessions,owners,{}) if include_events else (sessions,owners)
    for row in origin.execute('SELECT * FROM memory_raw_events ORDER BY rowid'):
        meta=json.loads(row['metadata'] or '{}')
        if timestamp(row['recorded_at'])>cutoff or meta.get('source_available_at') and timestamp(meta['source_available_at'])>cutoff:
            continue
        native=meta.get('native_session_id')
        native=native if isinstance(native,str) and native.strip() else None
        logical=row['session_id']
        value=native or (logical if isinstance(logical,str) and logical.strip() else None)
        if value is None:
            continue
        kind='native' if native else 'logical'; key=kind+':'+value
        recorded=timestamp(row['recorded_at'])
        item=sessions.setdefault(key,{'id':key,'display_title':None,'provenance_kind':kind,
            'first_recorded_at':recorded,'last_recorded_at':recorded,'assertion_count':0})
        item['first_recorded_at']=min(item['first_recorded_at'],recorded)
        item['last_recorded_at']=max(item['last_recorded_at'],recorded)
        event_sessions[row['id']]=key
    if 'viewer_source_info' in tables:
        # Explicit JSON episode_id became this IR column; this is not the
        # assertion-memory logical episode field or an evidence-reference guess.
        owners={row['assertion_id']:row['episode'] for row in origin.execute('SELECT assertion_id,episode FROM memory_assertions')}
    elif 'memory_extraction_runs' in tables:
        from prototypes.assertion_memory.source_revision import selection,watermark
        maximum=watermark(origin)
        selection(origin,cutoff,maximum)  # validates modern reconciliation and ownership collisions
        for row in origin.execute('SELECT event_id,result FROM memory_extraction_runs WHERE id<=? ORDER BY id',(maximum,)):
            result=json.loads(row['result']); manifest=result.get('source_revision',result)
            for assertion in manifest.get('assertion_ids',[]):
                previous=owners.setdefault(assertion,row['event_id'])
                if previous!=row['event_id']:
                    raise ValueError('Conflicting immutable assertion session ownership')
    result={assertion:event_sessions[event] for assertion,event in owners.items() if event in event_sessions}
    return (sessions,result,event_sessions) if include_events else (sessions,result)
