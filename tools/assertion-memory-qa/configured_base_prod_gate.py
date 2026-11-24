"""Configured Base PROD: separate gate; existing PROD writer only after authorization."""
from dataclasses import asdict, dataclass
import hmac
import hashlib
import json
import re
import secrets
import time

from reusable_proof import DIGEST, ID, ISSUER, OWNER_ID, REF, REPOSITORY, SHA, b64u, obj, plain, require
from configured_base import BASE
from configured_provider_checks import Content, Original, Promotion, signed_audience

WRAPPER=REPOSITORY+'/.github/workflows/configured-base-release.yml@refs/heads/main'

def intent_document(promotion):
    require(promotion.profile is BASE, 'Expected fixed Base profile')
    return {k:v for k,v in asdict(promotion).items() if k!='profile'}

def parse_intent(doc):
    require(type(doc) is dict and set(doc)=={'release','content','control_sha','run_id','test_receipt_id','trigger'},'Invalid intent')
    release=Original(**doc['release'])
    require(re.fullmatch(r'assertion-memory-v[0-9][A-Za-z0-9._-]{0,95}',release.tag) is not None,'Wrong Base tag')
    return Promotion(BASE,release,doc['control_sha'],doc['run_id'],doc['test_receipt_id'],Content(**doc['content']),doc['trigger'])

@dataclass(frozen=True)
class Policy:
    repository_id: str
    control_sha: str
    vm_id: str
    subject: str
    environment_node_id: str
    actor_ids: frozenset
    mode: str='pilot'

    def __post_init__(self):
        require(self.mode in ('pilot','pinned_wrapper') and plain(ID,self.repository_id) and plain(SHA,self.control_sha)
                and self.subject.endswith(':environment:'+BASE.prod.environment)
                and type(self.actor_ids) is frozenset and bool(self.actor_ids)
                and all(plain(ID,x) for x in self.actor_ids),'Invalid PROD policy')

def audience(policy,promotion,nonce):
    require(promotion.profile is BASE and plain(DIGEST,nonce)
            and (policy.mode=='pinned_wrapper' or promotion.control_sha==policy.control_sha),'Unreviewed control commit')
    if policy.mode=='pinned_wrapper':
        wire=json.dumps({'schema':2,'stage':'prod','profile':'base','attempt':1,'caller':WRAPPER,
              'child':BASE.reusable_path+'@'+policy.control_sha,'intent':intent_document(promotion),
              'environment':BASE.prod.environment,'vm':policy.vm_id,'environment_node_id':policy.environment_node_id,
              'repository_id':policy.repository_id,'nonce':nonce},
              sort_keys=True,separators=(',',':')).encode('ascii')
        return 'urn:assertion-memory:configured:base:promote:v2:'+hashlib.sha256(wire).hexdigest()
    return signed_audience(promotion,vm_id=policy.vm_id,environment_node_id=policy.environment_node_id,
                           reusable_sha=policy.control_sha,nonce=nonce)

def claims_policy(claims,policy,promotion,nonce,now):
    expected={'iss':ISSUER,'aud':audience(policy,promotion,nonce),'sub':policy.subject,
              'repository':REPOSITORY,'repository_id':policy.repository_id,'repository_owner':'Bonzar',
              'repository_owner_id':str(OWNER_ID),'workflow_ref':WRAPPER if policy.mode=='pinned_wrapper' else BASE.caller,
              'workflow_sha':promotion.control_sha,
              'job_workflow_ref':BASE.reusable_path+'@'+(policy.control_sha if policy.mode=='pinned_wrapper' else REF),'job_workflow_sha':policy.control_sha,
              'sha':promotion.control_sha,'ref':REF,'ref_type':'branch','environment':BASE.prod.environment,
              'environment_node_id':policy.environment_node_id,'event_name':promotion.trigger,
              'run_attempt':'1','run_id':promotion.run_id,'runner_environment':'github-hosted'}
    require(all(claims.get(k)==v for k,v in expected.items()) and claims.get('actor_id') in policy.actor_ids,
            'Unexpected configured PROD claim')
    iat,nbf,exp=(claims.get(k) for k in ('iat','nbf','exp'))
    require(all(type(v) is int for v in (iat,nbf,exp)) and now-300<=iat<=now+15
            and iat-600<=nbf<=now+15 and now<exp and 0<exp-iat<=600,'Expired configured PROD token')

