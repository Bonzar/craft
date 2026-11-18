"""Bounded immutable image bytes, separate from textual source/model evidence.

Only declared MIME/signature/encoding are validated. No image interpretation,
network retrieval, local-path access, OCR or model call happens here.
"""
from __future__ import annotations
import base64
import binascii
from datetime import datetime,timezone
import hashlib
import re

MAX_BYTES=2*1024*1024
MAX_ENCODED=4*((MAX_BYTES+2)//3)
MIME_ALIASES={'image/png':'image/png','image/x-png':'image/png',
    'image/jpeg':'image/jpeg','image/jpg':'image/jpeg','image/pjpeg':'image/jpeg',
    'image/webp':'image/webp','image/gif':'image/gif'}
_COLUMNS='artifact_id,sha256,size_bytes,mime_type'


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS memory_artifacts (
        artifact_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE,
        mime_type TEXT NOT NULL CHECK(mime_type IN ('image/png','image/jpeg','image/webp','image/gif')),
        size_bytes INTEGER NOT NULL CHECK(size_bytes>0 AND size_bytes<=2097152),
        data BLOB NOT NULL CHECK(length(data)=size_bytes), created_at TEXT NOT NULL,
        CHECK(artifact_id='artifact-'||sha256))''')
    for operation in ('UPDATE','DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS memory_artifacts_no_{operation.lower()} "
            f"BEFORE {operation} ON memory_artifacts BEGIN SELECT RAISE(ABORT,'artifacts are immutable'); END")
    # Prevent INSERT OR REPLACE from bypassing ordinary update/delete guards.
    db.execute("CREATE TRIGGER IF NOT EXISTS memory_artifacts_no_replace BEFORE INSERT ON memory_artifacts "
        "WHEN EXISTS(SELECT 1 FROM memory_artifacts WHERE artifact_id=new.artifact_id OR sha256=new.sha256) "
        "BEGIN SELECT RAISE(ABORT,'artifacts are immutable'); END")


def _identifier(value):
    if not isinstance(value,str) or re.fullmatch(r'artifact-[0-9a-f]{64}',value) is None:
        raise ValueError('canonical content-SHA artifact_id required')
    return value


def _signature(data,mime):
    valid=(mime=='image/png' and len(data)>=33 and data[:8]==b'\x89PNG\r\n\x1a\n'
           and data[8:16]==b'\x00\x00\x00\rIHDR'
        or mime=='image/jpeg' and len(data)>=4 and data[:3]==b'\xff\xd8\xff' and data[-2:]==b'\xff\xd9'
        or mime=='image/gif' and len(data)>=13 and data[:6] in (b'GIF87a',b'GIF89a')
        or mime=='image/webp' and len(data)>=12 and data[:4]==b'RIFF' and data[8:12]==b'WEBP'
           and int.from_bytes(data[4:8],'little')==len(data)-8)
    if not valid:raise ValueError('declared image MIME does not match supported image signature')


def _descriptor(row):
    return {key:row[key] for key in ('artifact_id','sha256','size_bytes','mime_type')}|{'not_interpreted':True}


def store(db,data_base64,mime_type,sha256=None):
    if not isinstance(data_base64,str) or not 1<=len(data_base64)<=MAX_ENCODED:
        raise ValueError('artifact must be nonempty standard base64, at most 2 MiB decoded')
    if not isinstance(mime_type,str) or mime_type.lower() not in MIME_ALIASES:
        raise ValueError('supported declared image MIME required')
    mime=MIME_ALIASES[mime_type.lower()]
    try:data=base64.b64decode(data_base64,validate=True)
    except (ValueError,binascii.Error):raise ValueError('invalid standard base64 artifact encoding') from None
    if not data or len(data)>MAX_BYTES or base64.b64encode(data).decode('ascii')!=data_base64:
        raise ValueError('noncanonical base64 or artifact exceeds 2 MiB')
    _signature(data,mime)
    digest=hashlib.sha256(data).hexdigest()
    if sha256 is not None and (not isinstance(sha256,str) or re.fullmatch(r'[0-9a-fA-F]{64}',sha256) is None or sha256.lower()!=digest):
        raise ValueError('expected SHA256 does not match artifact bytes')
    identity='artifact-'+digest
    existing=db.execute('SELECT '+_COLUMNS+' FROM memory_artifacts WHERE artifact_id=?',(identity,)).fetchone()
    if existing is not None:
        if existing['sha256']!=digest or existing['mime_type']!=mime or existing['size_bytes']!=len(data):
            raise ValueError('immutable artifact identity conflict')
        return _descriptor(existing)
    db.execute('INSERT INTO memory_artifacts VALUES(?,?,?,?,?,?)',
        (identity,digest,mime,len(data),data,datetime.now(timezone.utc).isoformat(timespec='microseconds')))
    return {'artifact_id':identity,'sha256':digest,'size_bytes':len(data),'mime_type':mime,'not_interpreted':True}


def read(db,artifact_id,*,include_content=False):
    _identifier(artifact_id)
    if type(include_content) is not bool:raise ValueError('include_content must be boolean')
    row=db.execute('SELECT '+_COLUMNS+(',data' if include_content else '')+
        ' FROM memory_artifacts WHERE artifact_id=?',(artifact_id,)).fetchone()
    if row is None:raise ValueError('unknown artifact_id')
    result=_descriptor(row)
    if include_content:
        data=bytes(row['data'])
        if len(data)!=row['size_bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:
            raise ValueError('stored artifact byte integrity check failed')
        _signature(data,row['mime_type'])
        result['data_base64']=base64.b64encode(data).decode('ascii')
    return result


def normalize_ids(values):
    if values is None:return []
    if not isinstance(values,list) or len(values)>8:raise ValueError('artifact_ids must be a list of at most 8 IDs')
    for value in values:_identifier(value)
    return list(dict.fromkeys(values))
