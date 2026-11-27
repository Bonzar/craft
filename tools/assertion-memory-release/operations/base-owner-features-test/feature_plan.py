"""Connect the exact TEST planners to the inert, typed execution seam."""
import device_plan
import graph_plan
from transaction import Request, digest, Refused


def graph(before, pins, receipt_id):
    plan = graph_plan.plan(before, pins)
    req = Request('test', 'graph', 'apply', pins.revision, pins.archive, pins.visual_manifest,
                  pins.install_chain_sha256, receipt_id, None, plan.candidate_sha256, None)
    return req, plan.rendered


def devices(before, pins, receipt_id):
    plan = device_plan.plan(before, pins)
    req = Request('test', 'devices', 'apply', pins.revision, pins.archive, pins.visual_manifest,
                  pins.chain, receipt_id, None, plan.marker_sha256, plan.prior_registry_identity)
    return req, plan.marker_bytes


def inverse(prior, candidate, *, verified_after, receipt_id):
    # A sealed original receipt and verified installed output are subsequently
    # independently checked again by the protected actor before execution.
    from transaction import Observed, TEST_ROOT
    if (type(prior) is not Request or prior.operation != 'apply' or prior.stage != 'test'
            or type(verified_after) is not Observed or verified_after.stage != 'test'
            or type(candidate) is not bytes or digest(candidate) != prior.candidate_digest
            or verified_after.existing_digest != prior.candidate_digest
            or verified_after.current != f'{TEST_ROOT}/releases/{prior.revision}-{prior.archive}'
            or verified_after.visual_manifest != prior.visual_manifest
            or verified_after.baseline_chain != prior.baseline_chain
            or verified_after.feature_authority_ready is not True
            or (prior.feature == 'devices' and verified_after.registry_identity is None)
            or (prior.feature == 'graph' and verified_after.registry_identity is not None)
            or prior.registry_identity not in (None, verified_after.registry_identity)):
        raise Refused('inverse_not_bound_to_exact_previous_install')
    return Request('test', prior.feature, 'inverse', prior.revision, prior.archive,
                   prior.visual_manifest, prior.baseline_chain, receipt_id, prior.candidate_digest,
                   prior.candidate_digest, verified_after.registry_identity), candidate
