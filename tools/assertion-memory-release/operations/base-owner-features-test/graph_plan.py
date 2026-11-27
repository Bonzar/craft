"""Pure, non-authorizing rehearsal of a single TEST Graph flag; no host I/O."""
from dataclasses import dataclass
from hashlib import sha256
import ipaddress
import json
import re

ROOT = '/srv/assertion-release/test'
ACCOUNT = 'assertion-test-deploy'
UNIT = 'assertion-test-deploy-graph.service'
FLAGS = '--browser-owner-graph-node-removal'
DROPIN = 'zzzzzz-graph-node-removal-test-v1.conf'
DROPINS = f'/etc/systemd/user/{UNIT}.d'
DESTINATION = f'{DROPINS}/{DROPIN}'
ORIGIN = 'https://d5dc14bhh6hba54q0i3p.hsvi2zuh.apigw.yandexcloud.net'
CREDENTIALS = ('pause-owner-digest', 'pause-proof-key', 'pause-read-token', 'pause-write-token', 'queue-base-token')
ARGV = [f'{ROOT}/current/venv/bin/python', '-B', '-m', 'apps.graph_viewer.server']
VALUES = {
    '--host': '127.0.0.1', '--port': '8891', '--source': f'{ROOT}/state/fictional.sqlite3',
    '--state-dir': f'{ROOT}/state/graph-state', '--password-file': f'{ROOT}/state/graph-password',
    '--static-dir': f'{ROOT}/current/apps/graph_viewer/web/dist', '--public-origin': ORIGIN,
    '--queue-status-config': '/run/assertion-memory-dashboard/test/scheduler.json', '--queue-status-stage': 'test',
}
OPTIONAL = {'--host', '--provider-id', '--embedding-model', '--embedding-cache', '--test-direct-events-origin'}
SWITCHES = {'--synthetic-fixture', '--browser-owner-api'}
FIELDS = frozenset({
    'stage', 'unit', 'account', 'uid', 'effective_uid', 'pid', 'listener', 'cgroup',
    'credential_directory', 'credential_names', 'credential_scope_ok', 'base_owner_policy_matches',
    'private_base_listener', 'current', 'cwd', 'install_chain_sha256', 'visual_manifest_sha256',
    'verified_lane_artifacts', 'generic_owner_carrier', 'preflight_smoke_ok', 'baseline_dropins',
    'dropin_absent', 'argv',
})
HEX = re.compile(r'[0-9a-f]{64}\Z')
REV = re.compile(r'[0-9a-f]{40}\Z')
SAFE = re.compile(r'[A-Za-z0-9._:/-]{1,330}\Z')
NAME = re.compile(r'[A-Za-z0-9._-]{1,150}\.conf\Z')


def fail(message):
    raise ValueError(message)


def digest(data):
    return sha256(data).hexdigest()


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def pin(value, pattern=HEX):
    return type(value) is str and pattern.fullmatch(value) is not None


@dataclass(frozen=True)
class Pins:
    revision: str
    archive: str
    visual_manifest: str
    evidence_sha256: str
    install_chain_sha256: str
    uid: int


@dataclass(frozen=True)
class Candidate:
    destination: str
    rendered: bytes
    candidate_sha256: str
    original_argv: tuple[str, ...]
    baseline_dropins: tuple[tuple[str, str], ...]
    current: str
    install_chain_sha256: str


