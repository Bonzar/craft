"""Disabled, no-transport Base rollback receiver authorization contract.

Caller computes the actual staged payload SHA over the same sealed bytes it
would hand to the receiver. Issuer JWKS and ReceiverPolicy must come from
independently trusted receiver configuration. No deployment is wired here.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import asdict, dataclass

from github_graph_preview import DIGEST, ID, REPO, REPO_ID, SHA, Reject, exact, product_of_tag
from rollback_rebuild_origin import RebuiltOrigin
from rollback_rebuild_promotion import RebuiltPromotion, WORKFLOW

ISSUER='https://token.actions.githubusercontent.com'
OWNER_ID='61428612'
ENVIRONMENT='configured-release-base-prod'
PREFIX='urn:assertion-memory:rollback-rebuild:base:prod:v1:'
MAX_TOKEN=16_384


@dataclass(frozen=True)
class ReceiverPolicy:
    control_sha: str
    child_ref: str
    child_sha: str
    environment_node: str
    subject: str
    audience_host: str
    actor_ids: frozenset


def audience(policy, permit, nonce):
    if (type(policy) is not ReceiverPolicy or type(permit) is not RebuiltPromotion
            or permit.kind!='rollback_rebuild_promotion'
            or type(permit.origin) is not RebuiltOrigin or permit.origin.kind!='rollback_rebuild'
            or (permit.origin.profile,permit.origin.layout)!=('base','base-v1')
            or permit.control_sha!=policy.control_sha
            or type(policy.actor_ids) is not frozenset or not policy.actor_ids):
        raise Reject('Only exact rebuilt Base PROD promotion is allowed')
    exact(SHA,policy.control_sha); exact(SHA,policy.child_sha)
    exact(DIGEST,nonce); exact(ID,permit.run_id); exact(ID,permit.test_receipt_id)
    exact(ID,permit.origin.release_id); exact(ID,permit.origin.build_run_id); exact(ID,permit.origin.artifact_id)
    exact(SHA,permit.origin.revision); exact(SHA,permit.origin.control_sha)
    exact(DIGEST,permit.origin.payload_sha); exact(DIGEST,permit.origin.archive_sha)
    if (product_of_tag(permit.origin.tag).workflow!='assertion-memory-base.yml'
            or permit.run_id==permit.origin.build_run_id):
        raise Reject('Wrong historical Base release origin')
    if (policy.subject!=f'repo:{REPO}:environment:{ENVIRONMENT}'
            or policy.child_ref!=REPO+'/.github/workflows/assertion-memory-rollback-rebuild-reusable.yml@'+policy.child_sha):
        raise Reject('Unexpected pinned receiver policy')
    exact(r'[A-Za-z0-9_=-]{4,180}',policy.environment_node)
    exact(r'[A-Za-z0-9.-]{1,128}',policy.audience_host)
    for actor in policy.actor_ids: exact(ID,actor)
    body=dict(kind='rollback_rebuild',lane='prod',repository_id=REPO_ID,
              environment=ENVIRONMENT,environment_node=policy.environment_node,
              vm=policy.audience_host,caller=REPO+'/'+WORKFLOW+'@refs/heads/main',
              child=policy.child_ref,permit=asdict(permit),nonce=nonce,attempt=1)
    return PREFIX+hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode('ascii')).hexdigest()


def _bytes(text):
    exact(r'[A-Za-z0-9_-]+',text)
    return base64.urlsafe_b64decode(text+'='*(-len(text)%4))


def _object(data):
    def unique(items):
        out={}
        for key,value in items:
            if key in out: raise Reject('Duplicate token field')
            out[key]=value
        return out
    value=json.loads(data,object_pairs_hook=unique)
    if type(value) is not dict: raise Reject('Token JSON must be an object')
    return value


def verify(token, trusted_jwks, policy, permit, nonce, observed_payload_sha, now):
    """Verify GitHub signed claims and observed bytes before any ledger use."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding,rsa

    expected_aud=audience(policy,permit,nonce)
    exact(DIGEST,observed_payload_sha)
    if not hmac.compare_digest(observed_payload_sha,permit.origin.payload_sha):
        raise Reject('Rebuilt payload differs from same-run TEST')
    if type(token) is not str or len(token)>MAX_TOKEN or token.count('.')!=2:
        raise Reject('Invalid GitHub rollback token')
    try:
        header,payload,signature=token.split('.')
        meta=_object(_bytes(header))
        if meta.get('alg')!='RS256' or set(meta)&{'jku','x5u','jwk','x5c','crit'}:
            raise Reject('Unexpected rollback token signature header')
        kid=exact(r'[A-Za-z0-9._-]{1,256}',meta.get('kid'))
        candidates=[item for item in trusted_jwks['keys'] if item.get('kid')==kid]
        if len(candidates)!=1:
            raise Reject('Unknown or ambiguous rollback signer')
        key=candidates[0]
        if key.get('kty')!='RSA' or key.get('alg','RS256')!='RS256' or key.get('use','sig')!='sig':
            raise Reject('Unexpected rollback signing key')
        n,e=(int.from_bytes(_bytes(key[field]),'big') for field in ('n','e'))
        if not 2048<=n.bit_length()<=4096 or e!=65537:
            raise Reject('Invalid rollback RSA key')
        rsa.RSAPublicNumbers(e,n).public_key().verify(_bytes(signature),(header+'.'+payload).encode('ascii'),
                                                     padding.PKCS1v15(),hashes.SHA256())
        claims=_object(_bytes(payload))
    except (ValueError,KeyError,TypeError,InvalidSignature):
        raise Reject('Invalid signed GitHub rollback token') from None
    expected=dict(iss=ISSUER,aud=expected_aud,sub=policy.subject,repository=REPO,repository_id=str(REPO_ID),
                  repository_owner='Bonzar',repository_owner_id=OWNER_ID,
                  workflow_ref=REPO+'/'+WORKFLOW+'@refs/heads/main',workflow_sha=policy.control_sha,
                  job_workflow_ref=policy.child_ref,job_workflow_sha=policy.child_sha,sha=policy.control_sha,
                  ref='refs/heads/main',ref_type='branch',environment=ENVIRONMENT,
                  environment_node_id=policy.environment_node,runner_environment='github-hosted',
                  event_name='workflow_dispatch',run_attempt='1',run_id=permit.run_id)
    if (not all(claims.get(k)==v for k,v in expected.items()) or type(claims.get('actor_id')) is not str
            or claims['actor_id'] not in policy.actor_ids):
        raise Reject('Unexpected rollback caller or lane')
    iat,nbf,exp=(claims.get(key) for key in ('iat','nbf','exp'))
    if (not all(type(value) is int for value in (iat,nbf,exp)) or not now-300<=iat<=now+15
            or not iat-600<=nbf<=now+15 or not now<exp or not 0<exp-iat<=600):
        raise Reject('Expired or invalid GitHub rollback token')


