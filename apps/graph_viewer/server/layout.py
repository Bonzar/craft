"""Deterministic source membership geometry, separated by weak arc components.

Coordinates have no truth or semantic authority; the source supplies every label,
membership and parent edge. Ancestor umbrellas do not collapse their descendants.
"""
import math
import sqlite3
from .common import digest
from .providers.assertion_memory_topics import position

VERSION='source-component-bins-v3'
COMPONENT_GAP=60.
COMPONENT_MARGIN=30.


def volume_point(key,radius):
    unit=position(key,1)
    radial=(int(digest('radius:'+key)[:12],16)/0xffffffffffff)**(1/3)
    return [value*radius*radial for value in unit]


def topic_geometry(topics,count):
    extent=max(650,min(2400,70*max(1,count)**(1/3)))
    entries=topics['topics']; parents={t['index']:[] for t in entries}
    for link in topics['links']:
        if link['relation']=='subquestion':
            parents[link['child']].append(link['parent'])
    centers={}; ancestors={}; radii={}
    dominant=max(entries,key=lambda t:t['member_count'],default=None)
    for topic in sorted(entries,key=lambda t:(t['depth'],t['index'])):
        index=topic['index']; known=[p for p in parents[index] if p in centers]
        inherited=set(known)
        for parent in known:
            inherited.update(ancestors.get(parent,()))
        ancestors[index]=inherited
        base=[sum(centers[p][axis] for p in known)/len(known) for axis in range(3)] if known else [0,0,0]
        offset=position(topic['id'],extent*.6/(1+topic['depth']*.55))
        if topic is dominant and not known:
            offset=[0,0,0]
        centers[index]=[base[axis]+offset[axis] for axis in range(3)]
        radii[index]=max(extent*.12,extent*.65*(max(1,topic['member_count'])/max(1,count))**(1/3))
    return extent,centers,ancestors,radii


def pack(c,topics):
    count=c.execute('SELECT count(*) FROM nodes').fetchone()[0]
    extent,centers,ancestors,radii=topic_geometry(topics,count)
    membership_cursor=c.execute('SELECT node,group_concat(topic) AS topics FROM memberships GROUP BY node')
    memberships={row['node']:[int(value) for value in row['topics'].split(',')] for row in membership_cursor}
    for row in c.execute('SELECT ordinal,id FROM nodes ORDER BY ordinal'):
        members=memberships.get(row['ordinal'],[])
        active=[t for t in members if not any(t in ancestors[other] for other in members)]
        if active:
            center=[sum(centers[t][axis] for t in active)/len(active) for axis in range(3)]
            jitter=volume_point(row['id'],min(radii[t] for t in active))
            xyz=[center[axis]+jitter[axis] for axis in range(3)]
        else:
            xyz=volume_point(row['id'],extent*.8)
        c.execute('UPDATE nodes SET x=?,y=?,z=? WHERE ordinal=?',(*xyz,row['ordinal']))
    pack_components(c)
    refresh_topic_geometry(c,topics)
    return extent


def refresh_topic_geometry(c,topics):
    for topic in topics['topics']:
        row=c.execute('SELECT avg(x),avg(y),avg(z) FROM nodes n JOIN memberships m ON m.node=n.ordinal WHERE m.topic=?',(topic['index'],)).fetchone()
        if row[0] is not None:
            topic['position']=list(row)
            square=c.execute('SELECT max((x-?)*(x-?)+(y-?)*(y-?)+(z-?)*(z-?)) FROM nodes n JOIN memberships m ON m.node=n.ordinal WHERE m.topic=?',(*[value for value in row for _ in range(2)],topic['index'])).fetchone()[0]
            topic['radius']=max(30,math.sqrt(square))


def component_sets(nodes,edges):
    """Weak connectivity only: original directed/parallel/self arcs stay untouched."""
    parents={node:node for node in nodes};sizes={node:1 for node in nodes}
    def root(node):
        while node!=parents[node]:
            parents[node]=parents[parents[node]];node=parents[node]
        return node
    for f,t in edges:
        a,b=root(f),root(t)
        if a!=b:
            if sizes[a]<sizes[b]:a,b=b,a
            parents[b]=a;sizes[a]+=sizes[b]
    groups={}
    for node in nodes:groups.setdefault(root(node),[]).append(node)
    return list(groups.values())


