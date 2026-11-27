"""External release convergence and explicit predecessor redeploy evidence.

No deploy, auth, browser, receipt-fetch, or root operations. Protected workflow
metadata and public responses are supplied by separate pre-authenticated
collectors; this module cannot authenticate arbitrary input on its own.
"""
from dataclasses import dataclass
import re

import public_read_contract as wire

WORKFLOW = '.github/workflows/configured-base-release.yml'
BASE_ENV = 'configured-release-base-test'
ID = re.compile(r'[1-9][0-9]{0,19}\Z')
MAX_OBSERVATION_DELAY = 180


@dataclass(frozen=True)
class Protected:
    run: str
    attempt: int
    workflow: str
    environment: str
    started_at: int
    finished_at: int
    receipt: dict


@dataclass(frozen=True)
class Observed:
    timestamp: int
    response: tuple


def _receipt(value, expected):
    if (type(value) is not Protected or type(value.run) is not str or not ID.fullmatch(value.run)
            or type(value.attempt) is not int or value.attempt != 1 or value.workflow != WORKFLOW
            or value.environment != BASE_ENV or type(value.started_at) is not int
            or type(value.finished_at) is not int or not 0 < value.started_at < value.finished_at):
        raise ValueError('wrong_protected_test_run')
    _, revision, archive = expected
    if type(value.receipt) is not dict or value.receipt != dict(
            status='ok', environment='test', revision=revision, sha256=archive):
        raise ValueError('wrong_exact_protected_receipt')


def _stamp(value):
    if type(value) is not Observed or type(value.timestamp) is not int or value.timestamp <= 0:
        raise ValueError('wrong_public_observation')
    return value.timestamp


def prove(*, predecessor, candidate, before, forward, forward_read, inverse, inverse_read):
    old, new = wire.identity(*predecessor), wire.identity(*candidate)
    if old == new or predecessor[1:] == candidate[1:]:
        raise ValueError('releases_not_distinct')
    _receipt(forward, candidate)
    _receipt(inverse, predecessor)
    b, f, i = map(_stamp, (before, forward_read, inverse_read))
    if not (forward.run != inverse.run and
            b < forward.started_at < forward.finished_at <= f
            < inverse.started_at < inverse.finished_at <= i
            and forward.started_at-b <= MAX_OBSERVATION_DELAY
            and f-forward.finished_at <= MAX_OBSERVATION_DELAY
            and i-inverse.finished_at <= MAX_OBSERVATION_DELAY):
        raise ValueError('unordered_or_stale_evidence')
    for observation, expected in ((before, predecessor), (forward_read, candidate), (inverse_read, predecessor)):
        wire.read_identity(lambda _: observation.response, *expected)
    return dict(environment='test', forward_run=forward.run, inverse_run=inverse.run,
                candidate_generation=new, restored_generation=old,
                inverse='explicit_predecessor_redeploy', automatic_receiver_rollback_proven=False,
                kernel_pid_proven=False)
