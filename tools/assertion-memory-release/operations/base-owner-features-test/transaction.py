"""TEST-only interface rehearsal. No host implementation or authority."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re

TEST_ROOT = '/srv/assertion-release/test'
GRAPH = 'assertion-test-deploy-graph.service'
MCP = 'assertion-test-deploy-mcp.service'
GRAPH_FLAG = f'/etc/systemd/user/{GRAPH}.d/zzzzzz-graph-node-removal-test-v1.conf'
DEVICE_FLAG = '/etc/assertion-release/test/machine-device-registry-v1.enabled'
REGISTRY = TEST_ROOT + '/state/machine-devices/registry.sqlite3'
DEVICE_BYTES = b'assertion-memory-owner-machine-devices-v1\n'
HEX = re.compile(r'[a-f0-9]{64}\Z')
REV = re.compile(r'[a-f0-9]{40}\Z')
FEATURES = {'graph': (GRAPH_FLAG, (GRAPH,)), 'devices': (DEVICE_FLAG, (MCP, GRAPH))}


class Refused(ValueError):
    pass


def digest(data):
    return sha256(data).hexdigest()


def valid(value, pattern=HEX):
    return type(value) is str and pattern.fullmatch(value) is not None


@dataclass(frozen=True)
class Request:
    stage: str
    feature: str
    operation: str
    revision: str
    archive: str
    visual_manifest: str
    baseline_chain: str
    receipt_id: str
    existing_digest: str | None
    candidate_digest: str
    registry_identity: str | None

    def canonical(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(',', ':')).encode('ascii')


@dataclass(frozen=True)
class Observed:
    stage: str
    current: str
    visual_manifest: str
    baseline_chain: str
    existing_digest: str | None
    registry_identity: str | None
    feature_authority_ready: bool


def execute(req: Request, candidate: bytes, protected_review, runner):
    """A trusted runner must implement a fenced exact transaction or fail closed.

    protected_review validates the immutable receipt and entire request/candidate
    hash against pre-existing TEST authority; caller data conveys no authority.
    begin() claims the receipt durably before effects. A raised operation must
    leave an uncertain claim, never be retried, and require trusted readback.
    """
    if (type(req) is not Request or req.stage != 'test' or req.feature not in FEATURES
            or req.operation not in ('apply','inverse') or not valid(req.revision, REV)
            or not all(valid(x) for x in (req.archive,req.visual_manifest,req.baseline_chain,
                                         req.receipt_id,req.candidate_digest))
            or req.registry_identity is not None and not valid(req.registry_identity)
            or req.feature=='graph' and req.registry_identity is not None
            or req.existing_digest != (None if req.operation=='apply' else req.candidate_digest)
            or type(candidate) is not bytes or not 0 < len(candidate) <= 8192
            or digest(candidate) != req.candidate_digest
            or (req.feature=='devices' and candidate != DEVICE_BYTES)):
        raise Refused('invalid_exact_test_request')
    # Rendered Graph bytes come from the independently reviewed TEST Graph
    # planner; the receipt binds exactly those bytes, not arbitrary argv.
    if protected_review(req.receipt_id, digest(req.canonical())) is not True:
        raise Refused('unauthenticated_exact_test_receipt')
    path, units = FEATURES[req.feature]
    current=f'{TEST_ROOT}/releases/{req.revision}-{req.archive}'
    with runner.exclusive_test_deploy_lease():
        if runner.receipt_status(req.receipt_id) != 'absent':
            raise Refused('receipt_already_claimed')
        live=runner.observe(path, req.feature)
        if (type(live) is not Observed or live != Observed('test',current,req.visual_manifest,
                req.baseline_chain,req.existing_digest,req.registry_identity,True)
                or live.feature_authority_ready is not True):
            raise Refused('test_state_drift')
        if req.feature=='devices' and req.operation=='inverse' and req.registry_identity is None:
            raise Refused('registry_identity_required_for_inverse')
        runner.begin(req.receipt_id, digest(req.canonical()))
        expected_registry=req.registry_identity
        if req.operation=='apply':
            if req.feature=='devices' and expected_registry is None:
                expected_registry=runner.initialize_absent_registry(REGISTRY)
                if not valid(expected_registry):
                    raise Refused('registry_initialization_uncertain')
            runner.install_if_absent(path,candidate)
        else:
            runner.remove_if_digest(path,req.candidate_digest)
        runner.restart_exact_test_units(units)
        expected_hash=req.candidate_digest if req.operation=='apply' else None
        after=runner.observe(path,req.feature)
        if after != Observed('test',current,req.visual_manifest,req.baseline_chain,
                             expected_hash,expected_registry,True) or after.feature_authority_ready is not True:
            raise Refused('test_postcondition_uncertain')
        if runner.readonly_test_smoke(req.feature,req.operation) is not True:
            raise Refused('test_smoke_uncertain')
        runner.finish(req.receipt_id)
        return after