def allocate_box(state,size):
    """Linear shelf packing, with only seven cached scalars needed by appends."""
    width,depth,height=[n+COMPONENT_GAP for n in size]
    state['volume']+=width*depth*height
    if state['x'] and state['x']+width>state['target']:
        state['x']=0.;state['y']+=state['row'];state['row']=0.
    if (state['y'] and state['y']+depth>state['target']) or max(width,depth)>state['target']:
        state['x']=0.;state['y']=0.;state['z']+=state['layer'];state['row']=0.;state['layer']=0.
        state['target']=max(state['target'],max(width,depth),state['volume']**(1/3)*1.4)
    low=[state['x'],state['y'],state['z']]
    state['x']+=width;state['row']=max(state['row'],depth);state['layer']=max(state['layer'],height)
    return low+[low[i]+size[i] for i in range(3)]


def save_packing_state(c,state):
    c.executemany('INSERT OR REPLACE INTO layout_packing VALUES(?,?)',state.items())


def pack_components(c):
    # Initial weak components seed physical bins. Later bridges do not merge bins.
    # Derived cache only; no extra source nodes, labels, memberships or arcs.
    c.executescript('CREATE TABLE IF NOT EXISTS layout_bins(id TEXT PRIMARY KEY,x0 REAL,y0 REAL,z0 REAL,x1 REAL,y1 REAL,z1 REAL); CREATE TABLE IF NOT EXISTS layout_bin_nodes(node INTEGER PRIMARY KEY,bin TEXT); CREATE TABLE IF NOT EXISTS layout_packing(key TEXT PRIMARY KEY,value REAL); DELETE FROM layout_bins; DELETE FROM layout_bin_nodes; DELETE FROM layout_packing;')
    nodes={r['ordinal']:(r['id'],list(r)[2:]) for r in c.execute('SELECT ordinal,id,x,y,z FROM nodes')}
    groups=component_sets(nodes,c.execute('SELECT f,t FROM edges'))
    entries=[];volume=0.;largest=0.
    for members in groups:
        low=[min(nodes[n][1][i] for n in members)-COMPONENT_MARGIN for i in range(3)]
        high=[max(nodes[n][1][i] for n in members)+COMPONENT_MARGIN for i in range(3)]
        size=[high[i]-low[i] for i in range(3)];key=min(nodes[n][0] for n in members)
        entries.append((key,members,low,size));volume+=math.prod(n+COMPONENT_GAP for n in size);largest=max(largest,*size)
    state={'x':0.,'y':0.,'z':0.,'row':0.,'layer':0.,'target':max(650.,largest+COMPONENT_GAP,volume**(1/3)*1.4),'volume':0.}
    for key,members,low,size in sorted(entries,key=lambda e:(-max(e[3]),e[0])):
        box=allocate_box(state,size)
        c.execute('INSERT INTO layout_bins VALUES(?,?,?,?,?,?,?)',(key,*box))
        c.executemany('INSERT INTO layout_bin_nodes VALUES(?,?)',((n,key) for n in members))
        c.executemany('UPDATE nodes SET x=?,y=?,z=? WHERE ordinal=?',((*[nodes[n][1][i]+box[i]-low[i] for i in range(3)],n) for n in members))
    save_packing_state(c,state)


def append_bins(c,added_nodes,added_edges):
    # Missing/corrupt derived layout metadata cannot make the source unavailable.
    # The caller discards the cloned projection when a rebuild is requested.
    try:
        return _append_bins(c,added_nodes,added_edges)
    except (sqlite3.DatabaseError,KeyError,TypeError,ValueError):
        return False


