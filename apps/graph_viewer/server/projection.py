"""Disk-backed immutable snapshots; only O(nodes) state is held in Python."""
import json
import sqlite3
import struct
import time
import uuid
from pathlib import Path
from .providers.base import SOURCE_MEMBERSHIP_POLICY
from .declared import declared_kind
from .common import now, timestamp, APIError
from .temporal import eligible, available_at
from .status import apply_status,next_effective_at
from .layout import pack,raw_bounds,padded_bounds,refresh_topic_geometry,VERSION
from .transport import compress_binary
from .providers.assertion_memory_topics import publish_topics, position, TOPIC_PROJECTION_POLICY

from .presentation_refresh import PRESENTATION_POLICY
from .sessions import capture_sessions,apply_session_filter,POLICY as SESSION_POLICY
from .search import node_search_text

_INFER_KIND = object()

SCHEMA='''
PRAGMA journal_mode=DELETE;
CREATE TABLE nodes(ordinal INTEGER PRIMARY KEY,id TEXT UNIQUE,body TEXT,text TEXT,kind TEXT,scope TEXT,valid_at TEXT,flags INTEGER DEFAULT 0,disposition TEXT,conflict INTEGER DEFAULT 0,x REAL,y REAL,z REAL);
CREATE TABLE edges(ordinal INTEGER PRIMARY KEY,id TEXT UNIQUE,f INTEGER,t INTEGER,kind TEXT,recorded_at TEXT,metadata TEXT);
CREATE TABLE status_links(id TEXT PRIMARY KEY,f INTEGER,t INTEGER,meaning TEXT);
CREATE TABLE memberships(topic INTEGER,node INTEGER,PRIMARY KEY(topic,node));
CREATE TABLE overviews(topic INTEGER PRIMARY KEY,id TEXT UNIQUE,text TEXT,text_revision TEXT);
CREATE TABLE sources(id TEXT PRIMARY KEY,body TEXT,session_id TEXT,position INTEGER);
CREATE VIRTUAL TABLE search USING fts5(text);
'''


def open_disk(path):
    c=sqlite3.connect(path,timeout=10)
    c.row_factory=sqlite3.Row
    return c


class PreparedGraph:
    def __init__(self, directory, manifest):
        self.directory=Path(directory)
        self.manifest=manifest
        self.revision=manifest['revision']
        self.path=self.directory/'projection.sqlite3'
    def connect(self):
        return open_disk(self.path)
    def topics(self):
        return json.loads((self.directory/'topics.json').read_text())


def insert_node(c, row, ordinal, states, ids, meta=None, *, kind_override=_INFER_KIND):
    meta=json.loads(row['metadata']) if meta is None else meta; body=dict(row); body['metadata']=meta
    kind=declared_kind(row['text'],meta) if kind_override is _INFER_KIND else kind_override
    cancel=meta.get('viewer_status') or {}
    states[ordinal]={'kind':kind,'scope':row['scope'],'valid_at':row['valid_at'],
                     'cancelled':cancel.get('published') is True and cancel.get('disposition')=='cancelled'}
    ids[row['assertion_id']]=ordinal
    xyz=position(row['assertion_id'],1000)
    c.execute('INSERT INTO nodes(ordinal,id,body,text,kind,scope,valid_at,x,y,z) VALUES(?,?,?,?,?,?,?,?,?,?)',
              (ordinal,row['assertion_id'],json.dumps(body,ensure_ascii=False),row['text'],kind,row['scope'],row['valid_at'],*xyz))
    c.execute('INSERT INTO search(rowid,text) VALUES(?,?)',(ordinal,node_search_text(row['text'],meta)))


def copy_nodes(source, c, selection, captured, *, source_only_kinds=False, inactive_ids=frozenset()):
    states,ids={},{}
    counts={'undated_known_count':0,'excluded_unknown_valid_count':0,'future_valid_known_count':0}
    for row in source.execute('SELECT * FROM memory_assertions ORDER BY rowid'):
        if row['assertion_id'] in inactive_ids:
            continue
        meta=json.loads(row['metadata'])
        cutoff=selection['at'] if selection['mode']=='known_then' else captured
        known = available_at(row,meta)
        if known is not None and known<=cutoff:
            if row['valid_at'] is None:
                counts['undated_known_count']+=1
            elif timestamp(row['valid_at'])>(selection['at'] or captured):
                counts['future_valid_known_count']+=1
        if not eligible(row,meta,selection,captured):
            continue
        insert_node(c,row,len(ids),states,ids,meta,kind_override=meta.get('kind') if source_only_kinds else _INFER_KIND)
    if selection['mode']=='known_now' and selection['undated']=='separate':
        counts['excluded_unknown_valid_count']=counts['undated_known_count']
    return states,ids,counts


