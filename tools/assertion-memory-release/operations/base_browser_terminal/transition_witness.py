"""Offline model of authorizer-owned transition lookup, not a socket endpoint."""
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class Release:
    revision: str
    archive: str
    manifest: str
    mode: str
    def __post_init__(self):
        for field,width in ((self.revision,40),(self.archive,64),(self.manifest,64)):
            if type(field) is not str or not re.fullmatch('[0-9a-f]{%d}' % width,field):
                raise ValueError('invalid sealed release')
        if self.mode not in ('legacy','sidecar'):
            raise ValueError('invalid browser mode')

@dataclass(frozen=True)
class Admission:
    # Constructed ONLY by the existing verified OIDC signature/one-use owner
    # authorization path. This code does not itself verify a JWT.
    run: str
    nonce: str
    candidate: Release
    lane: str='prod'
    def __post_init__(self):
        if (self.lane!='prod' or not re.fullmatch('[1-9][0-9]{0,19}',self.run)
                or not re.fullmatch('[0-9a-f]{64}',self.nonce) or type(self.candidate) is not Release):
            raise ValueError('invalid protected admission')

class WitnessState:
    """Single-writer authorizer state: never owned or mutable by deploy UID."""
    def __init__(self, seeded):
        if type(seeded) is not Release:
            raise ValueError('previous release requires protected baseline')
        self.active=seeded
        self.pending=None
        self.spent=set()

    def consume(self, verified):
        if (type(verified) is not Admission or self.pending is not None
                or verified.run in self.spent or verified.candidate==self.active):
            raise ValueError('nonfresh protected run')
        self.pending=(verified,self.active)
        self.spent.add(verified.run)

    def lookup(self, *, peer_is_verified_writer, run, nonce, phase, observed):
        if peer_is_verified_writer is not True or self.pending is None or type(observed) is not Release:
            raise ValueError('no protected pending witness')
        verified,previous=self.pending
        expected={'restart':verified.candidate,'restore':previous}.get(phase)
        if run!=verified.run or nonce!=verified.nonce or expected!=observed:
            raise ValueError('wrong run, phase, or physical release')
        return expected

    def settle(self, *, verified_terminal):
        # This entrypoint is NOT exposed on the deployment UID socket.
        # Existing infrastructure has yet to furnish this independent input.
        if self.pending is None or type(verified_terminal) is not Release:
            raise ValueError('missing verified settlement')
        admission,old=self.pending
        if verified_terminal not in (old,admission.candidate):
            raise ValueError('unknown terminal physical release')
        self.active=verified_terminal
        self.pending=None
