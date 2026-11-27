"""Deterministic phase ordering for the separately installed TEST lifecycle hook.

No subprocesses or network client here. The trusted hook adapter observes
kernel/systemd identity; package or HTTP inputs cannot provide that assertion.
"""
from dataclasses import dataclass
from enum import Enum
import re

GRAPH = 'assertion-test-deploy-graph.service'
BASE = 'assertion-test-deploy-mcp.service'


class Contract(Enum):
    LEGACY = 'legacy'
    NATIVE = 'native'


@dataclass(frozen=True)
class State:
    graph: tuple | None
    base: tuple | None
    port8891: tuple | None
    port8893: tuple | None


def ordered_restart(io, release, contract):
    """Used after release_core swaps current; caller holds existing TEST lock.

    Identity tuple comes from trusted physical cwd, PID/start-time, cgroup and
    listener PID. wait_for is notification/readiness based and bounded by the
    installed hook. A timeout is failure, not permission to start later phases.
    The receiver already owns the activation journal and restore: do not nest
    that journal or swap current here. The inverse calls this same routine on
    the preselected predecessor, including pre-native archive compatibility.
    """
    if (type(contract) is not Contract or type(release) is not str
            or re.fullmatch(r'[a-f0-9]{40}-[a-f0-9]{64}',release) is None):
        raise ValueError('wrong_test_handoff_contract')

    def proof(predicate):
        state = io.wait_for(release, predicate)
        if type(state) is not State or io.current() != release or not predicate(state):
            raise ValueError('unverified_test_handoff_readiness')
        return state

    # Do not touch unidentified listeners; a qualified predecessor (either
    # exact managed unit) or an already idle retry is the only valid baseline.
    io.preflight(release)
    if io.current() != release:
        raise ValueError('test_handoff_current_changed')
    io.stop_exact_pair(GRAPH, BASE)
    proof(lambda state: state == State(None,None,None,None))

    io.start_exact(GRAPH)
    graph = proof(lambda s: s.graph is not None and s.base is None and s.port8893 is None
        and s.port8891 == (s.graph if contract is Contract.LEGACY else None)).graph
    if not io.attest_graph(graph, release, contract):
        raise ValueError('test_graph_unit_wrong_contract')

    io.start_exact(BASE)
    state = proof(lambda s: s.graph == graph and s.base is not None and s.port8893 == s.base
        and s.port8891 == (graph if contract is Contract.LEGACY else s.base))
    if not io.attest_base(state.base, release, contract):
        raise ValueError('test_base_unit_wrong_contract')
    return state
