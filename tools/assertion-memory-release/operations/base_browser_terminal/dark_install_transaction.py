"""Narrow exact dark recovery transaction, with independent root-owned I/O.

The host binding must construct the adapter from the reviewed and protected
root filesystem. This module has no CLI, credentials or service operations.
"""
from hashlib import sha256

from sealed_host_install_bundle import MOUNT,STAGED,MOUNTED,verify_source_set

ORDER=(
    'configured_base_prod_gate.py','receiver-entry.py','authorizer-entry.py',
    'prod_service_hook.py','assertion-base-prod-transition',
)

def _sha(raw):
    if type(raw) is not bytes:
        raise ValueError('exact protected bytes required')
    return sha256(raw).hexdigest()

def _recovery(verified_manifest):
    return _sha(verified_manifest)

def run(*, manifest, source_commit, reviewed_pins, images, protected_io, inverse=False):
    """Protected I/O holds existing deploy then transition lock, no service action.

    It fsyncs recovery before mutation; all file writes use same-directory
    replacement/fsync. Stage/discard namespace only under fixed existing root.
    A failed half-step leaves the recovery anchor, not an unsigned fallback.
    """
    if type(inverse) is not bool:
        raise ValueError('closed dark action required')
    rows=verify_source_set(manifest,source_commit=source_commit,
        exact_source_digest_map=reviewed_pins,candidate_images=images)
    existing={row['destination']:row for row in rows if row['before'] is not None}
    ordered=sorted(existing,key=lambda path:ORDER.index(path.rsplit('/',1)[1]))
    mounted={row['destination']:row for row in rows if row['before'] is None}
    if set(existing)!=set(STAGED) or set(mounted)!=set(MOUNTED):
        raise ValueError('exact reviewed dark source scope required')
    digest=_recovery(manifest)
    with protected_io.lease():
        if protected_io.marker_absent() is not True or protected_io.native_idle() is not True:
            raise ValueError('protected dark operator preflight failed')
        anchor=protected_io.recovery()
        observed={path:_sha(protected_io.read_exact(path,existing[path]['mode'])) for path in ordered}
        if inverse:
            if anchor!=digest:
                raise ValueError('no exact durable inverse for this bundle')
            originals=protected_io.originals(digest)
            if (set(originals)!=set(existing) or any(_sha(raw)!=existing[p]['before'] for p,raw in originals.items())
                    or any(observed[p] not in (existing[p]['before'],existing[p]['after']) for p in ordered)):
                raise ValueError('unverified mixed-prefix inverse')
            # Validate the namespace (present and exact, or absent) before
            # changing the first fixed file. Never delete an unknown file.
            protected_io.check_namespace(MOUNT,mounted,allow_missing=True)
            for path in reversed(ordered):
                if observed[path]==existing[path]['after']:
                    protected_io.replace_exact(path,existing[path]['mode'],observed[path],originals[path])
            protected_io.discard_namespace(MOUNT,mounted)
            if any(_sha(protected_io.read_exact(path,existing[path]['mode']))!=existing[path]['before'] for path in ordered):
                raise ValueError('protected stock inverse readback failed')
            protected_io.finish_inverse(digest)
            return 'stock-restored'
        if anchor is not None or any(observed[p]!=existing[p]['before'] for p in ordered):
            raise ValueError('dirty dark install; supply exact reviewed inverse')
        protected_io.check_namespace(MOUNT,mounted,require_absent=True)
        originals={p:protected_io.read_exact(p,existing[p]['mode']) for p in ordered}
        if any(_sha(raw)!=existing[p]['before'] for p,raw in originals.items()):
            raise ValueError('protected original source changed')
        protected_io.stage_inverse(digest,originals)
        if protected_io.recovery()!=digest:
            raise ValueError('protected inverse missing before first write')
        protected_io.publish_namespace(MOUNT,mounted,{p:images[row['source']] for p,row in mounted.items()})
        protected_io.check_namespace(MOUNT,mounted)
        for path in ordered:
            row=existing[path]
            protected_io.replace_exact(path,row['mode'],row['before'],images[row['source']])
        if any(_sha(protected_io.read_exact(path,existing[path]['mode']))!=existing[path]['after'] for path in ordered):
            raise ValueError('protected dark install final readback failed')
        if protected_io.marker_absent() is not True:
            raise ValueError('protected marker changed during source-only install')
        return 'source-dark-ready'
