"""Qualify one immutable releaser asset, independently of selected lane."""
from dataclasses import dataclass
from hashlib import sha256
from releaser import HEX, LANES, SHA, TAG, operate


@dataclass(frozen=True)
class Selection:
    lane: str
    operation: str
    tag: str
    commit: str
    digest: str


@dataclass(frozen=True)
class Published:
    tag: str
    ref_commit: str
    immutable: bool
    asset_name: str
    asset: bytes


def qualify(selection, publication):
    """Caller reads publication and actual tag ref through trusted GitHub API."""
    if (type(selection) is not Selection or type(publication) is not Published
            or selection.lane not in LANES or selection.operation not in ('apply','rollback')
            or not TAG.fullmatch(selection.tag) or not SHA.fullmatch(selection.commit)
            or not HEX.fullmatch(selection.digest) or publication.immutable is not True
            or (publication.tag,publication.ref_commit,publication.asset_name)!=(selection.tag,selection.commit,'assertion-releaser.tar.gz')
            or type(publication.asset) is not bytes or not 0<len(publication.asset)<=256*1024
            or sha256(publication.asset).hexdigest()!=selection.digest):
        raise ValueError('unverified immutable releaser publication')
    return selection.digest


def entrypoint(selection, publication, receipt, *, configured_lane, verify, io):
    digest=qualify(selection,publication)
    if configured_lane not in LANES or configured_lane!=selection.lane or io is None or not callable(verify):
        raise ValueError('no installed authorized releaser actuator')
    if (getattr(receipt,'tag',None),getattr(receipt,'commit',None))!=(selection.tag,selection.commit):
        raise ValueError('releaser receipt does not bind selected publication')
    operation='apply' if selection.operation=='apply' else 'inverse'
    return operate(selection.lane,operation,receipt,configured=configured_lane,
                   installed_artifact=digest,verify=verify,io=io)
