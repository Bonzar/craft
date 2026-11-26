"""Graph -> existing Base PROD owner REST. No user-selected routes or authorities."""
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from prototypes.assertion_memory import processing_control
from prototypes.assertion_memory.queue_item_removal import PATH as REMOVE, PREVIEW_PATH
from prototypes.assertion_memory.queue_pause_proof import ENVELOPE, HEX, PATH as PAUSE, TTL, ProvenOwner, canonical
from prototypes.assertion_memory.queue_pause_rest import unique
from prototypes.assertion_memory.queue_processing_policy import PATH as POLICY
from prototypes.assertion_memory.queue_session_control import PATH as SESSIONS

from .common import APIError

_ROUTES = {
    'pause_prepare': ('GET', PAUSE, frozenset()),
    'pause_set': ('PUT', PAUSE, frozenset(('paused',))),
    'sessions_prepare': ('GET', SESSIONS, frozenset()),
    'sessions_set': ('PUT', SESSIONS, frozenset(('selected_session_ids',))),
    'policy_prepare': ('GET', POLICY, frozenset()),
    'policy_set': ('PUT', POLICY, frozenset(('policy',))),
    'item_preview': ('PUT', PREVIEW_PATH, frozenset(('queue_id', 'item_id'))),
    'item_remove': ('PUT', REMOVE, frozenset(('queue_id', 'item_id'))),
}
_CREDENTIALS = ('queue-base-token', 'pause-read-token', 'pause-write-token', 'pause-owner-digest', 'pause-proof-key')
_SNAPSHOT_FIELDS = {'stage', 'release_version', 'release_generation', 'global_order',
                    'snapshot_at', 'queues', 'processing_control'}
