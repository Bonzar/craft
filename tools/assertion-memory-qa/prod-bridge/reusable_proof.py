"""Offline policy candidate only. No network, deployment or live credentials."""
import base64
from dataclasses import asdict, dataclass
import hashlib
import json
import re


DIGEST = r'[a-f0-9]{64}'
ENVIRONMENT = 'assertion-memory-production'
ID = r'[1-9][0-9]{0,19}'
ISSUER = 'https://token.actions.githubusercontent.com'
OWNER_ID = 61428612
REF = 'refs/heads/main'
REPOSITORY = 'Bonzar/craft'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def plain(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


SHA = r'[a-f0-9]{40}'
CALLER = f'{REPOSITORY}/.github/workflows/assertion-memory-base.yml@{REF}'
REUSABLE_PATH = f'{REPOSITORY}/.github/workflows/assertion-memory-authorize-promotion.yml'


def b64u(value):
    require(plain(r'[A-Za-z0-9_-]+', value), 'Invalid encoding')
    result = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    require(base64.urlsafe_b64encode(result).decode().rstrip('=') == value, 'Noncanonical encoding')
    return result


def obj(data):
    def no_duplicate(pairs):
        result = dict(pairs)
        require(len(result) == len(pairs), 'Duplicate members')
        return result
    value = json.loads(data, object_pairs_hook=no_duplicate)
    require(type(value) is dict, 'Expected object')
    return value


@dataclass(frozen=True)
class Intent:
    run_id: str
    test_run_id: str
    test_receipt_id: str
    release_id: str
    build_run_id: str
    artifact_id: str
    revision: str
    sha256: str

    def __post_init__(self):
        require(all(plain(ID, field) for field in
                    (self.run_id, self.test_run_id, self.test_receipt_id,
                     self.release_id, self.build_run_id, self.artifact_id))
                and len({self.run_id,self.test_run_id,self.build_run_id}) == 3
                and plain(SHA,self.revision) and plain(DIGEST,self.sha256), 'Invalid release intent')


@dataclass(frozen=True)
class Policy:
    repository_id: str
    reusable_sha: str
    vm_id: str
    subject: str
    environment_node_id: str
    actor_ids: frozenset[str]

    def __post_init__(self):
        require(plain(ID,self.repository_id) and plain(SHA,self.reusable_sha)
                and plain(r'[a-zA-Z0-9.-]{1,128}',self.vm_id)
                and plain(r'[^\r\n]{1,300}',self.subject)
                and self.subject.endswith(':environment:' + ENVIRONMENT)
                and plain(r'[A-Za-z0-9_=-]{4,180}',self.environment_node_id)
                and bool(self.actor_ids) and all(plain(ID,a) for a in self.actor_ids),
                'Invalid root trust policy')


def audience(policy, intent, nonce):
    require(plain(DIGEST,nonce), 'Invalid nonce')
    message = dict(asdict(intent), nonce=nonce, vm=policy.vm_id, repository_id=policy.repository_id,
                   environment=ENVIRONMENT, environment_node_id=policy.environment_node_id, attempt=1)
    wire = json.dumps(message, sort_keys=True, separators=(',', ':')).encode('ascii')
    return 'urn:assertion-memory:promote:v2:' + hashlib.sha256(wire).hexdigest()


def verify(token, issuer_keys, policy, intent, nonce, now):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    # issuer_keys can only come from the fixed, independently authenticated issuer endpoint.
    require(isinstance(token,str) and len(token)<=16_384 and token.count('.')==2, 'Invalid token')
    h,b,s = token.split('.')
    header = obj(b64u(h))
    require(header.get('alg')=='RS256' and not ({'jku','x5u','jwk','x5c','crit'} & header.keys()), 'Invalid JWT header')
    kid=header.get('kid')
    require(plain(r'[A-Za-z0-9._-]{1,256}',kid),'Invalid key identifier')
    keys=[k for k in issuer_keys['keys'] if k.get('kid')==kid]
    require(len(keys)==1,'Unknown or ambiguous issuer key')
    k=keys[0]
    require(k.get('kty')=='RSA' and k.get('alg','RS256')=='RS256' and k.get('use','sig')=='sig','Invalid signing key type')
    n,e=(int.from_bytes(b64u(k[f]),'big') for f in ('n','e'))
    require(2048<=n.bit_length()<=4096 and e==65537,'Invalid signing key parameters')
    rsa.RSAPublicNumbers(e,n).public_key().verify(b64u(s),(h+'.'+b).encode('ascii'),padding.PKCS1v15(),hashes.SHA256())
    claims=obj(b64u(b))
    expected = {'iss':ISSUER,'aud':audience(policy,intent,nonce),'sub':policy.subject,
                'repository':REPOSITORY,'repository_id':policy.repository_id,
                'repository_owner':'Bonzar','repository_owner_id':str(OWNER_ID),
                'workflow_ref':CALLER,'ref':REF,'ref_type':'branch',
                'job_workflow_ref':REUSABLE_PATH+'@'+policy.reusable_sha,'job_workflow_sha':policy.reusable_sha,
                'environment':ENVIRONMENT,'environment_node_id':policy.environment_node_id,'event_name':'workflow_dispatch','run_attempt':'1',
                'run_id':intent.run_id,'runner_environment':'github-hosted'}
    require(all(claims.get(k)==v for k,v in expected.items())
            and claims.get('actor_id') in policy.actor_ids
            and plain(SHA,claims.get('workflow_sha')) and claims.get('sha')==claims['workflow_sha'],
            'Unexpected workflow identity or release audience')
    iat,nbf,exp=(claims.get(k) for k in ('iat','nbf','exp'))
    require(all(type(v) is int for v in (iat,nbf,exp))
            and now-300<=iat<=now+15 and iat-60<=nbf<=now+15 and now<exp and 0<exp-iat<=600,
            'Expired or invalid token timing')


class OneUse:
    """Used only by dedicated authorizer, with a DB not writable by deploy UID."""

    def __init__(self, db):
        self.db=db
        db.execute('CREATE TABLE IF NOT EXISTS challenge '
                   '(nonce TEXT PRIMARY KEY, audience TEXT NOT NULL, expires INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS consumed '
                   '(run_scope TEXT PRIMARY KEY, expires INTEGER NOT NULL)')
        db.commit()

    def issue(self, policy, intent, now):
        import secrets
        nonce=secrets.token_hex(32)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('DELETE FROM challenge WHERE expires<=?',(now,))
            if self.db.execute('SELECT count(*) FROM challenge LIMIT 1').fetchone()[0]>=64:
                raise ValueError('Challenge capacity reached')
            # No durable use marker until a real signed proof is received.
            self.db.execute('INSERT INTO challenge VALUES (?,?,?)',(nonce,audience(policy,intent,nonce),now+180))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return nonce

    def verify_and_consume(self, token, keys, policy, intent, nonce, now):
        verify(token,keys,policy,intent,nonce,now)
        scope=f'{policy.vm_id}:{policy.repository_id}:{intent.run_id}:1'
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT audience,expires FROM challenge WHERE nonce=?',(nonce,)).fetchone()
            require(row is not None and row[0]==audience(policy,intent,nonce) and now<row[1],
                    'Expired, mismatched or already consumed challenge')
            # Retain exactly-one attempt marker until the run cannot mint another
            # token under the platform's first-attempt policy. No automatic pruning yet.
            require(self.db.execute('SELECT 1 FROM consumed WHERE run_scope=?',(scope,)).fetchone() is None,
                    'Production run already consumed')
            self.db.execute('INSERT INTO consumed VALUES (?,?)',(scope,now))
            self.db.execute('DELETE FROM challenge WHERE nonce=?',(nonce,))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return {'environment':'production','revision':intent.revision,'sha256':intent.sha256}
