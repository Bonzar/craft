# Existing-socket protected projection, disabled

The private baseline stays in the root process. Its phase, proof and precommit
remain under the existing protected root transition directory. Neither authorizer UID980 nor deploy
UID986 opens those files when the projection profile is selected. Only
authenticated kernel UID 0 may send canonical run/nonce, phase, kind and a
digest over the one existing configured authorizer Unix socket. It sends
them after private fsync under the native transition fence. UID986 may read
only the active admitted pair or exact terminal digest over the same
socket; it cannot publish. No raw observations traverse the socket.

Normal native checkpoint polling does not hold the transition fence.
The installed stock guard transform executes the fixed scoped action
inside its existing exclusive fence, refetching active authorization,
projected phase and exact sealed physical package there. Neither waiting
nor stale terminal state authorizes a restart. First-effect failure may
use a root-proven no-op only if original legacy baseline PID/start/boot/DB
and exact restoring journal still match; if that baseline is missing or
changed the deployment fails closed rather than guessing. Precommit goes
after inherited smoke, before native journal unlink; final status is the
existing independent root-authorized terminal digest.

The checked-in source selector requires a separate exact immutable
root-owned projection profile; it is absent from the existing installed
profile and from this install manifest. A missing profile does not use
the projection socket and retains prior dark behavior. A malformed
profile fails closed. No marker or permissions must be changed to test
this source. The older b937/562 dark bundle is a fixed independent
artifact and is not a projection package. Prepare a new independently
approved manifest and launcher for any future installation, and verify the
actual first-root-operator setup (before the approved protected deploy),
baseline, cadence and real forward/inverse before considering activation.
