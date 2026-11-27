"""Pure terminal verification for the protected Base controller (no I/O)."""
from dataclasses import dataclass
import re
from transition_witness import Admission, Release

BASE='assertion-production-deploy-mcp.service'
GRAPH='assertion-production-deploy-graph.service'
PRIVATE_PORT=8903
BROWSER_PORT=8902
LEGACY_PORT=8901

@dataclass(frozen=True)
class Process:
    unit: str
    pid: int
    # Kernel process start ticks defeat PID reuse through the smoke interval.
    start_ticks: int
    uid: int
    release: Release

@dataclass(frozen=True)
class Snapshot:
    current: Release
    private: Process
    graph: Process
    listeners: tuple
    # Passed only by root controller after independent protected journal read.
    journal: tuple | None
    boot_id: str
    database_identity: tuple[int,int]

def _snapshot_valid(s, release, uid):
    if (type(s) is not Snapshot or s.current!=release or type(s.boot_id) is not str
            or not re.fullmatch(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}',s.boot_id)
            or type(s.database_identity) is not tuple or len(s.database_identity)!=2
            or any(type(v) is not int or v<=0 for v in s.database_identity)):
        raise ValueError('unexpected observed Base release')
    for process in (s.private,s.graph):
        if (type(process) is not Process or
            type(process.pid) is not int or process.pid<=0 or
            type(process.start_ticks) is not int or process.start_ticks<=0 or
            type(process.uid) is not int or process.uid!=uid or process.release!=release):
            raise ValueError('unexpected observed Base process')
    if s.private.unit!=BASE:
        raise ValueError('unexpected private service')
    if release.mode=='sidecar':
        if s.graph!=s.private:
            raise ValueError('sidecar must share exact private process')
        expected=((BROWSER_PORT,s.private.pid),(PRIVATE_PORT,s.private.pid))
    else:
        if s.graph.unit!=GRAPH or s.graph.pid==s.private.pid:
            raise ValueError('legacy requires its exact separate service')
        expected=((LEGACY_PORT,s.graph.pid),(PRIVATE_PORT,s.private.pid))
    if s.listeners!=expected:
        raise ValueError('unexpected or competing listener')

def terminal(*, admission, predecessor, phase, uid, before, after):
    """Only the independent controller may supply trusted observations.

    No caller-submitted JSON or deploy-UID response may instantiate these
    observations. Owner UI readback is a separate operations gate.
    """
    if (type(admission) is not Admission or type(predecessor) is not Release or
            phase not in ('restart','restore') or type(uid) is not int or uid<=0):
        raise ValueError('invalid protected Base transaction')
    release=admission.candidate if phase=='restart' else predecessor
    for snapshot in (before,after):
        _snapshot_valid(snapshot,release,uid)
    if (before.journal!=(admission.run,admission.nonce,predecessor,admission.candidate) or after.journal is not None or
            before.database_identity!=after.database_identity or before.boot_id!=after.boot_id or before.private!=after.private or before.graph!=after.graph):
        raise ValueError('Base transaction not stably settled')
    return release
