"""Unified protected releaser contract; live actuators are not shipped."""
from dataclasses import dataclass
import re

HEX=re.compile(r'[a-f0-9]{64}\Z')
SHA=re.compile(r'[a-f0-9]{40}\Z')
TAG=re.compile(r'assertion-releaser-v[0-9]+\.[0-9]+\.[0-9]+\Z')
TEST_BASELINE='b44bc45000daad0b09510ceaf269540cac545ca85bd7f316d3571174a4af1722'


@dataclass(frozen=True)
class Lane:
    environment: str
    config: str
    root: str
    operator: str


LANES={
    'test':Lane('assertion-releaser-test','/etc/assertion-release/test','/srv/assertion-release/test','test-releaser-owner'),
    'prod':Lane('assertion-releaser-prod','/etc/assertion-release/production','/srv/assertion-release/production','prod-releaser-owner'),
}


@dataclass(frozen=True)
class Receipt:
    lane: str
    environment: str
    operator: str
    action: str
    tag: str
    commit: str
    artifact: str
    before: str
    after: str
    inverse: str
    adapter: str
    nonce: str
    forward_receipt: str | None = None


def operate(lane_key, action, receipt, *, configured, installed_artifact, verify, io):
    """Call from one fixed environment-bound lane adapter, never from the app.

    configured and verify are protected host wiring; CLI/workflow values cannot
    select or supply them. verify must verify signature and expiry independently.
    io atomically reserves one-use receipt under the installed lane lock before
    any effect; on uncertain outcome it must retain the pending reservation.
    """
    if type(lane_key) is not str or lane_key not in LANES or configured != lane_key:
        raise ValueError('unbound releaser lane')
    lane=LANES[lane_key]
    if action not in ('apply','inverse') or type(receipt) is not Receipt:
        raise ValueError('unrecognized releaser action')
    if ((receipt.lane,receipt.environment,receipt.operator,receipt.action)!=(lane_key,lane.environment,lane.operator,action)
            or any(not isinstance(s,str) or not HEX.fullmatch(s) for s in
                   (receipt.artifact,receipt.before,receipt.after,receipt.inverse,receipt.adapter,receipt.nonce))
            or not isinstance(receipt.tag,str) or not TAG.fullmatch(receipt.tag)
            or not isinstance(receipt.commit,str) or not SHA.fullmatch(receipt.commit)
            or receipt.artifact!=installed_artifact
            or receipt.before==receipt.after or receipt.inverse!=receipt.before
            or lane_key=='test' and receipt.before!=TEST_BASELINE
            or action=='apply' and receipt.forward_receipt is not None
            or action=='inverse' and (not isinstance(receipt.forward_receipt,str)
                                         or not HEX.fullmatch(receipt.forward_receipt))
            or not verify(lane,receipt)):
        raise ValueError('unverified releaser receipt')
    with io.lock(lane):
        io.assert_current(lane,receipt)
        if action=='inverse':
            prior=io.settled_forward(lane,receipt.forward_receipt)
            if (type(prior) is not Receipt or prior.action!='apply' or prior.nonce!=receipt.forward_receipt
                    or any(getattr(prior,k)!=getattr(receipt,k) for k in
                           ('lane','environment','operator','tag','commit','artifact','before','after','inverse','adapter'))):
                raise ValueError('wrong releaser inverse predecessor')
        require=(receipt.before,None) if action=='apply' else (receipt.after,receipt.adapter)
        target=(receipt.after,receipt.adapter) if action=='apply' else (receipt.inverse,None)
        if io.observe(lane)!=require:
            raise ValueError('releaser state drift')
        io.reserve(lane,receipt)  # Durable single use even after failure.
        if action=='apply':
            io.stage_adapter(lane,receipt.adapter)
            if io.observe(lane)!=(receipt.before,receipt.adapter):
                raise ValueError('releaser adapter did not stage')
            io.swap(lane,receipt.before,receipt.after)
        else:
            io.swap(lane,receipt.after,receipt.inverse)
            if io.observe(lane)!=(receipt.inverse,receipt.adapter):
                raise ValueError('releaser old hook not restored')
            io.remove_exact_adapter(lane,receipt.adapter)
        if io.observe(lane)!=target:
            raise ValueError('releaser final state drift')
        io.finish(lane,receipt,target)
        return target
