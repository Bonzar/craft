"""TEST protected lifecycle contract; no shell, remote effects, or credentials."""
from dataclasses import dataclass
from enum import Enum
import re

LEGACY_UNIT = 'assertion-test-deploy-graph.service'
PRIVATE_PORT, CARRIER_PORT = 8893, 8891


class Mode(Enum):
    LEGACY = 'legacy'
    NATIVE = 'native'
    DARK = 'dark'


@dataclass(frozen=True)
class Evidence:
    stage: str
    revision: str
    archive: str
    legacy_unit: str
    private_port: int
    carrier_port: int
    origin: str
    database: tuple
    owner_store: tuple


def checked(evidence):
    if (type(evidence) is not Evidence or evidence.stage != 'test'
            or not re.fullmatch('[a-f0-9]{40}', evidence.revision)
            or not re.fullmatch('[a-f0-9]{64}', evidence.archive)
            or evidence.legacy_unit != LEGACY_UNIT or evidence.private_port != PRIVATE_PORT
            or evidence.carrier_port != CARRIER_PORT
            or evidence.origin != existing_public_origin()
            or any(type(x) is not tuple or len(x) != 2
                   or any(type(y) is not int or y <= 0 for y in x)
                   for x in (evidence.database, evidence.owner_store))):
        raise ValueError('wrong_test_carrier_evidence')
    return evidence


def existing_public_origin():
    from apps.graph_viewer.server.graph_node_test_binding import TEST_GRAPH_ORIGIN
    return TEST_GRAPH_ORIGIN


def transplant(io, permit, *, inverse=False):
    """Consume an in-process TEST controller permit, never browser/workflow text.

    io is the already-authorized TEST controller adapter, not app or shell.
    It supplies lock, current physical evidence, neither/both exclusivity and
    exact stage-local operations. Source implementation does not install it.
    Smoke uses only operation-free read endpoints and must not take a shared
    deployment lease while the exclusive lock is held.
    """
    if type(inverse) is not bool:
        raise ValueError('wrong_carrier_direction')
    source, target = ((Mode.NATIVE, Mode.LEGACY) if inverse else (Mode.LEGACY, Mode.NATIVE))
    with io.exclusive():
        expected = checked(io.admit(permit, inverse=inverse))
        if io.observe() != (expected, source):
            raise ValueError('test_carrier_drift')
        # Shadow exercises exact same auth/reads before the irreversible
        # ownership handoff. Hook may not mount any shadow write transport.
        if not inverse:
            io.check_shadow(expected)
        def require(mode):
            if io.observe() != (expected, mode):
                raise ValueError('unexpected_test_carrier_owner')
        require(source)
        try:
            io.retire(source, expected)
            require(Mode.DARK)
            io.start(target, expected)
            require(target)
            io.smoke(target, expected)
            require(target)
        except BaseException:
            current, mode = io.observe()
            if current != expected or mode not in (source, target, Mode.DARK):
                raise RuntimeError('test_carrier_inverse_unsafe')
            if mode == target:
                io.retire(target, expected)
                require(Mode.DARK)
            if mode != source:
                io.start(source, expected)
            require(source)
            io.smoke(source, expected)
            require(source)
            raise
        return target
