# Exact Base terminal proof, offline candidate

The native boundary adapter calls the existing release_core validation
and reads the exact journal/current and manifest digest against **already
independently admitted** identities. Neither the deploy-owned journal nor
the deploy-owned SHA256SUMS can become a source of authority. The kernel
and smoke observation bindings are callable interfaces, not stand-ins
for real process or HTTP probes. Nothing is added to the startup path.

The checked-in current configured PROD authorizer issues exact v1/v2 OIDC
one-use promotion authorization; its permit returns only status, scope,
revision, and archive. It does not bind manifest/mode or predecessor and
cannot be mapped directly to Admission. Reuse exactly its issuer/actor/
workflow/runner/nonce policy for additive v3 canonical signed envelope
with sealed manifest+mode; maintain prior active identity and pending
witness using the existing independently owned authorizer state/scope.
No new roles/keys/cookies or application DB/schema. Keep v1/v2 nonterminal.

The existing protected owner of the native controller must choose the
exact phase before collection. Run under the existing release lease, retain
the existing transition fence sequence (avoid a lease across a reentrant
protected smoke), and pass the native journal completion as its own
existing continuation. Candidate selection uses same installed archive;
rollback must call the original restore/smoke for historical release.
Use exact kernel PIDs, start ticks, UID and real listener ownership plus
before/after authenticated smoke; do not substitute a copied deploy success
string. Only the separately trusted controller may record precommit and
settle the authorizer's independent witness after native journal completion.
With no precommit after journal disappearance, fail closed and do not repeat
the native mutation.

Open integration prerequisites:
- Root-managed untracked pinned settled/receiver hook wiring for independent
  kernel/route observation and proof publishing; actual live no trustworthy
  publisher exists. Current approval permits narrowly scoped verifier,
  not bypassing existing receipt/rollback or granting generic execution.
- Protect exact v3 issuance and same existing socket mutual identity;
  bind independent witness run+nonce and actual revision/archive/manifest
  plus predecessor. Avoid changing persisted schema unless authorized;
  separate authorizer-owned durable record is the proposed location.
- Sidecar direct authenticated Graph check needs approved evidence;
  current legacy Smoke logs in but does not consume a cookie or fetch Graph
  afterwards. Do not alter or overstate that baseline, export any cookie
  or owner password, or use browser login outside the exact controlled proof.
- Real route rollback/ingress ownership and actual phase evidence are
  separately needed before any protected activation.

Offline:
    D=tools/assertion-memory-release/operations/base_browser_terminal
    PYTHONPATH=tools/assertion-memory-release:$D python -m unittest discover -s "$D" -p 'test_*.py'

No host I/O, releases or production activation are done by this suite.