_RELEASE = re.compile(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?\Z')


class ProdOwnerBridge:
    stage = 'prod'
    def __init__(self, queue_key, read_key, write_key, owner_digest, proof_key, *,
                 http_connection=HTTPConnection, clock=time.time):
        keys=(queue_key,read_key,write_key,owner_digest)
        if (any(type(k) is not str or not HEX.fullmatch(k) for k in keys)
                or type(proof_key) is not bytes or len(proof_key)!=32 or len(set((*keys,proof_key.hex())))!=5):
            raise ValueError('Invalid existing PROD Base authority')
        self._queue,self._read,self._write,self._owner=keys
        self._secret,self._connection,self._clock=proof_key,http_connection,clock

    def _request(self, method, path, token, *, body=None, proof=None, limit=64_000, timeout=4):
        headers={'Accept':'application/json','Authorization':'Bearer '+token,'Connection':'close'}
        if body is not None:
            signed=hmac.new(self._secret,canonical(body,proof),sha256).hexdigest()
            headers.update(dict(zip((h.decode('ascii') for h in ENVELOPE),
                (proof.owner,str(proof.issued_at),str(proof.expires_at),proof.nonce,signed))))
            headers['Content-Type']='application/json'
        if path.startswith('/api/v1/queue-status?'):
            headers['X-Memory-Queue-Format']='batch-cards-v1'
        conn=None
        try:
            conn=self._connection('127.0.0.1',8903,timeout=timeout)
            conn.request(method,path,body=body,headers=headers)
            rsp=conn.getresponse()
            if rsp.status==409:
                raise processing_control.Conflict('Base owner state changed')
            if (rsp.status!=200 or rsp.headers.get_content_type()!='application/json'
                    or rsp.headers.get('Content-Encoding') is not None):
                raise APIError('queue_unavailable','Base owner service unavailable',503)
            raw=rsp.read(limit+1)
            if len(raw)>limit:
                raise ValueError()
            result=json.loads(raw,object_pairs_hook=unique)
            if type(result) is not dict or result.get('stage')!='prod':
                raise ValueError()
            return result
        except (OSError, HTTPException, ValueError) as error:
            if isinstance(error,processing_control.Conflict):
                raise
            raise APIError('queue_unavailable','Invalid or unavailable Base owner response',503) from None
        finally:
            if conn is not None:
                conn.close()

    def perform(self, action, args):
        if action not in _ROUTES or type(args) is not dict:
            raise ValueError('Unsupported Base owner action')
        method,path,fields=_ROUTES[action]
        if method=='GET':
            if args:
                raise ValueError('Unexpected Base read arguments')
            return self._check_state(path,self._request(method,path,self._read),args)
        revision=args.get('expected_revision')
        if (set(args)!=fields|{'expected_revision'} or type(revision) is not str
                or not processing_control.REVISION.fullmatch(revision)):
            raise ValueError('Invalid Base owner body')
        body=json.dumps(args,separators=(',',':'),ensure_ascii=True).encode('ascii')
        if len(body)>(16384 if path in (SESSIONS,POLICY) else 1024):
            raise ValueError('Oversized Base owner body')
        now=int(self._clock())
        proof=ProvenOwner(self._owner,revision,now,now+TTL,secrets.token_hex(16),'prod',path)
        return self._check_state(path,self._request(method,path,self._write,body=body,proof=proof),args)

    @staticmethod
    def _check_state(path, value, args):
        expected={
            PAUSE:{'stage','revision','paused'},
            SESSIONS:{'stage','revision','selected_session_ids','sessions'},
            POLICY:{'stage','revision','policy','sessions'},
            PREVIEW_PATH:{'stage','queue_id','item_id','source_preview','source_role'},
            REMOVE:{'stage','queue_id','item_id','removed','effect'},
        }[path]
        if (set(value)!=expected
                or (path in (PAUSE,SESSIONS,POLICY) and (type(value['revision']) is not str
                    or not processing_control.REVISION.fullmatch(value['revision'])))
                or (path==PAUSE and type(value['paused']) is not bool)
                or (path in (SESSIONS,POLICY) and type(value['sessions']) is not list)
                or (path in (REMOVE,PREVIEW_PATH) and (value['item_id']!=args['item_id']
                    or value['queue_id']!=args['queue_id']))
                or (path==REMOVE and (value['removed'] is not True
                    or value['effect']!=('source_and_queue' if args['queue_id']=='source' else 'queue_only')))
                or (path==PREVIEW_PATH and (type(value['source_preview']) is not str
                    or not 0<len(value['source_preview'])<=240
                    or value['source_role'] not in (None,'user','assistant','system','developer','tool')))):
            raise APIError('queue_unavailable','Invalid Base owner receipt',503)
        return value

    def queue_snapshot(self, *, limit, offset):
        if type(limit) is not int or not 1<=limit<=50 or type(offset) is not int or not 0<=offset<=1_000_000:
            raise ValueError('Invalid Base queue page')
        value=self._request('GET',f'/api/v1/queue-status?limit={limit}&offset={offset}',self._queue,
                            timeout=3,limit=2_400_000)
        control=value.get('processing_control')
        if (set(value)!=_SNAPSHOT_FIELDS or value.get('global_order') is not False
                or not isinstance(value.get('release_version'),str) or not _RELEASE.fullmatch(value['release_version'])
                or not isinstance(value.get('release_generation'),str) or not HEX.fullmatch(value['release_generation'])
                or not isinstance(value.get('snapshot_at'),str) or len(value['snapshot_at'])>64
                or not isinstance(value.get('queues'),list) or len(value['queues'])>3
                or not isinstance(control,dict) or set(control)!={'paused','revision','can_change'}
                or type(control['paused']) is not bool or type(control['can_change']) is not bool
                or type(control['revision']) is not str or not processing_control.REVISION.fullmatch(control['revision'])):
            raise APIError('queue_unavailable','Invalid Base queue projection',503)
        return value


def configured_prod_owner_bridge(credentials_directory):
    if not isinstance(credentials_directory,(str,Path)) or not Path(credentials_directory).is_absolute():
        raise ValueError('PROD Base owner bridge requires existing systemd credentials')
    root=Path(credentials_directory)
    if root.resolve(strict=True)!=root or not root.is_dir():
        raise ValueError('Invalid systemd credentials directory')
    directory=os.open(root,os.O_RDONLY|os.O_NOFOLLOW|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        dirmeta=os.fstat(directory)
        if (dirmeta.st_uid not in (0,os.geteuid()) or dirmeta.st_mode&0o022
                or not os.path.samestat(dirmeta,root.lstat())):
            raise ValueError('Invalid systemd credentials directory')
        def read(name):
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=directory)
            try:
                meta=os.fstat(fd)
                if (not stat.S_ISREG(meta.st_mode) or meta.st_uid not in (0,os.geteuid())
                        or meta.st_mode&0o077 or meta.st_nlink!=1 or meta.st_size not in (64,65)):
                    raise ValueError('Invalid existing systemd credential')
                raw=os.read(fd,66)
                value=raw.removesuffix(b'\n').decode('ascii')
                if not HEX.fullmatch(value):
                    raise ValueError('Invalid existing systemd credential')
                return value
            finally:
                os.close(fd)
        queue,read_key,write_key,owner,secret=map(read,_CREDENTIALS)
        return ProdOwnerBridge(queue,read_key,write_key,owner,bytes.fromhex(secret))
    finally:
        os.close(directory)
