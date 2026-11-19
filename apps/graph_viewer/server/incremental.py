"""Append-only publishing: clone cached pages, read new source rows, patch buffers."""
import ctypes
import json
import os
import shutil
import struct
import sys
import time
import uuid
from pathlib import Path
from .providers.base import SOURCE_MEMBERSHIP_POLICY,membership_checked
from .common import now,timestamp
from .declared import declared_kind
from .projection import PreparedGraph,insert_node,open_disk,copy_sources
from .status import apply_status,effective_boundaries,next_effective_at
from .temporal import eligible,available_at
from .layout import append_bins,append_bounds,VERSION
from .transport import compress_binary
from .live_profile import mark
from .presentation_refresh import PRESENTATION_POLICY,policy_current,semantic_marks,refresh_labels,inherit_semantic
from .providers.assertion_memory_topics import TOPIC_PROJECTION_POLICY, topic_policy_current


def clone_file(source,target):
    # APFS clonefile is constant-time copy-on-write; old revisions stay immutable.
    if sys.platform=='darwin':
        library=ctypes.CDLL(None,use_errno=True)
        if library.clonefile(os.fsencode(source),os.fsencode(target),0)==0:
            return
    elif sys.platform.startswith('linux'):
        try:
            import fcntl
            with open(source,'rb') as src,open(target,'wb') as dst:
                fcntl.ioctl(dst,0x40049409,src.fileno())
            return
        except OSError:
            pass
    shutil.copyfile(source,target)


def watermarks(c):
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    marks={name:row_watermark(c,name) for name in ['memory_assertions','memory_edges']}
    marks['state']=c.execute('SELECT version FROM memory_state LIMIT 1').fetchone()[0]
    marks['raw']=c.execute('SELECT coalesce(max(rowid),0) FROM memory_raw_events').fetchone()[0] if 'memory_raw_events' in tables else 0
    views=[]
    for name in ['memory_views','memory_view_checks','memory_view_versions','memory_question_route_versions','memory_historical_checks']:
        if name in tables:
            # Definitions/current IDs can update in-place without a source-version bump.
            if name=='memory_views':
                views.append([name,[tuple(r) for r in c.execute('SELECT * FROM memory_views ORDER BY id')]])
            else:
                views.append([name,row_watermark(c,name)])
    marks['topics']=json.dumps(views,sort_keys=True)
    marks['presentation']=c.execute('SELECT coalesce(max(id),0) FROM memory_view_names').fetchone()[0] if 'memory_view_names' in tables else 0
    return marks


def row_watermark(c,table):
    # Keep MAX's rowid seek and COUNT's B-tree page-count fast paths separate.
    # The caller already pins one read transaction: these scalars share its snapshot.
    maximum=c.execute(f'SELECT coalesce(max(rowid),0) FROM {table}').fetchone()[0]
    count=c.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
    return maximum,count


def append_compatible(old,new):
    if old.get('assertion_membership') is None or old['assertion_membership']!=new.get('assertion_membership'):
        return False
    delta=sum(new[t][1]-old[t][1] for t in ['memory_assertions','memory_edges'])
    return delta>0 and delta<=2000 and new['state']-old['state']==delta and new['topics']==old['topics'] and all(new[t][0]>=old[t][0] and new[t][1]>=old[t][1] for t in ['memory_assertions','memory_edges'])


def semantic_component(c,seeds):
    nodes=set(seeds); pending=list(seeds)
    while pending:
        node=pending.pop()
        for row in c.execute("SELECT f,t FROM edges WHERE (f=? OR t=?) AND kind IN ('supersedes','contradicts','exception_to')",(node,node)):
            for endpoint in row:
                if endpoint not in nodes:
                    nodes.add(endpoint); pending.append(endpoint)
    return nodes


