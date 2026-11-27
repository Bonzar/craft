"""Pure preflight/inverse for an independent managed TEST device store."""
from dataclasses import dataclass
from hashlib import sha256
import json
import re

ROOT = '/srv/assertion-release/test'
CONFIG = ROOT + '/state/http.json'
TOKEN = ROOT + '/state/mcp-token'
DATABASE = ROOT + '/state/fictional.sqlite3'
REGISTRY = ROOT + '/state/machine-devices/registry.sqlite3'
MARKER = '/etc/assertion-release/test/machine-device-registry-v1.enabled'
MARKER_BYTES = b'assertion-memory-owner-machine-devices-v1\n'
ACCOUNT = 'assertion-test-deploy'
GRAPH = ACCOUNT + '-graph.service'
MCP = ACCOUNT + '-mcp.service'
HEX = re.compile(r'[a-f0-9]{64}\Z')
REVISION = re.compile(r'[a-f0-9]{40}\Z')
FIELDS = frozenset({
    'stage', 'account', 'uid', 'graph_unit', 'mcp_unit', 'graph_listener', 'mcp_listener',
    'current', 'graph_cwd', 'mcp_cwd', 'base_archive', 'visual_manifest', 'chain_sha256',
    'config_path', 'token_selector', 'selected_database', 'token_env_selector_absent',
    'existing_authority_verified', 'same_namespaces', 'registry_parent_owner', 'registry_parent_mode',
    'registry_parent_safe', 'registry_state', 'registry_identity', 'marker_absent', 'marker_parent_safe',
    'generic_owner_carrier',
})


def digest(raw):
    return sha256(raw).hexdigest()


def seal(evidence):
    data = json.dumps(evidence, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    return digest(data.encode('ascii'))


def pin(value, pattern=HEX):
    return type(value) is str and pattern.fullmatch(value) is not None


@dataclass(frozen=True)
class Pins:
    revision: str
    archive: str
    visual_manifest: str
    chain: str
    evidence: str
    uid: int


@dataclass(frozen=True)
class Candidate:
    marker: str
    marker_sha256: str
    marker_bytes: bytes
    setup: str
    registry: str
    prior_registry_identity: str | None
    fixed_test_units: tuple[str, str]
    current: str
    chain_sha256: str


def plan(e, p: Pins):
    if (not pin(p.revision, REVISION) or not all(pin(x) for x in
            (p.archive, p.visual_manifest, p.chain, p.evidence)) or type(p.uid) is not int or p.uid <= 0):
        raise ValueError('missing_exact_protected_test_pins')
    if type(e) is not dict or e.keys() != FIELDS or seal(e) != p.evidence:
        raise ValueError('test_evidence_drift')
    current = ROOT + '/releases/' + p.revision + '-' + p.archive
    expect = dict(stage='test', account=ACCOUNT, uid=p.uid, graph_unit=GRAPH, mcp_unit=MCP,
                  graph_listener='127.0.0.1:8891', mcp_listener='127.0.0.1:8893',
                  current=current, graph_cwd=current, mcp_cwd=current, base_archive=p.archive,
                  visual_manifest=p.visual_manifest, chain_sha256=p.chain, config_path=CONFIG,
                  token_selector=TOKEN, selected_database=DATABASE, registry_parent_owner=p.uid,
                  registry_parent_mode=0o700)
    if any(type(e[k]) is not type(v) or e[k] != v for k, v in expect.items()):
        raise ValueError('wrong_test_identity_tool_profile_or_store')
    for f in ('token_env_selector_absent', 'existing_authority_verified', 'same_namespaces',
              'registry_parent_safe', 'marker_absent', 'marker_parent_safe', 'generic_owner_carrier'):
        if e[f] is not True:
            raise ValueError('existing_test_authority_unproven')
    if e['registry_state'] == 'absent' and e['registry_identity'] is None:
        setup = 'initialize_private_registry_once_while_dark'
    elif e['registry_state'] == 'private_schema_ready' and pin(e['registry_identity']):
        setup = 'reuse_exact_private_registry_without_mutation'
    else:
        raise ValueError('test_store_unrecognized')
    return Candidate(MARKER, digest(MARKER_BYTES), MARKER_BYTES, setup, REGISTRY, e['registry_identity'],
                     (MCP, GRAPH), current, p.chain)


def inverse(c: Candidate, *, marker_sha256, current, chain_sha256, registry_identity,
            activation_registry_identity):
    if (c.marker != MARKER or c.marker_bytes != MARKER_BYTES or c.marker_sha256 != digest(MARKER_BYTES)
            or c.registry != REGISTRY or c.fixed_test_units != (MCP, GRAPH)
            or marker_sha256 != c.marker_sha256 or current != c.current or chain_sha256 != c.chain_sha256
            or not pin(activation_registry_identity) or registry_identity != activation_registry_identity
            or c.prior_registry_identity not in (None, activation_registry_identity)):
        raise ValueError('rollback_drift_requires_operator')
    return dict(operation='remove_exact_test_marker_then_restart_existing_test_units',
                marker=MARKER, expected_sha256=c.marker_sha256, units=(MCP, GRAPH),
                keep_private_registry=True)
