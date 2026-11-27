# Exact native TEST attempt fence (disabled)

The existing release generation is deterministic for the same artifact; bytes,
tag and generation alone are not a distinct native activation. The explicit
NativeAttemptCheckpoints path joins three proofs under the same family lock:
an authority-issued TEST attempt, trusted actual TEST witness, and independently
verified selected TEST receipt. The artifact and generation must also match.
Authorized scoped recovery rotates the native attempt. No review policy or
normal build/tag/release production is changed, and legacy activation does not
publish forward authority.

Existing Base seams: authenticated configured TEST Intent and validated native
target -> shared release_core.ActivationWitness (before unlink/after fsync) +
canonical TEST Graph/Gateway proof -> protected native attempt recorder. Existing
PROD authorizer must consume that exact current receipt before the native switch.
Visual packed activation follows runtime_joint + authenticated static/runtime
health; the same trusted attempt marker is required. Known bits of these hooks
were tested with real activation helpers and only fixture authority.

The fixed TEST and PROD receiver identities currently expose independent
protected stores and no shared native attempt authority. This patch does not
merge/install a shared recorder, hand workflows new authority, or activate the
path. Absence of the approved recorder rejects before reservation.
