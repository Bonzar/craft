"""Diagnostic only: mutable last-job pointers. Never an immutable causal fallback."""
import json
from ..common import APIError,timestamp
from .session_origins import session_origins

POLICY='legacy-session-output-pins-v1'


def legacy_session_results(origin,selected,cutoff):
    history={'state':'unknown','known_results':0,'history_complete':False,
             'reason':'legacy_mutable_capture_jobs','unresolved':0}
    tables={r[0] for r in origin.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'memory_capture_jobs' not in tables:
        return {},history
    if not {'memory_views','memory_view_versions'}<=tables:
        history['unresolved']=1;return {},history
    required={'id','event_id','assertion_id','status','phase','updated_at','refreshed_views','error'}
    if not required<={r[1] for r in origin.execute('PRAGMA table_info(memory_capture_jobs)')}:
        history['unresolved']=1;return {},history
    _,owners,events=session_origins(origin,cutoff,include_events=True)
    pins={}
    for row in origin.execute('SELECT * FROM memory_capture_jobs ORDER BY updated_at,id'):
        if row['phase']!='done' or row['status'] not in ('completed','waiting_model'):
            continue
        # The event is primary; assertion fallback is legal only through the
        # same validated canonical extraction ownership used by node sessions.
        event,assertion=row['event_id'],row['assertion_id']
        owner=events.get(event) if event and not assertion else owners.get(assertion) if assertion and not event else None
        if owner!=selected:
            if owner is None:history['unresolved']+=1
            continue
        try:
            completed=timestamp(row['updated_at'])
            if completed>cutoff:continue
            if row['error'] or not isinstance(row['id'],str) or not row['id'].strip():raise ValueError('Invalid completed job')
            outputs=json.loads(row['refreshed_views'])
            if not isinstance(outputs,dict):raise ValueError('Invalid output manifest')
        except (APIError,ValueError,TypeError):
            history['unresolved']+=1;continue
        for view,version_id in outputs.items():
            try:
                if not isinstance(view,str) or not view.strip() or type(version_id) is not int or version_id<=0:raise ValueError('Invalid version reference')
                version=origin.execute('SELECT * FROM memory_view_versions WHERE id=? AND view_id=?',(version_id,view)).fetchone()
                exists=origin.execute('SELECT 1 FROM memory_views WHERE id=?',(view,)).fetchone()
                if not exists or version is None:raise ValueError('Missing exact version')
                snapshot=json.loads(version['snapshot'])
                created=version['created_at'] if 'created_at' in version.keys() else snapshot.get('created_at')
                if not created or timestamp(created)>cutoff or timestamp(created)>completed:raise ValueError('Version outside receipt cutoff')
                if not isinstance(snapshot,dict) or not isinstance(snapshot.get('summary'),str) or not isinstance(snapshot.get('evidence'),list):raise ValueError('Missing material')
                if any(not isinstance(item,dict) or not isinstance(item.get('id',item.get('assertion_id')),str) or not item.get('id',item.get('assertion_id')).strip() for item in snapshot['evidence']):raise ValueError('Invalid actual membership')
                pins[view]={'version_id':version_id,'session_id':selected,'receipt_id':'legacy-capture-job:'+row['id']}
            except (APIError,ValueError,TypeError,KeyError):
                history['unresolved']+=1
                if isinstance(view,str):pins.pop(view,None)
    history['known_results']=len(pins)
    history['state']='partial' if pins else 'unknown'
    return pins,history
