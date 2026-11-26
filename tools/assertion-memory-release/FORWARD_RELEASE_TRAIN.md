# Forward release admission (review draft)

Apply per product independently. Keep existing 0.1.x immutable. Seed each
product once from exact live PROD release, payload and generation. Its first
forward train is 1.0.0. Visual must not wait for Base or vice versa.
An already-green TEST candidate proceeds to existing protected PROD approval.

Next minor is only a repair of a recorded failing TEST check in the current
train. Protected review must bind exact predecessor artifact, exact new head,
failing check ID and scope manifest, with explicit no-unrelated-changes
classification even for edits in the same file. Incoming caller claims cannot
populate those types. The resolver must verify immutable review + provider
evidence and compute the manifest independently; paths cannot prove scope.

Wire the same durable authority before tag/Release publication, automatic
build, protected TEST activation and manual PROD activation. Restrict direct
tag/publisher bypass at cutover. GitHub event/build concurrency alone cannot
prevent early publication. Require exact immutable binding after publication;
recover the same slot after timeout, never assume a failed response means no
side effect. Lock promotion before effects; attest actual PROD before closing.
No new train until PROD fresh smoke matches closed artifact and generation.

Privileged historical rebuild/rollback bypasses no forward gate and never
opens or closes a train. If PROD moved away from forward baseline, do not
continue until exact restoration is explicitly requalified; no automatic
ledger reset. Pending uncertain activation also requires independent receiver
reconciliation. This source-only first review does not activate any workflow
or control plane.