def load_states(c,ordinals):
    states={}
    for ordinal in ordinals:
        row=c.execute('SELECT kind,scope,valid_at,body FROM nodes WHERE ordinal=?',(ordinal,)).fetchone()
        cancel=json.loads(row['body'])['metadata'].get('viewer_status') or {}
        states[ordinal]={'kind':row['kind'],'scope':row['scope'],'valid_at':row['valid_at'],
                         'cancelled':cancel.get('published') is True and cancel.get('disposition')=='cancelled'}
    return states


def copy_append(origin,c,old,selection,captured,inactive_ids=frozenset()):
    states,ids={},{}; added_nodes=[]; added_edges=[]; affected=set()
    counters={'undated_known_count':0,'excluded_unknown_valid_count':0,'future_valid_known_count':0}
    node_count=c.execute('SELECT coalesce(max(ordinal),-1)+1 FROM nodes').fetchone()[0]
    edge_count=c.execute('SELECT coalesce(max(ordinal),-1)+1 FROM edges').fetchone()[0]
    for row in origin.execute('SELECT * FROM memory_assertions WHERE rowid>? ORDER BY rowid',(old['memory_assertions'][0],)):
        if row['assertion_id'] in inactive_ids:
            continue
        meta=json.loads(row['metadata'])
        update_temporal_counters(counters,row,meta,selection,captured)
        if eligible(row,meta,selection,captured):
            insert_node(c,row,node_count,states,ids,meta); added_nodes.append(node_count); affected.add(node_count); node_count+=1
    cutoff=selection['at'] if selection['mode']=='known_then' else captured
    for row in origin.execute('SELECT * FROM memory_edges WHERE rowid>? ORDER BY rowid',(old['memory_edges'][0],)):
        if timestamp(row['recorded_at'])>cutoff:
            continue
        endpoints=[c.execute('SELECT ordinal FROM nodes WHERE id=?',(row[key],)).fetchone() for key in ['from_id','to_id']]
        if any(value is None for value in endpoints):
            continue
        f,t=[value[0] for value in endpoints]
        c.execute('INSERT INTO edges VALUES(?,?,?,?,?,?,?)',(edge_count,row['edge_id'],f,t,row['kind'],row['recorded_at'],row['metadata']))
        added_edges.append(edge_count); affected.update((f,t)); edge_count+=1
    copy_sources(origin,c,cutoff,min_rowid=old['raw'])
    return added_nodes,added_edges,affected,counters


def patch_binary(c,directory,old_dir,added_nodes,added_edges,affected):
    for name in ['nodes.bin','edges.bin']:
        clone_file(old_dir/name,directory/name)
    with (directory/'nodes.bin').open('r+b') as file:
        for ordinal in sorted(affected):
            row=c.execute('SELECT x,y,z,flags FROM nodes WHERE ordinal=?',(ordinal,)).fetchone()
            file.seek(ordinal*16); file.write(struct.pack('<fffI',*row))
    with (directory/'edges.bin').open('ab') as file:
        for ordinal in added_edges:
            row=c.execute('SELECT f,t FROM edges WHERE ordinal=?',(ordinal,)).fetchone()
            file.write(struct.pack('<II',*row))


def affected_visible(c,ordinals,history):
    c.execute('CREATE TEMP TABLE IF NOT EXISTS changed_nodes(ordinal INTEGER PRIMARY KEY)')
    c.execute('DELETE FROM changed_nodes')
    c.executemany('INSERT INTO changed_nodes VALUES(?)',((n,) for n in ordinals))
    node_filter='' if history else ' WHERE flags&3=0'
    nodes=c.execute('SELECT count(*) FROM nodes WHERE ordinal IN changed_nodes'+('' if history else ' AND flags&3=0')).fetchone()[0]
    edge_filter='' if history else ' AND a.flags&3=0 AND b.flags&3=0'
    edges=c.execute('SELECT count(*) FROM edges e JOIN nodes a ON a.ordinal=e.f JOIN nodes b ON b.ordinal=e.t WHERE (e.f IN changed_nodes OR e.t IN changed_nodes)'+edge_filter).fetchone()[0]
    return nodes,edges


