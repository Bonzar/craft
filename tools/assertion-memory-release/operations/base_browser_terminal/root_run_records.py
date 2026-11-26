"""Filesystem names from exact already-admitted PROD nonce, not a request path."""
from hashlib import sha256
from pathlib import Path
from transition_witness import Admission

RECORDS=Path('/var/lib/assertion-base-prod-transition/base-terminal-witness-v1')
KINDS=frozenset(('phase','abort','restart.precommit','restore.precommit','restart.settled','restore.settled'))

def record(admission,kind):
    if type(admission) is not Admission or admission.lane!='prod' or kind not in KINDS:
        raise ValueError('protected exact root run record required')
    scope=sha256((admission.run+':'+admission.nonce).encode('ascii')).hexdigest()
    return RECORDS/(scope+'.'+kind)