def _append_bins(c,added_nodes,added_edges):
    """Delta-local placement; bins never claim current graph connectivity.

    Existing stars stay fixed, including when an exact source bridge connects
    their old islands. Only connected fragments of newly added stars choose a
    real source-neighbor bin (or a new bin if there is no old neighbor).
    """
    c.execute('SELECT node,bin FROM layout_bin_nodes LIMIT 0')
    c.execute('SELECT id,x0,y0,z0,x1,y1,z1 FROM layout_bins LIMIT 0')
    state={row[0]:row[1] for row in c.execute('SELECT key,value FROM layout_packing')}
    if set(state)!={'x','y','z','row','layer','target','volume'} or not all(isinstance(value,(int,float)) and math.isfinite(value) and value>=0 for value in state.values()) or state['target']<=0:
        return False
    new=set(added_nodes);endpoints={};arcs=[]
    for ordinal in added_edges:
        f,t=c.execute('SELECT f,t FROM edges WHERE ordinal=?',(ordinal,)).fetchone();arcs.append((f,t))
        for node in (f,t):
            if node not in new and node not in endpoints:
                row=c.execute('SELECT m.bin,b.x0,b.y0,b.z0,b.x1,b.y1,b.z1,n.x,n.y,n.z FROM layout_bin_nodes m JOIN layout_bins b ON b.id=m.bin JOIN nodes n ON n.ordinal=m.node WHERE m.node=?',(node,)).fetchone()
                if row is None or not all(isinstance(value,(int,float)) and math.isfinite(value) for value in row[1:]) or not all(row[1+i]<=row[7+i]<=row[4+i] for i in range(3)):
                    return False
                endpoints[node]=row[0]
    groups=component_sets(added_nodes,((f,t) for f,t in arcs if f in new and t in new))
    group_for={node:index for index,members in enumerate(groups) for node in members}
    neighbors=[set() for _ in groups]
    for f,t in arcs:
        if f in new and t not in new:neighbors[group_for[f]].add(endpoints[t])
        if t in new and f not in new:neighbors[group_for[t]].add(endpoints[f])
    ids={n:c.execute('SELECT id FROM nodes WHERE ordinal=?',(n,)).fetchone()[0] for n in added_nodes}
    for index in sorted(range(len(groups)),key=lambda i:min(ids[n] for n in groups[i])):
        members=groups[index]
        if neighbors[index]:
            bin_id=min(neighbors[index])
            box=c.execute('SELECT x0,y0,z0,x1,y1,z1 FROM layout_bins WHERE id=?',(bin_id,)).fetchone()
        else:
            bin_id=min(ids[n] for n in members);box=allocate_box(state,[COMPONENT_MARGIN*2]*3)
            c.execute('INSERT INTO layout_bins VALUES(?,?,?,?,?,?,?)',(bin_id,*box))
        for node in members:
            unit=volume_point(ids[node],.9)
            xyz=[(box[i]+box[i+3])/2+unit[i]*(box[i+3]-box[i])/2 for i in range(3)]
            c.execute('UPDATE nodes SET x=?,y=?,z=? WHERE ordinal=?',(*xyz,node))
            c.execute('INSERT INTO layout_bin_nodes VALUES(?,?)',(node,bin_id))
    save_packing_state(c,state)
    return True


def raw_bounds(c):
    row=c.execute('SELECT min(x),min(y),min(z),max(x),max(y),max(z) FROM nodes').fetchone()
    return None if row[0] is None else {'min':list(row[:3]),'max':list(row[3:])}


def padded_bounds(raw):
    if raw is None:
        return {'min':[-1]*3,'max':[1]*3}
    padding=[max(.001,(raw['max'][i]-raw['min'][i])*.005) for i in range(3)]
    return {'min':[raw['min'][i]-padding[i] for i in range(3)],'max':[raw['max'][i]+padding[i] for i in range(3)]}


def bounds(c):
    return padded_bounds(raw_bounds(c))


def valid_raw_bounds(value):
    return isinstance(value,dict) and all(isinstance(value.get(key),list) and len(value[key])==3 and all(isinstance(n,(int,float)) and not isinstance(n,bool) and math.isfinite(n) for n in value[key]) for key in ['min','max']) and all(value['min'][i]<=value['max'][i] for i in range(3))


def append_bounds(c,old,added_nodes):
    cached=old.manifest.get('layout',{}).get('raw_bounds')
    if not added_nodes:
        previous=old.manifest['bounds']
        return {'min':list(previous['min']),'max':list(previous['max'])},cached if valid_raw_bounds(cached) else None
    if old.manifest['counts']['nodes'] and not valid_raw_bounds(cached):
        # Legacy snapshots have only padded extents. Reconstruct exact extrema
        # once instead of inverting rounded padding or accumulating it repeatedly.
        exact=raw_bounds(c)
        return padded_bounds(exact),exact
    exact={'min':list(cached['min']),'max':list(cached['max'])} if old.manifest['counts']['nodes'] else None
    for ordinal in added_nodes:
        row=c.execute('SELECT x,y,z FROM nodes WHERE ordinal=?',(ordinal,)).fetchone()
        if row is None or not all(isinstance(n,(int,float)) and math.isfinite(n) for n in row):
            raise ValueError('Appended position is unavailable')
        if exact is None:
            exact={'min':list(row),'max':list(row)}
        else:
            for axis in range(3):
                exact['min'][axis]=min(exact['min'][axis],row[axis])
                exact['max'][axis]=max(exact['max'][axis],row[axis])
    return padded_bounds(exact),exact
