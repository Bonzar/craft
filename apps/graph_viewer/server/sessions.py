"""Viewer-owned immutable session catalog and independent visibility mask."""
import json
from .common import APIError,bounded,cursor_decode,cursor_encode,digest
from .providers.session_origins import session_origins

SESSION_EXCLUDED=4
POLICY='source-session-origin-v1'


def capture_sessions(origin,disk,cutoff):
    from .providers.canonical_thoughts import is_canonical
    canonical = is_canonical(origin)
    sessions,owners=({}, {}) if canonical else session_origins(origin,cutoff)
    disk.executescript('CREATE TABLE IF NOT EXISTS session_catalog(id TEXT PRIMARY KEY,body TEXT); CREATE TABLE IF NOT EXISTS node_sessions(node INTEGER,session_id TEXT,PRIMARY KEY(node,session_id)); DELETE FROM session_catalog; DELETE FROM node_sessions;')
    if canonical:
        for row in disk.execute('SELECT ordinal,id,body FROM nodes'):
            body=json.loads(row['body']); meta=body['metadata']['canonical_thought']
            for session in meta['session_ids']:
                key='logical:'+session
                sessions.setdefault(key,{'id':key,'display_title':None,'provenance_kind':'logical',
                    'first_recorded_at':None,'last_recorded_at':None,'assertion_count':0})
                disk.execute('INSERT INTO node_sessions VALUES(?,?)',(row['ordinal'],key))
    else:
        disk.executemany('INSERT INTO node_sessions VALUES(?,?)',((row['ordinal'],owners[row['id']]) for row in disk.execute('SELECT ordinal,id FROM nodes') if row['id'] in owners))
    disk.executemany('INSERT INTO session_catalog VALUES(?,?)',((key,json.dumps(value,ensure_ascii=False)) for key,value in sessions.items()))


def apply_session_filter(disk,topics,selection):
    selected=selection.get('session_id')
    if selected is not None and disk.execute('SELECT 1 FROM session_catalog WHERE id=?',(selected,)).fetchone() is None:
        raise APIError('session_unavailable','This source session is unavailable in the selected snapshot',409)
    base='1' if selection['history'] else 'n.flags&3=0'
    unknown=disk.execute('SELECT count(*) FROM nodes n LEFT JOIN node_sessions s ON s.node=n.ordinal WHERE s.node IS NULL AND '+base).fetchone()[0]
    counts=dict(disk.execute('SELECT s.session_id,count(*) FROM node_sessions s JOIN nodes n ON n.ordinal=s.node WHERE '+base+' GROUP BY s.session_id'))
    for row in disk.execute('SELECT id,body FROM session_catalog').fetchall():
        body=json.loads(row['body']);body['assertion_count']=counts.get(row['id'],0)
        disk.execute('UPDATE session_catalog SET body=? WHERE id=?',(json.dumps(body,ensure_ascii=False),row['id']))
    disk.execute('UPDATE nodes SET flags=flags&~4')
    if selected is not None:
        first=disk.execute('SELECT body FROM nodes LIMIT 1').fetchone()
        canonical=first is not None and 'canonical_thought' in json.loads(first[0])['metadata']
        if canonical:
            # Keep exact derivation context even when a basis originated elsewhere.
            disk.execute("WITH RECURSIVE selected(node) AS (SELECT node FROM node_sessions WHERE session_id=? UNION SELECT e.t FROM edges e JOIN selected s ON e.f=s.node WHERE e.kind='thought_basis') UPDATE nodes SET flags=flags|4 WHERE ordinal NOT IN (SELECT node FROM selected)",(selected,))
        else:
            disk.execute('UPDATE nodes SET flags=flags|4 WHERE ordinal NOT IN (SELECT node FROM node_sessions WHERE session_id=?)',(selected,))
    mask=4 if selection['history'] else 7
    sliced=dict(disk.execute('SELECT m.topic,count(*) FROM memberships m JOIN nodes n ON n.ordinal=m.node WHERE n.flags&?=0 GROUP BY m.topic',(mask,)))
    for topic in topics['topics']:
        topic['slice_member_count']=sliced.get(topic['index'],0)
        topic['session_filter_applied']=selected is not None
    return {'session_id':selected,'unknown_origin_assertion_count':unknown,'policy':POLICY, **({'semantic_search_available':False} if selected is not None else {})}


def session_page(graph,params):
    query=params.get('query','').strip()
    if len(query)>300:raise APIError('invalid_query','Session search is too long')
    limit=bounded(params.get('limit'),50,100,1)
    binding=f'sessions:{graph.revision}:{digest(query)}:{limit}'
    offset=cursor_decode(params.get('cursor'),binding)
    with graph.connect() as c:
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='session_catalog'").fetchone() is None:
            raise APIError('session_catalog_unavailable','Reload the current graph to obtain its session catalog',409)
        items=[json.loads(row[0]) for row in c.execute('SELECT body FROM session_catalog')]
    needle=query.casefold()
    items=[item for item in items if needle in ' '.join(str(item.get(key) or '') for key in ('id','display_title','first_recorded_at','last_recorded_at')).casefold()]
    items.sort(key=lambda item:(item['last_recorded_at'] or '',item['id']),reverse=True)
    return {'revision':graph.revision,'source_revision':graph.manifest['source_revision'],'provider_id':graph.manifest['provider_id'],
        'items':items[offset:offset+limit],'next_cursor':cursor_encode(binding,offset+limit) if offset+limit<len(items) else None,
        'unknown_origin_assertion_count':graph.manifest.get('session_filter',{}).get('unknown_origin_assertion_count',0)}
