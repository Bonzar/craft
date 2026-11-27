"""Explicit binding for the protected standalone TEST Graph service only."""
import hashlib
import os
from pathlib import Path
import pwd

from prototypes.assertion_memory.queue_pause_binding import TEST, _policy
from .prod_owner_bridge import GraphNodeOwnerBridge, _read_owner_credentials

TEST_GRAPH_UNIT = 'assertion-test-deploy-graph.service'
SELF_CGROUP = Path('/proc/self/cgroup')
TEST_GRAPH_ORIGIN = 'https://d5dc14bhh6hba54q0i3p.hsvi2zuh.apigw.yandexcloud.net'


def _in_test_graph_user_unit(uid, cgroup):
    # cgroup v2, ordinary systemd --user service in the default app.slice.
    parent=f'/user.slice/user-{uid}.slice/user@{uid}.service'
    return any(line==f'0::{parent}/app.slice/{TEST_GRAPH_UNIT}' for line in cgroup.splitlines())


def installed_test_graph_node_bridge(dashboard):
    uid = pwd.getpwnam(TEST.account).pw_uid
    credentials=Path(f'/run/user/{uid}/credentials')/TEST_GRAPH_UNIT
    if (dashboard.stage != 'test' or Path(dashboard.db) != TEST.db or uid <= 0
            or os.getuid() != uid or os.geteuid() != uid
            or os.environ.get('CREDENTIALS_DIRECTORY') != str(credentials)
            or not _in_test_graph_user_unit(uid,SELF_CGROUP.read_text(encoding='ascii'))):
        raise ValueError('TEST graph node removal requires canonical user-unit credentials')
    policy = _policy(TEST)
    if policy is None:
        raise ValueError('Existing TEST private Base owner authority is required')
    queue, read, write, owner, signer = _read_owner_credentials(credentials)
    fingerprints = tuple(hashlib.sha256(v.encode('ascii')).hexdigest() for v in (read, write, signer))
    if (fingerprints != tuple(policy[k] for k in ('read_sha256','write_sha256','signer_sha256'))
            or owner != policy['owner_sha256']):
        raise ValueError('TEST Graph credentials do not match Base owner authority')
    return GraphNodeOwnerBridge(queue,read,write,owner,bytes.fromhex(signer),stage='test')
