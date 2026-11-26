"""Exact managed Nginx/Gateway pair observation and inverse fixture logic.

No provider access or writes: the already authorized protected edge operator
supplies canonical semantic digests and independent route/owner readback.
"""
from dataclasses import dataclass
import re
from transition_witness import Release

HEX=re.compile(r'[0-9a-f]{64}\Z')

@dataclass(frozen=True)
class Edge:
    nginx: str
    gateway: str
    def __post_init__(self):
        if any(type(x) is not str or not HEX.fullmatch(x) for x in (self.nginx,self.gateway)):
            raise ValueError('invalid verified edge identity')

@dataclass(frozen=True)
class Pair:
    prior_release: Release
    candidate_release: Release
    prior: Edge
    candidate: Edge
    def __post_init__(self):
        if (type(self.prior_release) is not Release or type(self.candidate_release) is not Release
                or type(self.prior) is not Edge or type(self.candidate) is not Edge
                or self.candidate_release==self.prior_release or self.candidate==self.prior):
            raise ValueError('requires independently reviewed exact edge/app pair')

def verify_edge_state(pair,*,phase,current_release, observed):
    if type(pair) is not Pair or type(observed) is not Edge:
        raise ValueError('reviewed exact Base edge transition required')
    if phase=='candidate':
        expected=(pair.candidate_release,pair.candidate)
    elif phase=='restore':
        expected=(pair.prior_release,pair.prior)
    else:
        raise ValueError('unknown fixed paired operation')
    if (current_release,observed)!=expected:
        raise ValueError('edge or immutable release differs from approved pair')
    return True

def required_inverse(pair,*,observed):
    if type(pair) is not Pair or type(observed) is not Edge:
        raise ValueError('reviewed exact Base edge inverse required')
    if observed.nginx not in (pair.prior.nginx,pair.candidate.nginx) or observed.gateway not in (
            pair.prior.gateway,pair.candidate.gateway):
        raise ValueError('foreign/different live edge; do not overwrite')
    # Existing published outer route is reverted first, before predecessor's
    # underlying ingress/runtime can be restored.
    return tuple(name for name in ('gateway','nginx')
                 if getattr(observed,name)!=getattr(pair.prior,name))