def copy_edges(source,c,ids,cutoff,*,allow_unknown=True):
    ordinal=0
    for row in source.execute('SELECT rowid,* FROM memory_edges ORDER BY rowid'):
        recorded = row['recorded_at']
        if row['from_id'] not in ids or row['to_id'] not in ids:
            continue
        if recorded is None and not allow_unknown or recorded is not None and timestamp(recorded)>cutoff:
            continue
        c.execute('INSERT INTO edges VALUES(?,?,?,?,?,?,?)',(ordinal,row['edge_id'],ids[row['from_id']],ids[row['to_id']],row['kind'],row['recorded_at'],row['metadata']))
        ordinal+=1
    return ordinal


def copy_sources(source,c,cutoff,min_rowid=0):
    tables={r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'memory_raw_events' not in tables:
        return
    has_positions='memory_event_positions' in tables
    sql='SELECT e.*,p.position FROM memory_raw_events e LEFT JOIN memory_event_positions p ON p.event_id=e.id WHERE e.rowid>?' if has_positions else 'SELECT *,NULL AS position FROM memory_raw_events WHERE rowid>?'

    for row in source.execute(sql,(min_rowid,)):
        body=dict(row); meta=json.loads(body.get('metadata','{}'))
        if timestamp(body['recorded_at'])>cutoff or (meta.get('source_available_at') and timestamp(meta['source_available_at'])>cutoff):
            continue
        c.execute('INSERT INTO sources VALUES(?,?,?,?)',(row['id'],json.dumps(body,ensure_ascii=False),row['session_id'],row['position']))


def write_binary(c,directory,history):
    with (directory/'nodes.bin').open('wb') as file:
        for row in c.execute('SELECT x,y,z,flags FROM nodes ORDER BY ordinal'):
            file.write(struct.pack('<fffI',*row))
    with (directory/'edges.bin').open('wb') as file:
        for row in c.execute('SELECT f,t FROM edges ORDER BY ordinal'):
            file.write(struct.pack('<II',*row))
    nodes=c.execute('SELECT count(*) FROM nodes').fetchone()[0]
    edges=c.execute('SELECT count(*) FROM edges').fetchone()[0]
    mask=4 if history else 7
    visible=c.execute('SELECT count(*) FROM nodes WHERE flags&?=0',(mask,)).fetchone()[0]
    visible_edges=c.execute('SELECT count(*) FROM edges e JOIN nodes a ON a.ordinal=e.f JOIN nodes b ON b.ordinal=e.t WHERE a.flags&?=0 AND b.flags&?=0',(mask,mask)).fetchone()[0]
    return {'nodes':nodes,'edges':edges,'visible_nodes':visible,'visible_edges':visible_edges,'eligible_before_status_filter':c.execute('SELECT count(*) FROM nodes WHERE flags&4=0').fetchone()[0]}


def copy_snapshot(source, c, selection, captured):
    source_revision=source.revision()
    cutoff=selection['at'] if selection['mode']=='known_then' else captured
    with source.connect() as origin:
        origin.execute('BEGIN'); origin.execute('SELECT version FROM memory_state LIMIT 1').fetchone()
        ir_info=None
        if origin.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='viewer_source_info'").fetchone():
            ir_info=origin.execute('SELECT source_revision,kind_policy FROM viewer_source_info LIMIT 1').fetchone()
            if ir_info is not None:
                source_revision=ir_info['source_revision']
        from .incremental import watermarks
        inactive_ids, token=source.assertion_membership(origin,cutoff)
        marks=watermarks(origin); marks['assertion_membership']=token
        states,ids,counts=copy_nodes(origin,c,selection,captured,source_only_kinds=ir_info is not None and ir_info['kind_policy']=='source_only',inactive_ids=inactive_ids)
        copy_edges(origin,c,ids,cutoff,allow_unknown=selection['mode']!='known_then'); copy_sources(origin,c,cutoff)
        topics=publish_topics(origin,c,ids,selection,inactive_ids=inactive_ids,captured_at=captured)
        capture_sessions(origin,c,cutoff)
        from .providers.canonical_thoughts import is_canonical
        if selection.get('session_id') is not None and not is_canonical(origin):
            from .providers.causal_results import result_bundle
            from .providers.assertion_memory_topics import read_pinned_material
            try:
                bundle=result_bundle(origin,selection['session_id'],cutoff,run_id=selection.get('run_id'),snapshot_id=selection.get('output_snapshot_id'),use_core_reader=ir_info is None,read_material=lambda view,version:read_pinned_material(origin,view,version,cutoff))
            except (ValueError,KeyError,TypeError) as error:
                raise APIError('causal_history_unavailable','Recorded causal history could not be validated',409) from error
            pins,history=bundle['pins'],bundle['history']
            staged=sqlite3.connect(':memory:');staged.row_factory=sqlite3.Row
            try:
                staged.executescript('CREATE TABLE memberships(topic INTEGER,node INTEGER,PRIMARY KEY(topic,node)); CREATE TABLE overviews(topic INTEGER PRIMARY KEY,id TEXT UNIQUE,text TEXT,text_revision TEXT);')
                actual=publish_topics(origin,staged,ids,selection,inactive_ids=inactive_ids,captured_at=cutoff,version_pins=pins)
                topics['_session_results']={'topics':actual,'memberships':[tuple(row) for row in staged.execute('SELECT * FROM memberships')],'overviews':[tuple(row) for row in staged.execute('SELECT * FROM overviews')],'history':history,'bundle':bundle}
            finally:staged.close()
        origin.commit()
    return source_revision,cutoff,marks,states,ids,counts,topics


def prepare(source, cache, selection):
    start=time.monotonic(); captured=now(); revision=uuid.uuid4().hex
    Path(cache).mkdir(parents=True,exist_ok=True,mode=0o700); Path(cache).chmod(0o700)
    directory=Path(cache)/revision; directory.mkdir(mode=0o700)
    c=open_disk(directory/'projection.sqlite3'); c.executescript(SCHEMA)
    source_revision,cutoff,marks,states,ids,counts,topics=copy_snapshot(source,c,selection,captured)
    source_read=time.monotonic()-start
    status_at=selection['at'] or captured
    apply_status(c,states,status_at); del states,ids
    pending=topics.pop('_session_results',None)
    extent=pack(c,topics)
    if pending is not None:
        family_topics,family_memberships=topics['topics'],topics['memberships']
        topics=pending['topics'];topics.update(layout_topics=family_topics,layout_memberships=family_memberships)
        c.execute('DELETE FROM memberships');c.execute('DELETE FROM overviews')
        c.executemany('INSERT INTO memberships VALUES(?,?)',pending['memberships'])
        c.executemany('INSERT INTO overviews VALUES(?,?,?,?)',pending['overviews'])
        refresh_topic_geometry(c,topics)
        c.execute('CREATE TABLE session_result_bundle(body TEXT NOT NULL)')
        c.execute('INSERT INTO session_result_bundle VALUES(?)',(json.dumps(pending['bundle'],ensure_ascii=False),))
    session_filter=apply_session_filter(c,topics,selection)
    if pending is not None:session_filter['overview_history']=pending['history']
    exact_bounds=raw_bounds(c); actual_bounds=padded_bounds(exact_bounds)
    c.executescript('CREATE INDEX node_valid_julian ON nodes(julianday(valid_at)); CREATE INDEX edge_kind ON edges(kind); CREATE INDEX edge_from ON edges(f,ordinal); CREATE INDEX edge_to ON edges(t,ordinal); CREATE INDEX membership_node ON memberships(node); CREATE INDEX source_context ON sources(session_id,position);')
    counts.update(write_binary(c,directory,selection['history'])); compress_binary(directory); counts['topics']=len(topics['topics'])
    next_status=next_effective_at(c,status_at) if selection['mode']=='current' else None
    c.commit(); c.close()
    topics['revision']=revision
    (directory/'topics.json').write_text(json.dumps(topics,ensure_ascii=False))
    base=f'/api/v1/graph/{revision}'
    manifest={'schema':1,'revision':revision,'source_revision':source_revision,'provider_id':source.provider_id,
              'captured_at':captured,'selector':selection,'session_filter':session_filter,'counts':counts,'nodes':{'url':base+'/nodes.bin','format':'gv-nodes-le-v1','stride':16,'byte_length':counts['nodes']*16},
              'edges':{'url':base+'/edges.bin','format':'gv-edges-le-v1','stride':8,'byte_length':counts['edges']*8},
              'topics_url':base+'/topics','bounds':actual_bounds,
              'layout':{'version':VERSION,'semantic':False,'stable':True,'extent':extent,'raw_bounds':exact_bounds},'complete_graph':True,
              'readiness':topics['unknowns'],'live_cursor':'0','status_at':status_at,'knowledge_cutoff':cutoff,'next_status_at':next_status,
              'preparation':{'session_policy':SESSION_POLICY,'session_result_policy':'core-run-provenance-pins-v2-request-context','causal_output_snapshot_id':pending['history']['snapshot_id'] if pending is not None else None,'presentation_policy':PRESENTATION_POLICY,'topic_projection_policy':TOPIC_PROJECTION_POLICY,'source_membership_policy':SOURCE_MEMBERSHIP_POLICY,'source_read_seconds':source_read,'total_seconds':time.monotonic()-start,'search_index_ready':True}}
    (directory/'manifest.json').write_text(json.dumps(manifest))
    (directory/'watermarks.json').write_text(json.dumps(marks))
    for file in directory.iterdir():
        if file.is_file(): file.chmod(0o600)
    graph=PreparedGraph(directory,manifest); graph.watermarks=marks
    return graph
