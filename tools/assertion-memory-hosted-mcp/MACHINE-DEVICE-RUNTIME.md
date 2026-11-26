# Stage-local managed machine fingerprints

No live system or release is changed by this source. Existing config-owned
`machine_clients` is immutable, including the designated personal Mac and
Sites/gateway credentials. The machine path still uses the existing full
verified operation catalog in the same lane; no other route or credential
can be used for enrollment.

The optional root-owned exact `machine-device-registry-v1.enabled` file is
absent by default. Its bytes are `assertion-memory-owner-machine-devices-v1`
and one newline. The existing lane deploy account may open only its exact
already-initialized private `state/machine-devices/registry.sqlite3`; the
directory belongs to the existing lane account and admits no group/world
access. No startup initialization, credential issuance or memory DB changes.
Only a protected, separately authorized rollout may initialize and enable
this. Absent marker leaves existing static authentication unchanged.
The registry fails closed on mode, UID, inode, lane or content drift.

The separately reviewed owner browser integration should inject the exact
same registry into the existing Base-owned authenticated app after immutable
lane/current-release verification. Do not advertise the capability until
the concrete Base owner adapter exists. Wire calls:

- GET status: only after exact existing owner cookie and fixed host checks,
  no extra read secret; `registry.status()`.
- POST prepare: additionally verify same Origin and existing owner session
  CSRF; derive a fixed hex digest from the **already verified opaque
  session ID** in trusted server memory, not a supplied owner field.
  `registry.prepare(owner_binding, exact_body)` emits a one-use grant
  that expires in 120 seconds. Store only the SHA-256 of the grant.
- PUT commit: independently require same owner cookie, Origin and serving
  release/lane lease, then `registry.commit(owner_binding, exact_body, proof)`.
  This consumes the exact proof in the same transaction as registry
  generation mutation. Do not use the ordinary session CSRF as operation
  proof. Return typed 409 on stale, 403 on invalid proof, 503 on unavailable;
  never surface raw exception reasons, proof or submitted strings in logs.

Current registry resolution happens again for each machine request, including
the effects guard, so revocation takes effect on the next request without
restart. Active requests already authenticated may finish. If enrollment
commits between middleware and inner auth, an earlier denial is terminal
and cannot gain a path around the release lock. If currently allowed, the
guard takes the same shared lease as the existing machine endpoint, and
inner auth may still deny on a concurrent revoke.
