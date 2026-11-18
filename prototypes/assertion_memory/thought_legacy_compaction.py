"""Bounded removal of legacy byte copies, backed by exact canonical history.

Run after validated canonical cutover. Legacy semantic readers must resolve the
small reference receipt; useful canonical versions, hashes and grounds never change.
"""
from __future__ import annotations
import hashlib
import json
from collections import Counter
from .thought_store import _hash
try:
    from .thought_payload import decode_payload
except ImportError:
    decode_payload = json.loads

_FORMAT = 'canonical-thought-reference/v1'
_POLICY = 'legacy-duplicate-compaction-v1'


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _exists(c,table):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is not None


def _canonical(c,old):
    if not _exists(c,'memory_thought_legacy'):
        return None
    rows = c.execute("SELECT v.payload,v.content_hash FROM memory_thought_legacy a JOIN memory_thought_versions v "
        "ON v.thought_id=a.thought_id AND v.version=a.thought_version WHERE a.source_table='memory_view_versions' AND a.source_key=?",(str(old['id']),))
    for row in rows:
        payload = decode_payload(row['payload'])
        legacy = payload.get('metadata',{}).get('legacy',{})
        original = legacy.get('original',{})
        snapshot = original.get('snapshot')
        if not isinstance(snapshot,str) or legacy.get('version_id') != old['id']:
            continue
        if any(original.get(key) != old[key] for key in ('id','view_id','fingerprint')):
            continue
        if payload.get('id') != old['view_id'] or _hash(payload) != row['content_hash']:
            continue
        logical = json.loads(snapshot)
        if payload.get('text') != logical.get('summary'):
            continue
        current = json.loads(old['snapshot'])
        receipt = {'_format':_FORMAT,'thought_id':payload['id'],'version':payload['version'],
            'content_hash':row['content_hash'],'snapshot_sha256':_digest(snapshot)}
        if old['snapshot'] != snapshot and current != receipt:
            continue
        return {'payload':payload,'snapshot':snapshot,'logical':logical,'receipt':receipt}
    return None


def resolve_legacy_snapshot(thoughts,legacy_version_id):
    """Read the original full snapshot through either old bytes or the tiny receipt."""
    with thoughts.store.transaction() as c:
        old = c.execute('SELECT * FROM memory_view_versions WHERE id=?',(legacy_version_id,)).fetchone()
        if old is None:
            return None
        body = json.loads(old['snapshot'])
        if body.get('_format') != _FORMAT:
            return body
        proven = _canonical(c,old)
        if proven is None:
            raise ValueError('legacy snapshot reference has no exact preserved canonical history')
        return proven['logical']


def _state(c):
    if _exists(c,'memory_thought_migration') and c.execute(
        'SELECT 1 FROM memory_thought_migration WHERE cursor<end_cursor LIMIT 1').fetchone():
        raise ValueError('complete and validate migration before compacting legacy duplicates')
    c.execute('CREATE TABLE IF NOT EXISTS memory_thought_legacy_compaction '
        '(policy TEXT PRIMARY KEY,phase TEXT NOT NULL,cursor INTEGER NOT NULL,end_cursor INTEGER NOT NULL,protected TEXT NOT NULL)')
    end = c.execute('SELECT coalesce(max(rowid),0) FROM memory_view_versions').fetchone()[0] if _exists(c,'memory_view_versions') else 0
    c.execute("INSERT OR IGNORE INTO memory_thought_legacy_compaction VALUES(?,'versions',0,?,'[]')",(_POLICY,end))
    result = dict(c.execute('SELECT * FROM memory_thought_legacy_compaction WHERE policy=?',(_POLICY,)).fetchone())
    result['protected'] = json.loads(result['protected'])
    return result


def _advance(c,state,phase,table):
    state.update(phase=phase,cursor=0,end_cursor=c.execute('SELECT coalesce(max(rowid),0) FROM '+table).fetchone()[0]
        if _exists(c,table) else 0)


def _compact_snapshot(c,row,counts,protected):
    proven = _canonical(c,row)
    if proven is None:
        protected.append({'kind':'legacy_snapshot','version_id':row['id'],'reason':'no exact preserved canonical snapshot'})
        return
    body = _json(proven['receipt'])
    saved = len(row['snapshot'].encode())-len(body.encode())
    if saved>0:
        c.execute('UPDATE memory_view_versions SET snapshot=? WHERE id=?',(body,row['id']))
        counts['snapshots_compacted'] += 1
        counts['bytes_removed'] += saved


def _compact_proof(c,row,counts,protected):
    if not row['overview_review']:
        return
    old = c.execute('SELECT * FROM memory_view_versions WHERE id=?',(row['version_id'],)).fetchone()
    proven = _canonical(c,old) if old else None
    proof = json.loads(row['overview_review'])
    if proof.get('_format') == 'canonical-thought-proof-reference/v1':
        return
    if proven is None or proof != proven['logical'].get('overview_review'):
        protected.append({'kind':'legacy_check_proof','check_id':row['id'],'reason':'proof not duplicated exactly in canonical snapshot'})
        return
    body = _json({'_format':'canonical-thought-proof-reference/v1',
        'thought_id':proven['payload']['id'],'version':proven['payload']['version'],
        'content_hash':proven['receipt']['content_hash'],'proof_sha256':_digest(_json(proof))})
    saved = len(row['overview_review'].encode())-len(body.encode())
    if saved>0:
        c.execute('UPDATE memory_view_checks SET overview_review=? WHERE id=?',(body,row['id']))
        counts['check_proofs_compacted'] += 1
        counts['bytes_removed'] += saved


def compact_legacy_batch(thoughts,*,limit=100):
    """Compact at most limit examined rows; unmatched proof stays byte-for-byte."""
    if type(limit) is not int or limit<1:
        raise ValueError('compaction limit must be positive')
    counts,processed = Counter(),0
    with thoughts.store.transaction(write=True) as c:
        state = _state(c)
        while processed<limit and state['phase']!='done':
            phase,remaining = state['phase'],limit-processed
            table = {'versions':'memory_view_versions','checks':'memory_view_checks','jobs':'memory_capture_jobs'}[phase]
            rows = c.execute('SELECT rowid AS compact_rowid,* FROM '+table+' WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?',
                (state['cursor'],state['end_cursor'],remaining)).fetchall() if _exists(c,table) else []
            for row in rows:
                if phase=='versions':
                    _compact_snapshot(c,row,counts,state['protected'])
                elif phase=='checks':
                    _compact_proof(c,row,counts,state['protected'])
                elif row['status']=='completed':
                    saved = sum(len(row[key].encode()) for key in ('candidates','proposals','refreshed_views'))-6
                    if saved>0:
                        c.execute("UPDATE memory_capture_jobs SET candidates='[]',proposals='[]',refreshed_views='{}' WHERE id=?",(row['id'],))
                        counts['completed_jobs_compacted'] += 1
                        counts['bytes_removed'] += saved
                state['cursor']=row['compact_rowid']; processed+=1
            if len(rows)<remaining:
                if phase=='versions':_advance(c,state,'checks','memory_view_checks')
                elif phase=='checks':_advance(c,state,'jobs','memory_capture_jobs')
                else:state['phase']='done'
        c.execute('UPDATE memory_thought_legacy_compaction SET phase=?,cursor=?,end_cursor=?,protected=? WHERE policy=?',
            (state['phase'],state['cursor'],state['end_cursor'],_json(state['protected']),_POLICY))
    return {'processed':processed,'counts':dict(counts),'complete':state['phase']=='done','protected':state['protected']}
