"""Single owner credentials and independent, revocable opaque sessions."""
import hashlib
import hmac
import json
import secrets
import re
import sqlite3
from urllib.parse import urlsplit
import time
import threading
from collections import OrderedDict
from pathlib import Path
from .common import APIError, now, selector

COOKIE = 'gv_session'


class OwnerStore:
    def __init__(self, directory, password=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.path = self.directory / 'viewer.sqlite3'
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS owner(id INTEGER PRIMARY KEY,salt BLOB,hash BLOB);
                CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,csrf TEXT,expires REAL);
                CREATE TABLE IF NOT EXISTS bookmarks(id TEXT PRIMARY KEY,body TEXT,created_at TEXT);
            ''')
            if password is not None:
                if len(password) < 12:
                    raise ValueError('Owner password must have at least twelve characters')
                row = c.execute('SELECT salt,hash FROM owner WHERE id=1').fetchone()
                if not row:
                    salt = secrets.token_bytes(16)
                    c.execute('INSERT INTO owner VALUES(1,?,?)', (salt, self.derive(password,salt)))
        self.path.chmod(0o600)
        self.attempts = OrderedDict()
        self._attempt_lock=threading.Lock()

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    @staticmethod
    def derive(password, salt):
        return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)

    def login(self, password, client):
        self.limit_login(client)
        with self.connect() as c:
            row = c.execute('SELECT salt,hash FROM owner WHERE id=1').fetchone()
            salt = row[0] if row else bytes(16)
            actual = self.derive(password if isinstance(password,str) and len(password)<=4096 else '', salt)
            if not row or not hmac.compare_digest(actual, row[1]):
                raise APIError('unauthorized', 'Invalid owner credential', 401)
            token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
            c.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(),csrf,time.time()+86400*14))
        return token, csrf

    def limit_login(self,client):
        with self._attempt_lock:
            cutoff=time.monotonic()-60
            attempts=[t for t in self.attempts.pop(client,[]) if t>cutoff]
            if len(attempts)>=6:
                self.attempts[client]=attempts
                raise APIError('rate_limited','Too many login attempts; retry in one minute',429)
            attempts.append(time.monotonic()); self.attempts[client]=attempts
            while len(self.attempts)>256:
                self.attempts.popitem(last=False)

    def session(self, request):
        token = request.cookies.get(COOKIE, '')
        with self.connect() as c:
            row = c.execute('SELECT csrf FROM sessions WHERE token=? AND expires>?',
                            (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
        return row[0] if row else None

    def revoke(self, request):
        with self.connect() as c:
            c.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(request.cookies.get(COOKIE,'').encode()).hexdigest(),))

    def reset_graph_bookmarks(self,provider_id,epoch):
        # Share one viewer store with auth, but mutate only bookmarked graph data.
        with self.connect() as db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS bookmark_reset_epoch '
                       '(provider_id TEXT PRIMARY KEY,epoch TEXT NOT NULL)')
            previous=db.execute('SELECT epoch FROM bookmark_reset_epoch WHERE provider_id=?',
                                (provider_id,)).fetchone()
            if previous and previous[0]==epoch:
                return
            db.execute("DELETE FROM bookmarks WHERE json_valid(body) AND json_extract(body,'$.provider_id')=?",
                       (provider_id,))
            db.execute('INSERT INTO bookmark_reset_epoch VALUES (?,?) '
                       'ON CONFLICT(provider_id) DO UPDATE SET epoch=excluded.epoch',(provider_id,epoch))

    def bookmarks(self, provider_id):
        with self.connect() as c:
            rows = c.execute('SELECT id,body,created_at FROM bookmarks ORDER BY created_at,id').fetchall()
        return {'items': [dict(json.loads(body),id=id,created_at=created,availability='available' if json.loads(body)['provider_id']==provider_id else 'provider_unavailable') for id,body,created in rows], 'revision':str(len(rows))}

    def bookmark(self, id, body=None):
        if not id or len(id)>200:
            raise APIError('invalid_bookmark','Invalid bookmark identifier')
        with self.connect() as c:
            if body is None:
                c.execute('DELETE FROM bookmarks WHERE id=?',(id,))
                return {'deleted':True}
            if body.get('object_kind') not in {'node','topic'} or not all(isinstance(body.get(k),str) and 0<len(body[k])<=1000 for k in ['object_id','provider_id']):
                raise APIError('invalid_bookmark','Bookmark requires stable provider and object identifiers')
            clean=validate_bookmark(body)
            if len(json.dumps(clean))>16000:
                raise APIError('invalid_bookmark','Bookmark is too large')
            c.execute('INSERT INTO bookmarks VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body', (id,json.dumps(clean),now()))
        return dict(clean,id=id)


def check_origin(request, *, required=False):
    origin = request.headers.get('origin')
    expected = getattr(request.app.state,'public_origin',None) or str(request.base_url).rstrip('/')
    if (required and not origin) or (origin is not None and origin != expected):
        raise APIError('origin_denied','Same-origin request required',403)


def validate_bookmark(body):
    clean={k:body[k] for k in ['object_kind','object_id','provider_id']}
    if 'label' in body:
        if not isinstance(body['label'],str) or len(body['label'])>300:
            raise APIError('invalid_bookmark','Bookmark label must be a short string')
        clean['label']=body['label']
    if body.get('selector') is not None:
        value=body['selector']
        allowed={'mode','at','history','undated','date','selected_date','timezone','session_id','run_id','output_snapshot_id'}
        if not isinstance(value,dict) or set(value)-allowed:
            raise APIError('invalid_bookmark','Invalid bookmark perspective')
        if 'history' in value and type(value['history']) is not bool:
            raise APIError('invalid_bookmark','Bookmark history must be boolean')
        clean['selector']=selector(dict(value,history='1' if value.get('history') else '0'))
        for key in ['date','selected_date','timezone']:
            if key in value:
                if not isinstance(value[key],str) or len(value[key])>100:
                    raise APIError('invalid_bookmark','Invalid bookmark date or timezone')
                clean['selector'][key]=value[key]
    else:
        clean['selector']=None
    return clean


def secure_transport(request):
    return request.url.scheme=='https' or bool(getattr(request.app.state,'public_origin',None))


def check_host(request):
    allowed={'localhost','127.0.0.1','::1','testserver'}
    allowed.update(getattr(request.app.state,'allowed_proxy_hosts',()))
    origin=getattr(request.app.state,'public_origin',None)
    if origin:
        allowed.add(urlsplit(origin).hostname)
    if request.url.hostname not in allowed:
        raise APIError('invalid_host','Untrusted request host',400)


def validate_public_origin(value):
    if value is None:
        return None
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:
        raise ValueError('Public origin must be an explicit HTTPS origin without path or credentials')
    return value.rstrip('/')


def validate_proxy_hosts(values):
    if isinstance(values,str):
        raise ValueError('Proxy hosts must be an explicit hostname list')
    result=set()
    for value in values:
        if not isinstance(value,str) or len(value)>253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',label) for label in value.lower().split('.')):
            raise ValueError('Proxy hosts must be exact DNS hostnames without URLs or wildcards')
        result.add(value.lower())
    return frozenset(result)
