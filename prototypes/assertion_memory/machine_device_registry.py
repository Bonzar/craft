"""Stage-local owner-managed machine fingerprints; no bearer creation or storage."""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

HEX = re.compile(r'[0-9a-f]{64}', re.ASCII)
REV = re.compile(r'[0-9a-f]{32}', re.ASCII)
PROOF = re.compile(r'[0-9]{1,18}\.[0-9a-f]{64}', re.ASCII)
LANES = ('test','prod')
MAX_DEVICES = 16
MAX_PENDING = 64
TTL_MS = 120_000

class MachineDeviceError(ValueError):
    """Code is safe to report; exception never carries a key or token."""


def _matches(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def _clock():
    return time.time_ns() // 1_000_000


def _hash(value):
    return sha256(value.encode('ascii')).hexdigest()


def _label(value):
    if type(value) is not str or value != value.strip() or not value or any(ord(c)<32 or ord(c)==127 for c in value):
        return False
    try:
        return len(value.encode('utf-16-le'))<=160
    except UnicodeError:
        return False


@dataclass(frozen=True)
class Intent:
    stage: str
    expected_revision: str
    action: str
    fingerprint: str
    label: str | None

    @classmethod
    def parse(cls, body):
        if type(body) is not dict or body.get('action') not in ('enroll','revoke'):
            raise MachineDeviceError('invalid_device_intent')
        fields={'stage','expected_revision','action','fingerprint'}
        enrolling=body['action']=='enroll'
        if enrolling:
            fields.add('label')
        if (set(body)!=fields or body['stage'] not in LANES or not _matches(REV,body['expected_revision'])
            or not _matches(HEX,body['fingerprint']) or (enrolling and not _label(body['label']))):
            raise MachineDeviceError('invalid_device_intent')
        return cls(body['stage'],body['expected_revision'],body['action'],body['fingerprint'],body.get('label'))


class MachineDeviceRegistry:
    """Only behind an already verified owner router; never uses the memory DB.

    `owner_binding` is a digest from a verified owner session, not supplied by
    an HTTP request. Router must independently enforce cookie, Host, Origin and
    session-CSRF for prepare; for commit the opaque per-op proof is the CSRF.
    """
    @classmethod
    def initialize(cls, path, lane, configured=(), **options):
        """Explicit private operator bootstrap; never called during read/startup."""
        path = cls._private_path(path)
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        os.close(fd)
        try:
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript("""
                  CREATE TABLE stage(id INTEGER PRIMARY KEY CHECK(id=1), lane TEXT NOT NULL, revision TEXT NOT NULL);
                  CREATE TABLE enrolled(fingerprint TEXT PRIMARY KEY, label TEXT NOT NULL);
                  CREATE TABLE proofs(digest TEXT PRIMARY KEY, owner TEXT NOT NULL, revision TEXT NOT NULL,
                    action TEXT NOT NULL, target TEXT NOT NULL, label TEXT, expires INTEGER NOT NULL);
                """)
                db.execute('INSERT INTO stage VALUES (1,?,?)',(lane,secrets.token_hex(16)))
            return cls(path,lane,configured,**options)
        except BaseException:
            path.unlink()
            raise

    @staticmethod
    def _private_path(path):
        path=Path(path)
        if not path.is_absolute() or path.resolve()!=path or not path.parent.is_dir():
            raise MachineDeviceError('device_registry_path_untrusted')
        parent=path.parent.stat()
        if parent.st_uid!=os.geteuid() or (stat.S_IMODE(parent.st_mode)&0o077):
            raise MachineDeviceError('device_registry_path_untrusted')
        return path

    def __init__(self,path,lane,configured=(),*,reserved=(),clock=_clock):
        if (lane not in LANES or type(configured) is not tuple or len(configured)>MAX_DEVICES
            or type(reserved) is not tuple or len(reserved)>16
            or any(not _matches(HEX,d) for d in (*configured,*reserved)) or len(set(configured))!=len(configured)
            or not set(configured).isdisjoint(reserved)):
            raise MachineDeviceError('invalid_device_registry_policy')
        self.path=self._private_path(path)
        self.lane=lane
        self.configured=configured
        self.reserved=frozenset(reserved)
        self.clock=clock
        st=self.path.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1
            or stat.S_IMODE(st.st_mode)!=0o600):
            raise MachineDeviceError('device_registry_path_untrusted')
        self.identity=(st.st_dev,st.st_ino)
        with self._read() as db:
            self._revision(db)
            self._managed(db)

    def _connect(self,mode):
        st=self.path.lstat()
        if ((st.st_dev,st.st_ino)!=self.identity or not stat.S_ISREG(st.st_mode)
            or st.st_uid!=os.geteuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600):
            raise MachineDeviceError('device_registry_path_untrusted')
        return sqlite3.connect(self.path.as_uri()+'?mode='+mode,uri=True,timeout=2)

    @contextmanager
    def _read(self):
        with closing(self._connect('ro')) as db:
            db.execute('BEGIN')
            yield db

    @contextmanager
    def _write(self):
        with closing(self._connect('rw')) as db, db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    def _revision(self,db):
        row=db.execute('SELECT lane,revision FROM stage WHERE id=1').fetchone()
        if not row or row[0]!=self.lane or not _matches(REV,row[1]):
            raise MachineDeviceError('device_registry_stage_untrusted')
        return row[1]

    def _managed(self,db):
        rows=db.execute('SELECT fingerprint,label FROM enrolled ORDER BY fingerprint LIMIT 17').fetchall()
        if (len(rows)+len(self.configured)>MAX_DEVICES
            or any(not _matches(HEX,d) or (d in self.configured or d in self.reserved) or not _label(label) for d,label in rows)):
            raise MachineDeviceError('device_registry_rows_untrusted')
        return dict(rows)

    def machine_digests(self):
        with self._read() as db:
            self._revision(db)
            return self.configured+tuple(self._managed(db))

    def status(self):
        with self._read() as db:
            revision=self._revision(db)
            managed=self._managed(db)
        return {'schema':'owner-machine-devices/v1','stage':self.lane,'revision':revision,'items':[
            *({'fingerprint':d,'label':'Устройство из конфигурации','revocable':False} for d in self.configured),
            *({'fingerprint':d,'label':label,'revocable':True} for d,label in managed.items())]}

    def _validate(self,db,owner_binding,body):
        if not _matches(HEX,owner_binding):
            raise MachineDeviceError('invalid_device_owner')
        intent=Intent.parse(body)
        if intent.stage!=self.lane or intent.expected_revision!=self._revision(db):
            raise MachineDeviceError('stale_device_registry')
        managed=self._managed(db)
        if intent.fingerprint in self.configured or intent.fingerprint in self.reserved:
            raise MachineDeviceError('configured_device_immutable')
        if intent.action=='enroll' and (intent.fingerprint in managed or len(managed)+len(self.configured)>=MAX_DEVICES):
            raise MachineDeviceError('device_enrollment_not_available')
        if intent.action=='revoke' and intent.fingerprint not in managed:
            raise MachineDeviceError('device_not_managed')
        return intent

    def prepare(self,owner_binding,body):
        now=self.clock()
        if type(now) is not int or not 0<now<10**18-TTL_MS:
            raise MachineDeviceError('device_registry_clock_unavailable')
        with self._write() as db:
            intent=self._validate(db,owner_binding,body)
            db.execute('DELETE FROM proofs WHERE expires<=?',(now,))
            if db.execute('SELECT count(*) FROM proofs').fetchone()[0]>=MAX_PENDING:
                raise MachineDeviceError('device_operation_limit')
            proof=str(now)+'.'+secrets.token_hex(32)
            db.execute('INSERT INTO proofs VALUES (?,?,?,?,?,?,?)',(_hash(proof),owner_binding,
                intent.expected_revision,intent.action,intent.fingerprint,intent.label,now+TTL_MS))
        return {'stage':self.lane,'revision':intent.expected_revision,'action':intent.action,
                'fingerprint':intent.fingerprint,'operation_proof':proof}

    def commit(self,owner_binding,body,proof):
        if not _matches(PROOF,proof):
            raise MachineDeviceError('invalid_device_operation_proof')
        now=self.clock()
        if type(now) is not int or not 0<now<10**18-TTL_MS:
            raise MachineDeviceError('device_registry_clock_unavailable')
        with self._write() as db:
            intent=self._validate(db,owner_binding,body)
            row=db.execute('SELECT owner,revision,action,target,label,expires FROM proofs WHERE digest=?',(_hash(proof),)).fetchone()
            expected=(owner_binding,intent.expected_revision,intent.action,intent.fingerprint,intent.label)
            if not row or row[:5]!=expected or not row[5]-TTL_MS<=now<row[5]:
                raise MachineDeviceError('invalid_device_operation_proof')
            if intent.action=='enroll':
                db.execute('INSERT INTO enrolled VALUES (?,?)',(intent.fingerprint,intent.label))
            else:
                db.execute('DELETE FROM enrolled WHERE fingerprint=?',(intent.fingerprint,))
            revision=secrets.token_hex(16)
            db.execute('UPDATE stage SET revision=? WHERE id=1',(revision,))
            # No proof issued before this new registry generation remains valid.
            db.execute('DELETE FROM proofs')
        return {'stage':self.lane,'revision':revision,'action':intent.action,
                'fingerprint':intent.fingerprint,'committed':True}
