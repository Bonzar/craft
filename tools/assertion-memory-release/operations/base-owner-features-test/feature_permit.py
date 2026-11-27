"""Versioned TEST feature permit; existing trustworthy signature verifier is required."""
from dataclasses import asdict, dataclass
import json
import re

from transaction import FEATURES, Refused, Request, digest, execute, valid

PURPOSE = 'assertion-memory:test-owner-feature-operation:v1'
DOMAIN = b'assertion-memory\x00test-owner-feature-operation\x00v1\x00'
MAX_AGE = 120
RUN = re.compile(r'[1-9][0-9]{0,19}\Z')


@dataclass(frozen=True)
class Permit:
    version: int
    purpose: str
    stage: str
    feature: str
    action: str
    receipt_id: str
    request_sha256: str
    candidate_sha256: str
    protected_run: str
    issued_at: int
    expires_at: int

    def signed_bytes(self):
        return DOMAIN + json.dumps(asdict(self), sort_keys=True, separators=(',', ':'),
                                   ensure_ascii=True, allow_nan=False).encode('ascii')


def authorize(permit, signature, request, candidate, *, clock, verify_existing):
    """Verify exact typed request using only existing approved TEST trust.

    The verifier is installed configuration, not selected by the caller. It
    determines the allowed issuer/actor/workflow/run, and verifies the
    signature over these exact domain-separated bytes for that TEST run.
    """
    if (type(permit) is not Permit or type(request) is not Request or type(signature) is not bytes
            or not 1 <= len(signature) <= 8192 or type(candidate) is not bytes
            or not 0 < len(candidate) <= 8192 or not callable(verify_existing) or not callable(clock)):
        raise Refused('missing_typed_test_feature_permit')
    now = clock()
    if (type(permit.version) is not int or permit.version != 1 or permit.purpose != PURPOSE
            or permit.stage != 'test' or request.stage != 'test'
            or permit.feature not in FEATURES or permit.feature != request.feature
            or permit.action not in ('apply', 'inverse') or permit.action != request.operation
            or not valid(permit.receipt_id) or permit.receipt_id != request.receipt_id
            or not valid(permit.request_sha256) or permit.request_sha256 != digest(request.canonical())
            or not valid(permit.candidate_sha256) or permit.candidate_sha256 != request.candidate_digest
            or permit.candidate_sha256 != digest(candidate) or not valid(permit.protected_run, RUN)
            or any(type(n) is not int for n in (now, permit.issued_at, permit.expires_at))
            or not 0 <= permit.issued_at <= now < permit.expires_at
            or permit.expires_at - permit.issued_at > MAX_AGE):
        raise Refused('wrong_or_expired_test_feature_permit')
    if verify_existing(permit.signed_bytes(), signature, protected_run=permit.protected_run) is not True:
        raise Refused('test_feature_signature_rejected')
    return True


def apply_with_permit(permit, signature, request, candidate, *, clock, verify_existing, runner):
    # execute() consumes the receipt under the existing TEST deployment lease.
    # Its callback accepts only the exact request already signed by this permit.
    def approval(receipt_id, request_sha256):
        if (type(permit) is not Permit or receipt_id != permit.receipt_id
                or request_sha256 != permit.request_sha256):
            return False
        authorize(permit, signature, request, candidate, clock=clock, verify_existing=verify_existing)
        return True
    return execute(request, candidate, approval, runner)
