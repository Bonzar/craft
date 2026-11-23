# Planned production bridge (not installed, not connected)

Phase A adds the reusable workflow, protection code and tests. Do not wire
the caller in the same commit. The existing base workflow continues to test
and rejects all production transport. Review and record the exact Phase A
SHA and accept it in the root policy only after source and VM qualifications.

Phase B replaces only the root caller's production block with the exact
`exact_caller(PHASE_A_SHA)` output from test_producer.py. The existing TEST
prelude and implementation files remain exact pinned bytes, then can change
only after a separate reviewed authorizer update. Normal application commits
do not necessitate a pin update. A TEST receipt from a previous control
commit cannot authorize a newer control commit; test the same immutable bytes
again, without release rebuild. No legacy allow-activation file is consulted
by the new receiver. Keep the old generic production receiver unreachable.

Host setup requires an established scoped admin session; install the same
reviewed root-owned shared modules as TEST under /etc/assertion-release/shared.
Do not reinstall TEST or place a second copy of receiver/core code under PROD.
Then stage only the policy/identity entrypoints for each scope. Second-party
verification is required before applying the live host upgrade;
 do not run anything
until the TEST and visual work owners clear shared host access. Start with
host-preflight.sh using real connection addresses and compare expected
trusted host fingerprint. Stage root-owned immutable code in
/etc/assertion-release/production/authorization, entrypoints and service
hook in /etc/assertion-release/production, root-owned production login wrapper,
then a single forced-only SSH Match for assertion-production-deploy. Identity
and source shell must be distinct from TEST/admin; expected paths exist as
shown by preflight. The only key file is the new root-owned environment
production authorized key, and no test or admin key can qualify. The service
hook uses separate production ports, systemd units and state with explicit
health check. Do not seed any existing personal database during qualification.
Validate no-key, wrong-key, arbitrary command, agent/port forwarding, shell,
PTY, and uppercase/reference mismatches; no state should change on rejection.
Use two independent fresh admin sessions and rollback exact config on mismatch.
Use one synthetic production fixture for service qualification and restore
previous pointer on smoke failure. No real rollout until that passes.

The socket must be created by systemd root, socket filesystem access only
root and production deployment group, peer UID checked by the authority.
The authority runs separately as assertion-release-auth and persists its
replay ledger under its isolated state directory; the deployment UID cannot
write it. Use configured actual VM and actor IDs, no broad secret reuse.
Verify root-only writeability of all source and config paths and that the
VM can fetch only the official GitHub OIDC keys as designed. Validate that
actual JWT claim *shapes* are exact using protected synthetic non-activating
preflight; never print a JWT or private key. If they differ, fail closed and
review a new pin, not an acceptance fallback.

## Runtime permissions and environment binding

The immutable reusable job has a hard-coded production environment and
checks protected main, the authenticated GitHub run actor/triggering actor,
explicit dispatch and exact TEST source. The host independently verifies the
GitHub signed JWT for that exact reusable job, caller and main ref. Its
root-owned configuration pins the actual environment's node ID, exact
subject and explicit operator IDs. The nonce audience binds that exact
environment ID as well as the VM and artifact. The runner uses its scoped
configured matching node ID solely to construct the audience, not as
authority. A runner-provided list or modified UI variable does not authorize
an operator.

No runtime Environments metadata permission is needed; this is not a claim
the branch-policy settings were read at dispatch. Verify the actual protected
branch, exact PROD environment node ID and main-only configuration once during
administrator preflight and maintain it as deployment policy. If the real
trusted JWT does not carry the exact expected environment node claim, fail
closed; do not remove the identity check or add a runtime permission bypass.
The existing #208 inert metadata check is preserved and is not routed to
network transport in Phase A.
