"""Additive v3 signed admission, deliberately absent from default dispatch."""
from hashlib import sha256
import json
from configured_base_prod_gate import audience, claims_policy, verified_jwt_claims
from transition_witness import Admission, Release

def signed_audience(policy,promotion,nonce,candidate):
    if (type(candidate) is not Release or candidate.revision!=promotion.release.revision
            or candidate.archive!=promotion.content.sha256):
        raise ValueError('unexpected exact terminal candidate')
    inherited=audience(policy,promotion,nonce)
    wire=json.dumps({
        'legacy_authority':inherited,
        'tag':promotion.release.tag,
        'run':promotion.run_id,'nonce':nonce,
        'candidate':{'revision':candidate.revision,'archive':candidate.archive,
                     'manifest':candidate.manifest,'mode':candidate.mode}
    },sort_keys=True,separators=(',',':')).encode('ascii')
    return 'urn:assertion-memory:configured:base:terminal:v3:'+sha256(wire).hexdigest()

def verify_admission(token,issuer_keys,policy,promotion,nonce,candidate,now):
    # Check immutable v3 content *before* delegating the preserved v1/v2
    # issuer/workflow/actor/timing identity constraints. No unsigned legacy
    # audience is accepted as authorization for a terminal transaction.
    claims=verified_jwt_claims(token,issuer_keys)
    if claims.get('aud')!=signed_audience(policy,promotion,nonce,candidate):
        raise ValueError('wrong sealed Base terminal audience')
    claims_policy(dict(claims,aud=audience(policy,promotion,nonce)),policy,promotion,nonce,now)
    return Admission(promotion.run_id,nonce,candidate)