def delta_payload(c,added_nodes,added_edges,affected):
    nodes=[]; edges=[]; statuses=[]
    for ordinal in added_nodes:
        row=c.execute('SELECT * FROM nodes WHERE ordinal=?',(ordinal,)).fetchone()
        nodes.append({'id':row['id'],'ordinal':ordinal,'position':[row['x'],row['y'],row['z']],'flags':row['flags']})
    for ordinal in added_edges:
        row=c.execute('SELECT * FROM edges WHERE ordinal=?',(ordinal,)).fetchone()
        edges.append({'id':row['id'],'ordinal':ordinal,'from':row['f'],'to':row['t']})
    for ordinal in affected:
        row=c.execute('SELECT * FROM nodes WHERE ordinal=?',(ordinal,)).fetchone()
        replacements=[r[0] for r in c.execute('SELECT n.id FROM status_links s JOIN nodes n ON n.ordinal=s.f WHERE s.t=?',(ordinal,))]
        statuses.append({'id':row['id'],'ordinal':ordinal,'flags':row['flags'],
                         'status':{'disposition':row['disposition'],'unresolved_conflict':bool(row['conflict']),'replacements':replacements,'basis_edge_ids':[],'effective_at':None,'effectiveness':'unknown'}})
    return {'upsert_nodes':nodes,'upsert_edges':edges,'remove_node_ordinals':[],'remove_edge_ordinals':[],'status_changes':statuses,'topic_changes':[]}


def presentation_incremental(origin,source,old,cache,selection,captured,source_revision,marks):
    before=old.watermarks
    previous=before.get('presentation')
    if previous is None:
        legacy=next((row[1] for row in json.loads(before['topics']) if row[0]=='memory_view_names'),[0,0])
        previous=legacy[0]
    migration=not policy_current(old.manifest)
    if not migration and marks['presentation']==previous:return None,{'no_graph_change':True,'observed_source_revision':source_revision}
    keys=None;pending=False
    if not migration:
        rows=origin.execute('SELECT id,view_id FROM memory_view_names WHERE id>? ORDER BY id LIMIT 64',(previous,)).fetchall()
        if not rows:return None,{'no_graph_change':True,'observed_source_revision':source_revision}
        keys={row['view_id'] for row in rows};published=rows[-1]['id'];pending=published<marks['presentation'];marks=dict(marks,presentation=published)
    revision=uuid.uuid4().hex;directory=Path(cache)/revision;directory.mkdir(mode=0o700)
    for file in old.directory.iterdir():
        if file.is_file() and file.name not in ('manifest.json','watermarks.json','topics.json') and not file.name.endswith('.tmp'):
            clone_file(file,directory/file.name);(directory/file.name).chmod(0o600)
    topics=old.topics();changed=refresh_labels(origin,topics,selection,captured,marks['presentation'],keys)
    topics['revision']=revision;(directory/'topics.json').write_text(json.dumps(topics,ensure_ascii=False))
    manifest=json.loads(json.dumps(old.manifest));manifest.update(revision=revision,captured_at=captured,source_revision=source_revision)
    for name in ('nodes','edges'):manifest[name]['url']=f'/api/v1/graph/{revision}/{name}.bin'
    manifest['topics_url']=f'/api/v1/graph/{revision}/topics'
    manifest['preparation'].update(presentation_policy=PRESENTATION_POLICY,presentation_only=True,presentation_pending=pending,incremental=True)
    graph=persist(directory,manifest,marks);inherit_semantic(graph,old)
    return graph,{'upsert_nodes':[],'upsert_edges':[],'remove_node_ordinals':[],'remove_edge_ordinals':[],
                  'status_changes':[],'topic_changes':changed}

