"""Trusted owner-verifier request proof and single-use SQLite pause transition."""
from dataclasses import dataclass
from hashlib import sha256
import hmac
import re
import time

from .processing_control import REVISION, set_control

PATH = '/api/v1/queue-pause'
ENVELOPE = (b'x-memory-pause-owner', b'x-memory-pause-issued-at',
            b'x-memory-pause-expires-at', b'x-memory-pause-nonce',
            b'x-memory-pause-signature')
HEX = re.compile(r'[0-9a-f]{64}\Z')
NONCE = re.compile(r'[0-9a-f]{32}\Z')
UNIX = re.compile(r'[1-9][0-9]{9}\Z')
TTL = 15


class ProofRejected(ValueError):
    pass


@dataclass(frozen=True)
class ProvenOwner:
    owner: str
    revision: str
    issued_at: int
    expires_at: int
    nonce: str
    lane: str = 'test'


def canonical(body, p):
    return ('\n'.join(('memory-queue-pause-v1', 'PUT', PATH, p.lane,
                      sha256(body).hexdigest(), p.owner, p.revision,
                      str(p.issued_at), str(p.expires_at), p.nonce))+'\n').encode('ascii')


def _fresh(p, now):
    return p.expires_at == p.issued_at + TTL and p.issued_at <= now < p.expires_at


def make_owner_verifier(secret, owner, *, lane='test', clock=time.time):
    if (lane not in ('test','prod') or type(secret) is not bytes or len(secret) != 32
            or type(owner) is not str or not HEX.fullmatch(owner)):
        raise ValueError('Invalid TEST owner proof authority')

    async def verify(scope, body, revision):
        if scope.get('method') != 'PUT' or scope.get('path') != PATH or not REVISION.fullmatch(revision):
            raise ProofRejected()
        headers = []
        for key in ENVELOPE:
            hits = [v for k, v in scope.get('headers', ()) if k.lower() == key]
            if len(hits) != 1 or len(hits[0]) > 64:
                raise ProofRejected()
            try:
                headers.append(hits[0].decode('ascii'))
            except UnicodeError as exc:
                raise ProofRejected() from exc
        who, issued, expires, nonce, sig = headers
        if (not HEX.fullmatch(who) or not hmac.compare_digest(who, owner)
                or not UNIX.fullmatch(issued) or not UNIX.fullmatch(expires)
                or not NONCE.fullmatch(nonce) or not HEX.fullmatch(sig)):
            raise ProofRejected()
        proof = ProvenOwner(who, revision, int(issued), int(expires), nonce, lane)
        if not _fresh(proof, int(clock())) or not hmac.compare_digest(
                hmac.digest(secret, canonical(body, proof), 'sha256').hex(), sig):
            raise ProofRejected()
        return proof
    return verify


def apply_once(db, *, stage, paused, expected_revision, proof, clock=time.time):
    """Caller must hold BEGIN IMMEDIATE and commit only after successful result."""
    if (stage not in ('test','prod') or type(proof) is not ProvenOwner or proof.lane != stage or not db.in_transaction
            or proof.revision != expected_revision or not _fresh(proof, int(clock()))):
        raise ProofRejected()
    db.execute('CREATE TABLE IF NOT EXISTS memory_queue_pause_nonce '
               '(nonce TEXT PRIMARY KEY, expires INTEGER NOT NULL)')
    # Leave a margin against small wall-clock adjustments across workers.
    db.execute('DELETE FROM memory_queue_pause_nonce WHERE expires < ?', (int(clock())-300,))
    used = db.execute('INSERT INTO memory_queue_pause_nonce VALUES (?,?) '
                      'ON CONFLICT(nonce) DO NOTHING', (proof.nonce, proof.expires_at))
    if used.rowcount != 1:
        raise ProofRejected()
    state, revision = set_control(db, paused=paused, expected_revision=expected_revision)
    return dict(stage=stage, paused=state, revision=revision)
