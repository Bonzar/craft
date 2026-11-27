# TEST receipt reconciliation and protected continuation (dark source only)

A normal first activation durably creates an exact attempt under the release
family lock before the actuator can run. Every normal retry thereafter can only
rebuild the receipt from an independently authenticated, fresh actual TEST
observation matching release ID, revision, digest and (if already known)
generation. A stale/superseded record or observation never revives a candidate.

A separate operator operation accepts either independently protected proof that
the actuator was never entered for this exact attempt, or an independently
verified protected manual recovery decision. The manual decision must bind
product/TEST/exact release and digest, exact attempt ID, a durable decision ID
and incident evidence sufficient to authorize a safe new effect. For a prior
effect that began, baseline alone is insufficient: require independently
verified failure/rollback completion, unchanged relevant durable state and no
active switch/lease. Unknown state stays blocked. Both decisions are scoped to
the same still-current candidate and may be consumed only once by compare and
swap before the actuator runs. No automatic generation of these decisions.
If the retry sees the exact active candidate first, it reconciles and does not
invoke the operator continuance.

The existing native activation journal may be absent both before intent and
after a successfully completed or rolled-back activation. Its absence, old UI
state, and elapsed time cannot prove non-entry. Current ActivationWitness
supports completing a published-result crash window but does not yet expose
independently authenticated non-entry for this authority attempt ID. No proof
adapter or manual recovery policy is provisioned by this patch. Runtime is off.
