"""Technical image blobs only; no decoding into facts, vision, files or URL fetches."""
import base64
import hashlib

MAX_BYTES=2*1024*1024
MAX_IMAGES=8
MIME_ALIASES={'image/jpg':'image/jpeg','image/pjpeg':'image/jpeg','image/x-png':'image/png'}
MIMES={'image/png','image/jpeg','image/gif','image/webp'}


def canonical_mime(value):
    if not isinstance(value,str):raise ValueError('unsupported_image_mime')
    normalized=value.strip().lower()
    return MIME_ALIASES.get(normalized,normalized)


def describe(data,mime):
    mime=canonical_mime(mime)
    if not isinstance(data,bytes) or not data or len(data)>MAX_BYTES:raise ValueError('image_size_limit')
    actual=('image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else
            'image/jpeg' if data.startswith(b'\xff\xd8\xff') else
            'image/gif' if data.startswith((b'GIF87a',b'GIF89a')) else
            'image/webp' if data.startswith(b'RIFF') and data[8:12]==b'WEBP' else None)
    if mime not in MIMES or actual!=mime:raise ValueError('image_mime_or_magic_mismatch')
    digest=hashlib.sha256(data).hexdigest()
    return {'artifact_id':'artifact-'+digest,'sha256':digest,'size_bytes':len(data),'mime_type':mime,'not_interpreted':True}


def data_uri(value):
    if not isinstance(value,str) or not value.startswith('data:'):raise ValueError('external_image_not_fetched')
    try:header,payload=value.split(',',1)
    except ValueError:raise ValueError('unsupported_image_data_uri') from None
    if not header.endswith(';base64') or header.count(';')!=1:raise ValueError('unsupported_image_data_uri')
    if len(payload)>((MAX_BYTES+2)//3)*4:raise ValueError('image_size_limit')
    try:data=base64.b64decode(payload,validate=True)
    except ValueError:raise ValueError('invalid_image_base64') from None
    meta=describe(data,header[5:-7])
    return {'metadata':meta,'data':data}