def parse_argv(argv):
    if type(argv) is not list or not len(ARGV) < len(argv) <= 36 or argv[:4] != ARGV or any(
            not pin(x, SAFE) or any(p in ('.', '..') for p in x.split('/')) for x in argv):
        fail('unrecognized_test_graph_execstart')
    flags, i = {}, 4
    while i < len(argv):
        key = argv[i]
        i += 1
        if key in flags or (key not in VALUES and key not in OPTIONAL and key not in SWITCHES):
            fail('unknown_repeated_or_other_control_flag')
        if key in SWITCHES:
            flags[key] = True
        else:
            if i == len(argv) or argv[i].startswith('--'):
                fail('flag_missing_value')
            flags[key] = argv[i]
            i += 1
    if (not SWITCHES <= flags.keys()
            or any(flags.get(k, v if k in OPTIONAL else None) != v for k, v in VALUES.items())
            or flags.get('--provider-id', 'assertion-memory') != 'assertion-memory'
            or ('--embedding-model' in flags) != ('--embedding-cache' in flags)
            or not flags.get('--embedding-cache', ROOT + '/state/').startswith(ROOT + '/state/')):
        fail('wrong_owner_graph_scope')
    direct = flags.get('--test-direct-events-origin')
    if direct is not None:
        try:
            ip = ipaddress.IPv4Address(direct.removeprefix('https://'))
            if not ip.is_global or direct != f'https://{ip}':
                fail('wrong_direct_test_origin')
        except (ValueError, TypeError):
            fail('wrong_direct_test_origin')


def plan(before: dict, pins: Pins) -> Candidate:
    if (not pin(pins.revision, REV) or not all(pin(v) for v in (
            pins.archive, pins.visual_manifest, pins.evidence_sha256, pins.install_chain_sha256))
            or type(pins.uid) is not int or pins.uid < 1):
        fail('missing_independently_sealed_test_pins')
    if type(before) is not dict or before.keys() != FIELDS or digest(canonical(before)) != pins.evidence_sha256:
        fail('trusted_evidence_drift')
    uid = pins.uid
    cur = f'{ROOT}/releases/{pins.revision}-{pins.archive}'
    if (tuple(before[k] for k in ('stage', 'account', 'unit')) != ('test', ACCOUNT, UNIT)
            or any(type(before[k]) is not int or before[k] != uid for k in ('uid', 'effective_uid'))
            or type(before['pid']) is not int or before['pid'] <= 1
            or before['cgroup'] != f'/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/{UNIT}'
            or before['listener'] != '127.0.0.1:8891' or before['private_base_listener'] != '127.0.0.1:8893'
            or before['current'] != cur or before['cwd'] != cur
            or before['install_chain_sha256'] != pins.install_chain_sha256
            or before['visual_manifest_sha256'] != pins.visual_manifest):
        fail('wrong_exact_test_unit_or_generation')
    if (before['credential_directory'] != f'/run/user/{uid}/credentials/{UNIT}'
            or before['credential_names'] != list(CREDENTIALS)
            or before['credential_scope_ok'] is not True or before['base_owner_policy_matches'] is not True):
        fail('existing_test_owner_authority_unproven')
    if any(before[k] is not True for k in ('dropin_absent', 'verified_lane_artifacts',
                                           'generic_owner_carrier', 'preflight_smoke_ok')):
        fail('test_readiness_unproven')
    dropins = before['baseline_dropins']
    if (type(dropins) is not dict or not 1 <= len(dropins) <= 16
            or any(not pin(name, NAME) or name >= DROPIN or not pin(value) for name, value in dropins.items())):
        fail('unrecognized_or_shadowing_dropin')
    argv = before['argv']
    parse_argv(argv)
    raw = ('# Canonical TEST Graph node removal; no new credential directives.\n'
           '[Service]\nExecStart=\nExecStart=' + ' '.join([*argv, FLAGS]) + '\n').encode('ascii')
    return Candidate(DESTINATION, raw, digest(raw), tuple(argv), tuple(sorted(dropins.items())), cur,
                     pins.install_chain_sha256)


def inverse(candidate: Candidate, *, actual_sha256: str, current: str,
            baseline_dropins: dict, install_chain_sha256: str) -> dict:
    if (candidate.destination != DESTINATION or digest(candidate.rendered) != candidate.candidate_sha256
            or actual_sha256 != candidate.candidate_sha256 or current != candidate.current
            or baseline_dropins != dict(candidate.baseline_dropins)
            or install_chain_sha256 != candidate.install_chain_sha256):
        fail('rollback_drift_requires_operator')
    return dict(operation='remove_exact_dropin_then_verify_reload_restart_same_test_graph',
                unit=UNIT, path=DESTINATION, expected_present_sha256=actual_sha256,
                expected_argv=candidate.original_argv)