def incremental(source,old,cache,selection):
    # Rebuild filtered snapshots against the full authoritative status graph.
    if selection.get('session_id') is not None:
        return None
    from .sessions import capture_sessions,apply_session_filter
    before=getattr(old,'watermarks',None)
    if not topic_policy_current(old.manifest) or not membership_checked(old.manifest,before) or old.manifest.get('layout',{}).get('version')!=VERSION:
        return None
    captured=now(); start=time.monotonic(); source_revision=source.revision(); phases={}; phase_started=start
    with source.connect() as origin:
        origin.execute('BEGIN'); origin.execute('SELECT version FROM memory_state LIMIT 1').fetchone()
        cutoff=selection['at'] if selection['mode']=='known_then' else captured
        inactive_ids,token=source.assertion_membership(origin,cutoff)
        marks=watermarks(origin); marks['assertion_membership']=token
        clock_due=selection['mode']=='current' and old.manifest.get('next_status_at') and old.manifest['next_status_at']<=captured
        if not clock_due and semantic_marks(before)==semantic_marks(marks):
            return presentation_incremental(origin,source,old,cache,selection,captured,source_revision,marks)
        compatible_before=dict(before,topics=json.dumps([row for row in json.loads(before['topics']) if row[0]!='memory_view_names'],sort_keys=True))
        if not append_compatible(compatible_before,marks) and not (clock_due and marks==before):
            return None
        phase_started=mark(phases,'source_snapshot_setup_ms',phase_started)
        revision=uuid.uuid4().hex; directory=Path(cache)/revision; directory.mkdir(mode=0o700)
        clone_file(old.path,directory/'projection.sqlite3'); c=open_disk(directory/'projection.sqlite3'); phase_started=mark(phases,'clone_ms',phase_started)
        added_nodes,added_edges,seeds,counters=copy_append(origin,c,before,selection,captured,inactive_ids)
        topics=old.topics();label_changes=[];presentation_pending=False
        if not policy_current(old.manifest) or before.get('presentation')!=marks['presentation']:
            keys=None
            if policy_current(old.manifest):
                rows=origin.execute('SELECT id,view_id FROM memory_view_names WHERE id>? ORDER BY id LIMIT 64',(before.get('presentation',0),)).fetchall()
                keys={row['view_id'] for row in rows}
                if rows:presentation_pending=rows[-1]['id']<marks['presentation'];marks=dict(marks,presentation=rows[-1]['id'])
            label_changes=refresh_labels(origin,topics,selection,captured,marks['presentation'],keys)
        capture_sessions(origin,c,cutoff)
        origin.commit(); phase_started=mark(phases,'new_rows_ms',phase_started)
    if selection['mode']=='current':
        seeds.update(n for _,f,t in effective_boundaries(c,old.manifest['status_at'],captured) for n in (f,t))
    if not append_bins(c,added_nodes,added_edges):
        # Missing or corrupt derived placement metadata requires a fresh cache.
        # Source bridges themselves keep existing physical positions unchanged.
        c.close();shutil.rmtree(directory)
        return None
    affected=semantic_component(c,seeds)
    # Before counts include new rows: subtract their visible contribution separately.
    initial=affected_visible(c,affected,selection['history'])
    initial_new_edges=sum(1 for ordinal in added_edges if edge_visible(c,ordinal,selection['history']))
    apply_status(c,load_states(c,affected),selection['at'] or captured,subset=affected)
    final=affected_visible(c,affected,selection['history'])
    session_filter=apply_session_filter(c,topics,selection)
    payload=delta_payload(c,added_nodes,added_edges,affected);payload['topic_changes']=label_changes; phase_started=mark(phases,'affected_status_ms',phase_started)
    patch_binary(c,directory,old.directory,added_nodes,added_edges,affected); phase_started=mark(phases,'binary_patch_ms',phase_started)
    compress_binary(directory); phase_started=mark(phases,'gzip_ms',phase_started)
    counts,actual_bounds,next_status,exact_bounds=finish_counts(c,old,selection,captured,counters,added_nodes,added_edges,initial,final,initial_new_edges)
    phase_started=mark(phases,'counts_bounds_ms',phase_started)
    c.commit(); c.close()
    topics['revision']=revision
    for topic in topics['topics']:
        topic['freshness']='stale'
    (directory/'topics.json').write_text(json.dumps(topics,ensure_ascii=False))
    manifest=updated_manifest(old,revision,captured,source_revision,counts,time.monotonic()-start)
    manifest['session_filter']=session_filter
    manifest['preparation']['presentation_pending']=presentation_pending
    manifest['next_status_at']=next_status
    manifest['bounds']=actual_bounds
    manifest['layout']['raw_bounds']=exact_bounds
    graph=persist(directory,manifest,marks); mark(phases,'metadata_commit_ms',phase_started)
    graph.manifest['preparation']['phases_ms']=phases
    return graph,payload