class OneUse:
    """SQLite DB must be durable, receiver-owned, and unwritable by sender UID."""
    def __init__(self, db):
        self.db=db
        db.execute('CREATE TABLE IF NOT EXISTS rebuild_challenge (nonce TEXT PRIMARY KEY,audience TEXT NOT NULL,expires INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS rebuild_consumed (run_scope TEXT PRIMARY KEY)')
        db.commit()

    def issue(self,policy,permit,now):
        nonce=secrets.token_hex(32)
        aud=audience(policy,permit,nonce)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('DELETE FROM rebuild_challenge WHERE expires<=?',(now,))
            if self.db.execute('SELECT count(*) FROM rebuild_challenge').fetchone()[0]>=64:
                raise Reject('Rollback challenge capacity reached')
            self.db.execute('INSERT INTO rebuild_challenge VALUES (?,?,?)',(nonce,aud,now+180))
            self.db.commit()
        except BaseException:
            self.db.rollback(); raise
        return nonce

    def consume(self,token,trusted_jwks,policy,permit,nonce,observed_payload_sha,now):
        verify(token,trusted_jwks,policy,permit,nonce,observed_payload_sha,now)
        expected=audience(policy,permit,nonce)
        scope=f'{REPO_ID}:{ENVIRONMENT}:{policy.audience_host}:{permit.run_id}:1'
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT audience,expires FROM rebuild_challenge WHERE nonce=?',(nonce,)).fetchone()
            if row is None or not hmac.compare_digest(row[0],expected) or now>=row[1]:
                raise Reject('Unknown, expired or consumed rollback challenge')
            if self.db.execute('SELECT 1 FROM rebuild_consumed WHERE run_scope=?',(scope,)).fetchone() is not None:
                raise Reject('Rollback run already consumed')
            self.db.execute('INSERT INTO rebuild_consumed VALUES (?)',(scope,))
            self.db.execute('DELETE FROM rebuild_challenge WHERE nonce=?',(nonce,))
            self.db.commit()
        except BaseException:
            self.db.rollback(); raise
        return permit
