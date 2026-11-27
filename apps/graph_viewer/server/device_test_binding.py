"""Fixed standalone TEST Graph bridge to the same lane-owned machine registry."""
from functools import partial
import json
import os
from pathlib import Path
import pwd
import stat

from prototypes.assertion_memory.queue_rest import _identity
from prototypes.assertion_memory.machine_device_registry import MachineDeviceRegistry
from prototypes.assertion_memory.private_http_binding import configured_neutral_factory
from prototypes.assertion_memory.http_server import load_token

ROOT=Path('/srv/assertion-release/test')
CONFIG=ROOT/'state'/'http.json'
LEGACY=ROOT/'state'/'mcp-token'
MARKER=Path('/etc/assertion-release/test/machine-device-registry-v1.enabled')
ACCOUNT='assertion-test-deploy'


def _read_profile(path,owner,limit):
    if path.resolve(strict=True)!=path:
        raise ValueError('Untrusted machine device profile path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as stream:
        st=os.fstat(stream.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=owner or st.st_nlink!=1
            or st.st_mode&0o022 or not os.path.samestat(st,path.lstat())):
            raise ValueError('Untrusted machine device profile')
        data=stream.read(limit+1)
        if len(data)>limit:
            raise ValueError('Oversized machine device profile')
    return data


def installed_test_device_binding(dashboard):
    """Returns no binding unless the explicit existing-lane profile is present."""
    try:
        MARKER.lstat()
    except FileNotFoundError:
        return None
    uid=pwd.getpwnam(ACCOUNT).pw_uid
    canonical=ROOT/'state'/'fictional.sqlite3'
    if dashboard.stage!='test' or Path(dashboard.db)!=canonical or uid<=0 or os.getuid()!=uid or os.geteuid()!=uid:
        raise ValueError('Wrong standalone TEST device authority')
    if _read_profile(MARKER,0,64)!=b'assertion-memory-owner-machine-devices-v1\n':
        raise ValueError('Unknown standalone TEST device profile')
    # The existing fixture provisioning uses this canonical token file. Do
    # not inspect another unit's environment, ask for a secret, or guess a
    # path if an installed authority uses a different credential selector.
    def distinct(pairs):
        result=dict(pairs)
        if len(result)!=len(pairs):
            raise ValueError('Ambiguous TEST private HTTP profile')
        return result
    config=json.loads(_read_profile(CONFIG,uid,65536),object_pairs_hook=distinct)
    if (type(config) is not dict or config.get('token_file')!=str(LEGACY)
        or 'token_env_var' in config or config.get('port')!=8893
        or not isinstance(config.get('service'),dict) or config['service'].get('db')!=str(canonical)):
        raise ValueError('Unverified TEST private HTTP credential source')
    if LEGACY.resolve(strict=True)!=LEGACY:
        raise ValueError('Noncanonical TEST credential path')
    identity=partial(_identity,ROOT,'test')
    identity()
    factory=configured_neutral_factory(CONFIG,load_token(LEGACY))
    registry=getattr(factory,'machine_device_registry',None)
    if type(registry) is not MachineDeviceRegistry or registry.lane!='test':
        raise ValueError('Cannot establish same TEST machine registry')
    return registry,identity
