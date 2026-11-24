"""New configured Base TEST authority. No legacy receiver/SQLite lane changed."""
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import sqlite3
import secrets
import time

from reusable_proof import ISSUER, REPOSITORY, OWNER_ID, b64u, obj, plain, require
from authorizer import issuer_keys

ID=r'[1-9][0-9]{0,19}'
SHA=r'[a-f0-9]{40}'
DIGEST=r'[a-f0-9]{64}'
ENV='configured-release-base-test'
CALLER=REPOSITORY+'/.github/workflows/configured-release.yml@refs/heads/main'
REUSABLE=REPOSITORY+'/.github/workflows/configured-release-reusable.yml'
RECEIVER='configured-base-test'
MAX_FRAME=20_000


@dataclass(frozen=True)
class Intent:
    run: str
    control_sha: str
    trigger: str
    tag: str
    release_id: str
    build_run_id: str
    artifact_id: str
    revision: str
    archive_sha256: str

    def __post_init__(self):
        require(all(plain(ID,x) for x in (self.run,self.release_id,self.build_run_id,self.artifact_id))
                and self.run!=self.build_run_id
                and self.trigger in ('workflow_run','workflow_dispatch')
                and plain(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?',self.tag)
                and plain(SHA,self.control_sha) and plain(SHA,self.revision)
                and plain(DIGEST,self.archive_sha256),'Invalid configured TEST intent')


@dataclass(frozen=True)
class Policy:
    reusable_sha: str
    environment_node_id: str
    vm_id: str
    repository_id: str='1260097465'

    def __post_init__(self):
        require(self.repository_id=='1260097465' and plain(SHA,self.reusable_sha)
                and plain(r'[A-Za-z0-9_=-]{4,180}',self.environment_node_id)
                and plain(r'[A-Za-z0-9.-]{1,128}',self.vm_id),'Invalid configured TEST trust policy')


def audience(policy,intent,nonce):
    require(plain(DIGEST,nonce) and policy.reusable_sha==intent.control_sha,'Invalid configured TEST control pin or nonce')
    wire=json.dumps({'schema':1,'stage':'test','profile':'base','attempt':1,
          'intent':asdict(intent),'environment':ENV,**asdict(policy),'nonce':nonce},
          sort_keys=True,separators=(',',':')).encode('ascii')
    return 'urn:assertion-memory:configured:base:test:v1:'+hashlib.sha256(wire).hexdigest()


def verify(token,keys,policy,intent,nonce,now):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding,rsa
    require(type(token) is str and 0<len(token)<=16384 and token.count('.')==2,'Invalid configured token')
    h,b,s=token.split('.')
    hdr=obj(b64u(h))
    require(hdr.get('alg')=='RS256' and not ({'jku','x5u','jwk','x5c','crit'}&hdr.keys())
            and plain(r'[A-Za-z0-9._-]{1,256}',hdr.get('kid')),'Invalid configured token header')
    matches=[x for x in keys['keys'] if x.get('kid')==hdr['kid']]
    require(len(matches)==1,'Invalid configured key match')
    key=matches[0]
    require(key.get('kty')=='RSA' and key.get('alg','RS256')=='RS256' and key.get('use','sig')=='sig',
            'Invalid configured key type')
    n,e=(int.from_bytes(b64u(key[f]),'big') for f in ('n','e'))
    require(2048<=n.bit_length()<=4096 and e==65537,'Invalid configured key parameters')
    rsa.RSAPublicNumbers(e,n).public_key().verify(b64u(s),(h+'.'+b).encode('ascii'),padding.PKCS1v15(),hashes.SHA256())
    claims=obj(b64u(b))
    allowed={'iss':ISSUER,'aud':audience(policy,intent,nonce),
        'sub':'repo:'+REPOSITORY+':environment:'+ENV,'environment':ENV,
        'environment_node_id':policy.environment_node_id,'repository':REPOSITORY,
        'repository_id':policy.repository_id,'repository_owner':'Bonzar','repository_owner_id':str(OWNER_ID),
        'workflow_ref':CALLER,'workflow_sha':intent.control_sha,'sha':intent.control_sha,
        'ref':'refs/heads/main','ref_type':'branch','run_attempt':'1','run_id':intent.run,
        'event_name':intent.trigger,'runner_environment':'github-hosted',
        'job_workflow_ref':REUSABLE+'@refs/heads/main','job_workflow_sha':policy.reusable_sha}
    require(all(claims.get(k)==v for k,v in allowed.items()),'Wrong configured Base TEST claim')
    iat,nbf,exp=(claims.get(k) for k in ('iat','nbf','exp'))
    require(all(type(v) is int for v in (iat,nbf,exp))
            and now-300<=iat<=now+15 and iat-600<=nbf<=now+15
            and now<exp and 0<exp-iat<=600,'Invalid configured token lifetime')


class Ledger:
    def __init__(self,db):
        self.db=db
        db.execute('CREATE TABLE IF NOT EXISTS challenges (nonce TEXT PRIMARY KEY,audience TEXT,expires INTEGER)')
        db.execute('CREATE TABLE IF NOT EXISTS consumed (run TEXT PRIMARY KEY)')
        db.commit()

    def issue(self,policy,intent,now):
        nonce=secrets.token_hex(32)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('DELETE FROM challenges WHERE expires<=?',(now,))
            require(self.db.execute('SELECT count(*) FROM challenges').fetchone()[0]<64,'Too many challenges')
            self.db.execute('INSERT INTO challenges VALUES (?,?,?)',(nonce,audience(policy,intent,nonce),now+180))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return nonce

    def consume(self,policy,intent,nonce,now):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            found=self.db.execute('SELECT audience,expires FROM challenges WHERE nonce=?',(nonce,)).fetchone()
            require(found and found[0]==audience(policy,intent,nonce) and now<found[1],
                    'Missing configured challenge')
            require(not self.db.execute('SELECT 1 FROM consumed WHERE run=?',(intent.run,)).fetchone(),
                    'Configured TEST run already consumed')
            self.db.execute('INSERT INTO consumed VALUES (?)',(intent.run,))
            self.db.execute('DELETE FROM challenges WHERE nonce=?',(nonce,))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise


def receive(stream,policy,ledger,deploy,*,keys=issuer_keys,clock=lambda:int(time.time())):
    header=stream.readline(MAX_FRAME+1)
    require(0<len(header)<=MAX_FRAME and header.endswith(b'\n'),'Invalid configured TEST frame')
    frame=obj(header)
    action=frame.get('op')
    allowed={'challenge':{'op','intent'},'deploy':{'op','intent','nonce','token'}}
    require(action in allowed and frame.keys()==allowed[action],'Invalid configured TEST operation')
    intent=Intent(**frame['intent'])
    if action=='challenge':
        require(stream.read(1)==b'','Unexpected challenge payload')
        return {'status':'ok','nonce':ledger.issue(policy,intent,clock())}
    nonce=frame['nonce']
    signing_keys=keys()
    verify(frame['token'],signing_keys,policy,intent,nonce,clock())
    ledger.consume(policy,intent,nonce,clock())
    # Deploy callback uses canonical TEST root and common byte verification lock.
    deploy(intent.revision,intent.archive_sha256,stream)
    return {'status':'ok','environment':'test','revision':intent.revision,'sha256':intent.archive_sha256}