def edge_visible(c,ordinal,history):
    row=c.execute('SELECT a.flags,b.flags FROM edges e JOIN nodes a ON a.ordinal=e.f JOIN nodes b ON b.ordinal=e.t WHERE e.ordinal=?',(ordinal,)).fetchone()
    return history or not ((row[0]|row[1])&3)


def updated_manifest(old,revision,captured,source_revision,counts,elapsed):
    manifest=json.loads(json.dumps(old.manifest)); manifest.update(revision=revision,captured_at=captured,source_revision=source_revision,counts=counts)
    for name in ['nodes','edges']:
        manifest[name]['url']=f'/api/v1/graph/{revision}/{name}.bin'
        manifest[name]['byte_length']=counts[name]*manifest[name]['stride']
    manifest['topics_url']=f'/api/v1/graph/{revision}/topics'
    manifest['preparation']={'presentation_policy':PRESENTATION_POLICY,'topic_projection_policy':TOPIC_PROJECTION_POLICY,'source_membership_policy':SOURCE_MEMBERSHIP_POLICY,'incremental':True,'total_seconds':elapsed,'search_index_ready':True}
    manifest['knowledge_cutoff']=captured
    if manifest['selector']['mode']=='current':
        manifest['status_at']=captured
    return manifest


def update_temporal_counters(counters,row,metadata,selection,captured):
    cutoff=selection['at'] if selection['mode']=='known_then' else captured
    if available_at(row,metadata)>cutoff:
        return
    if row['valid_at'] is None:
        counters['undated_known_count']+=1
        if selection['mode']=='known_now' and selection['undated']=='separate':
            counters['excluded_unknown_valid_count']+=1
    elif timestamp(row['valid_at'])>(selection['at'] or captured):
        counters['future_valid_known_count']+=1


def persist(directory,manifest,marks):
    (directory/'manifest.json').write_text(json.dumps(manifest))
    (directory/'watermarks.json').write_text(json.dumps(marks))
    for file in directory.iterdir():
        if file.is_file():
            file.chmod(0o600)
    graph=PreparedGraph(directory,manifest); graph.watermarks=marks
    return graph


def finish_counts(c,old,selection,captured,counters,added_nodes,added_edges,initial,final,initial_new_edges):
    counts={key:value+counters.get(key,0) for key,value in old.manifest['counts'].items()}; counts['nodes']+=len(added_nodes); counts['edges']+=len(added_edges)
    counts['eligible_before_status_filter']=counts['nodes']
    if selection['mode']=='current':
        c.execute('CREATE INDEX IF NOT EXISTS node_valid_julian ON nodes(julianday(valid_at))')
        counts['future_valid_known_count']=c.execute('SELECT count(*) FROM nodes WHERE julianday(valid_at)>julianday(?)',(captured,)).fetchone()[0]
    counts['visible_nodes']+=final[0]-initial[0]+len(added_nodes)
    counts['visible_edges']+=final[1]-initial[1]+initial_new_edges
    actual_bounds,exact_bounds=append_bounds(c,old,added_nodes)
    next_status=next_effective_at(c,captured) if selection['mode']=='current' else None
    return counts,actual_bounds,next_status,exact_bounds
