"""Lossless storage packing; logical versions, addresses and hashes never change."""
import base64
import json
import zlib

PACKING_FORMAT = 'thought-payload/zlib-v1'
PACKING_MIN_CHARACTERS = 2048


def encode_payload(value):
    original = json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    if len(original) < PACKING_MIN_CHARACTERS:
        return original
    encoded = base64.b64encode(zlib.compress(original.encode(),level=6)).decode('ascii')
    # Preserve the indexed SQL selection predicate without decompressing rows.
    context = value.get('metadata',{}).get('selection_context')
    metadata = {'selection_context':context} if context is not None else {}
    refs = [{key:basis[key] for key in ('thought_id','version','event_id') if key in basis}
            for basis in value.get('bases',[])]
    packed = json.dumps({'_format':PACKING_FORMAT,'_zlib':encoded,'metadata':metadata,'bases':refs},separators=(',',':'))
    return packed if len(packed) < len(original) else original


def decode_payload(raw):
    value = json.loads(raw)
    if isinstance(value,dict) and value.get('_format') == PACKING_FORMAT:
        return json.loads(zlib.decompress(base64.b64decode(value['_zlib'],validate=True)))
    return value


def compact_batch(thoughts, *, after_rowid=0, through_rowid=None, limit=100):
    if type(limit) is not int or limit < 1:
        raise ValueError('compaction page must be positive')
    with thoughts.store.transaction() as db:
        maximum = through_rowid if through_rowid is not None else db.execute(
            'SELECT coalesce(max(rowid),0) FROM memory_thought_versions').fetchone()[0]
        rows = db.execute('SELECT rowid,payload,content_hash FROM memory_thought_versions '
                          'WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?', (after_rowid,maximum,limit)).fetchall()
    # Compression never holds the database writer; only the guarded byte swap does.
    updates = []
    saved = 0
    for row in rows:
        packed = encode_payload(decode_payload(row['payload']))
        if packed != row['payload']:
            updates.append((packed,row['rowid'],row['payload'],row['content_hash']))
            saved += len(row['payload'].encode())-len(packed.encode())
    with thoughts.store.transaction(write=True) as db:
        for update in updates:
            if db.execute('UPDATE memory_thought_versions SET payload=? WHERE rowid=? AND payload=? '
                          'AND content_hash=?',update).rowcount != 1:
                raise RuntimeError('immutable thought payload changed during compaction')
    end = rows[-1]['rowid'] if rows else maximum
    return {'after_rowid':end,'through_rowid':maximum,'complete':end>=maximum,
            'processed':len(rows),'packed':len(updates),'saved_bytes':saved}