def verify(token,issuer_keys,policy,promotion,nonce,now):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding,rsa
    require(isinstance(token,str) and len(token)<=16_384 and token.count('.')==2,'Invalid JWT')
    h,b,s=token.split('.')
    header=obj(b64u(h))
    require(header.get('alg')=='RS256' and not ({'jku','x5u','jwk','x5c','crit'} & header.keys())
            and plain(r'[A-Za-z0-9._-]{1,256}', header.get('kid')),'Invalid header')
    keys=[k for k in issuer_keys['keys'] if k.get('kid')==header['kid']]
    require(len(keys)==1,'Unexpected issuer key')
    k=keys[0]
    require(k.get('kty')=='RSA' and k.get('alg','RS256')=='RS256' and k.get('use','sig')=='sig','Invalid issuer key')
    n,e=(int.from_bytes(b64u(k[f]),'big') for f in ('n','e'))
    require(2048<=n.bit_length()<=4096 and e==65537,'Invalid signing key parameters')
    rsa.RSAPublicNumbers(e,n).public_key().verify(b64u(s),(h+'.'+b).encode('ascii'),padding.PKCS1v15(),hashes.SHA256())
    claims_policy(obj(b64u(b)),policy,promotion,nonce,now)

def permit(p):
    return {'status':'ok','environment':'production','revision':p.release.revision,'sha256':p.content.sha256}

class Ledger:
    def __init__(self,db,verify_token=verify):
        self.db,self.verify_token=db,verify_token
        db.execute('CREATE TABLE IF NOT EXISTS challenges (nonce TEXT PRIMARY KEY,audience TEXT NOT NULL,expires INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS consumed_runs (run TEXT PRIMARY KEY)')
        db.commit()

    def issue(self,policy,p,now):
        nonce=secrets.token_hex(32)
        aud=audience(policy,p,nonce)
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            self.db.execute('DELETE FROM challenges WHERE expires<=?',(now,))
            require(self.db.execute('SELECT count(*) FROM challenges').fetchone()[0]<64,'Challenge capacity')
            require(not self.db.execute('SELECT 1 FROM consumed_runs WHERE run=?',(p.run_id,)).fetchone(),'Already consumed output')
            self.db.execute('INSERT INTO challenges VALUES(?,?,?)',(nonce,aud,now+180))
        return {'status':'ok','nonce':nonce}

    def confirm(self,policy,p,nonce,token,keys,now):
        aud=audience(policy,p,nonce)
        self.verify_token(token,keys,policy,p,nonce,now)
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            row=self.db.execute('SELECT audience,expires FROM challenges WHERE nonce=?',(nonce,)).fetchone()
            require(row is not None and hmac.compare_digest(row[0],aud) and now<row[1],'Challenge denied')
            require(not self.db.execute('SELECT 1 FROM consumed_runs WHERE run=?',(p.run_id,)).fetchone(),'Already consumed output')
            self.db.execute('INSERT INTO consumed_runs VALUES(?)',(p.run_id,))
            self.db.execute('DELETE FROM challenges WHERE nonce=?',(nonce,))
        return permit(p)

def read_frame(stream):
    wire=stream.readline(20_001)
    require(0<len(wire)<=20_000 and wire.endswith(b'\n'),'Invalid authorization frame')
    return obj(wire)

def request(frame):
    require(frame.get('op') in ('challenge','confirm'),'Invalid operation')
    challenge=frame['op']=='challenge'
    require(set(frame)==({'op','intent'} if challenge else {'op','intent','nonce','token'}),'Invalid fields')
    p=parse_intent(frame['intent'])
    if not challenge:
        require(plain(DIGEST,frame['nonce']) and isinstance(frame['token'],str)
                and len(frame['token'])<=16384,'Invalid proof')
    return p,challenge

def receive(stream,authorize,activate_exact):
    frame=read_frame(stream)
    p,challenge=request(frame)
    if challenge:
        require(stream.read(1)==b'','Unexpected challenge payload')
    response=authorize(frame)
    if challenge:
        require(set(response)=={'status','nonce'} and response['status']=='ok' and plain(DIGEST,response['nonce']),'Challenge denied')
        return response
    require(response==permit(p),'Production authorization denied')
    result=activate_exact(p.release.revision,p.content.sha256,stream)
    require(result==permit(p),'Wrong activation receipt')
    return result

def serve_peer(sock,policy,ledger,writer_uid,peer_uid,issuer_keys,now=lambda:int(time.time())):
    try:
        require(peer_uid(sock)==writer_uid,'Unexpected receiver UID')
        sock.settimeout(4)
        with sock.makefile('rb') as f:
            frame=read_frame(f)
        p,challenge=request(frame)
        response=(ledger.issue(policy,p,now()) if challenge else
                  ledger.confirm(policy,p,frame['nonce'],frame['token'],issuer_keys(),now()))
    except Exception:
        response={'status':'rejected'}
    sock.sendall(json.dumps(response,separators=(',',':')).encode('ascii')+b'\n')
