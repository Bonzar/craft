"""Local policy binding; no permissions, storage or network changes."""
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

def verify_machine_binding(binding):
    connection=binding['connection']
    capture=binding['capture']
    url=urlsplit(connection['url'])
    path=Path(capture['state_dir'])
    if (url.scheme!='https' or not url.hostname or url.username or url.password or url.port or
       url.query or url.fragment or url.path!='/memory-api-prod' or not path.is_absolute() or
       '..' in path.parts or not isinstance(capture['fresh_since'],str)):
        raise ValueError('Invalid machine capture scope')
    expected=hashlib.sha256(('private_base_http\x00prod\x00'+connection['url']+'\x00'+capture['fresh_since']).encode()).hexdigest()
    if binding['endpoint_sha256']!=expected:
        raise ValueError('Capture scope disagrees with endpoint/cutover')
    return path / expected
