"""Optional server-to-server read capability; never owner-session authority."""
import hashlib
import hmac
import os
import re
import stat
from pathlib import Path

# Session endpoints are deliberately absent, including GET /session.
READ_PATHS=tuple(re.compile(pattern) for pattern in (
    r'/api/v1/(?:capabilities|graph|search|events|bookmarks)',
    r'/api/v1/graph/[^/]+/(?:nodes\.bin|edges\.bin|topics|sessions)',
    r'/api/v1/graph/[^/]+/nodes/[0-9]+(?:/edges)?',
    r'/api/v1/graph/[^/]+/edges/[0-9]+',
    r'/api/v1/(?:nodes|edges|sources|overviews)/.+',
))


class BridgeReadService:
    def __init__(self,secret_file=None):
        self._digest=None
        if secret_file is None:
            return
        path=Path(secret_file); info=path.stat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>1024:
            raise ValueError('Bridge read credential requires an owner-only regular file')
        token=path.read_bytes().rstrip(b'\r\n')
        if not 32<=len(token)<=512 or any(value<33 or value>126 for value in token):
            raise ValueError('Bridge read credential must be a high-entropy printable secret of 32–512 bytes')
        self._digest=hashlib.sha256(token).digest()

    def authorizes(self,request):
        if self._digest is None or request.method not in {'GET','HEAD'}:
            return False
        if not any(pattern.fullmatch(request.url.path) for pattern in READ_PATHS):
            return False
        header=request.headers.get('authorization','')
        if len(header)>1024:
            return False
        scheme,separator,token=header.partition(' ')
        if scheme.lower()!='bearer' or not separator or not 32<=len(token)<=512:
            return False
        try:
            supplied=hashlib.sha256(token.encode('ascii')).digest()
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(supplied,self._digest)


def read_service_authorized(request):
    service=getattr(request.app.state,'bridge_read_service',None)
    return service is not None and service.authorizes(request)


def stream_authorized(request):
    return request.app.state.owner.session(request) is not None or read_service_authorized(request)
