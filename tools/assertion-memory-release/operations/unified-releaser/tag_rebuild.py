"""Protected tag-to-ephemeral-package contract. No host or GitHub I/O."""
from dataclasses import dataclass
from hashlib import sha256
from releaser import HEX, LANES, SHA, TAG, operate


@dataclass(frozen=True)
class TagSelection:
    lane: str
    operation: str
    tag: str
    release_id: int
    commit: str
    digest: str


@dataclass(frozen=True)
class TagRun:
    repository: str
    trigger: str
    control_branch: str
    control_branch_protected: bool
    environment: str
    immutable: bool
    release_id: int
    tag: str
    first_ref_commit: str
    last_ref_commit: str
    checkout_commit: str
    checked_commit_approved: bool
    rebuilt: bytes


def qualify(selection, run):
    """Actor independently obtains this metadata from the protected job.

    The tag can have no attachment. Rebuild only after independent commit
    admission and verify remapped tag/immutable release again after build.
    """
    if (type(selection) is not TagSelection or type(run) is not TagRun
            or selection.lane not in LANES or selection.operation not in ('apply','rollback')
            or not isinstance(selection.tag,str) or not TAG.fullmatch(selection.tag)
            or not isinstance(selection.commit,str) or not SHA.fullmatch(selection.commit)
            or not isinstance(selection.digest,str) or not HEX.fullmatch(selection.digest)
            or type(selection.release_id) is not int or selection.release_id<=0
            or (run.repository,run.trigger,run.control_branch)!=('Bonzar/craft','workflow_dispatch','refs/heads/main')
            or run.control_branch_protected is not True or run.checked_commit_approved is not True
            or run.environment!=LANES[selection.lane].environment
            or run.immutable is not True or type(run.release_id) is not int or run.release_id!=selection.release_id
            or run.tag!=selection.tag
            or (run.first_ref_commit,run.last_ref_commit,run.checkout_commit)!=(selection.commit,)*3
            or type(run.rebuilt) is not bytes or not 0<len(run.rebuilt)<=256*1024
            or sha256(run.rebuilt).hexdigest()!=selection.digest):
        raise ValueError('unqualified protected releaser rebuild')
    return selection.digest


def dispatch(selection, run, receipt, *, configured_lane, verify, io):
    digest=qualify(selection,run)
    if configured_lane!=selection.lane or io is None or not callable(verify):
        raise ValueError('existing lane executor not qualified')
    if (getattr(receipt,'tag',None),getattr(receipt,'commit',None))!=(selection.tag,selection.commit):
        raise ValueError('receipt does not bind selected releaser tag')
    operation='apply' if selection.operation=='apply' else 'inverse'
    return operate(selection.lane,operation,receipt,configured=configured_lane,
                   installed_artifact=digest,verify=verify,io=io)
