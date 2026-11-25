# TEST Visual runtime companion

The original Visual release build preserves its static ZIP/TAR and separately
publishes `assertion-memory-visual-runtime-<release id>-<revision>` from the same
attempt and commit. The companion has only `asset-origin.py` and
`sites-shell.mjs`, with an exact byte receipt. The verifier binds tag, source
revision, release ID, build run ID, static archive SHA, runtime archive SHA,
and the provider artifact ID/digest. The release controller must first resolve
those values with its existing protected Visual checks and require one exact
companion from that original build. An expired companion is not replaceable
from main or a later build.

The proof uses the distinct `visual-runtime-test-v1` SSH signature namespace
and the TEST Visual signing principal. Verify that the existing TEST signer
policy allows that namespace, without giving it a PROD permission. The
companion verifier does not grant any SSH command or restart permission.
No runtime or native Sites publish occurs automatically in this patch.

## Needed in the existing Visual TEST lane

- The existing `assertion-memory-visual-test.service` listens on loopback
  port 8829 as `assertion-visual-test-read`. Its root-owned fixed script
  currently lives at `/etc/assertion-memory-visual/test/asset-origin.py`.
  Use a one-time, separately approved root-owned runtime pointer/launcher
  preserving this unit, port, identity and sandbox. Import the exact current
  script as the initial immutable rollback version. Scope a fixed hook to
  only this unit and the verified TEST Visual artifact, never shell access,
  arbitrary argv, Base state, shared TLS, or PROD.
- Bind the companion's static SHA to the accepted exact Visual payload.
  Save new verified runtime files in fixed revision directories, preserving
  root authority and a stable read-only pointer for the serving account.
  Treat `sites-shell.mjs` as source for the separate protected Sites
  publisher; do not execute it on the VM.
- After the restart of this same service, check the running runtime generation
  and exact static asset at the local origin, then authenticate and read the
  TEST queue via the scoped transport against current Base. If a check fails,
  restore the prior runtime, restart and prove the prior generation. Keep
  rollback unavailability explicit; a pending marker fences further releases
  until protected recovery confirms the state. Do not equate pointer identity
  with process identity.
- The trusted installation/controller wiring above is intentionally not
  present yet. Do not enable the native Sites queue route until the existing
  Visual release job can prove both runtime and Base availability. Native
  Sites activation remains the existing owner-gated TEST operation; no
  changes are required to PROD or the existing Base release workflow.

The protected Visual TEST job leaves this path dark while
`VISUAL_TEST_RUNTIME_REQUIRED` is unset or false. It never reads a companion
for PROD. The opting-in var belongs to the existing protected TEST environment,
not a dispatch input. With true, current staging validates the exact companion
before the first host operation, then uses only the scoped signed-pair command.
An old or unprovisioned host denies this command before pointer changes. Neither a missing companion nor
an unprovisioned receiver allows a green deployment while this var is true.
Older static-only Visual releases continue to work in the default dark mode.

The missing host grant is **only** activation/rollback restart of
`assertion-memory-visual-test.service` by the protected Visual TEST receiver.
Bind it to a fixed root-owned helper that checks the signed exact revision,
static payload SHA and companion SHA, acquires the existing Visual release
lock for both payload pointers, and starts only this one unit using sterile
fixed arguments. No `sudo systemctl *`, shell, Base unit, environment
forwarding, generic file overwrite or PROD authority. Restore both accepted
pointers and verify the previously running service before the lock releases
if activation/health fails. The one-time install of that hook and the
existing unit launch-path conversion require explicit host-owner approval.

### Joint receiver implementation seam

`runtime_receive.receive` admits a new TEST-only framed command, checks the
existing protected TEST Visual principal's signature in a separate namespace,
then verifies both complete archives before invoking a preinstalled callback.
`runtime_joint.run` provides the canonical Visual deploy-lock transaction
for the independently prepared static and runtime pointers. Its durable
journal records both old targets even when the static checksum is unchanged.
Its callback must restart the existing service and prove the running code
returned the expected generation; a failed readiness check restores both
old pointers and verifies the old service.

The included `configured-host/runtime-test-restart.py` is the only proposed
privileged helper. Install its exact root-owned file separately, pin no
arguments, and grant only to the existing TEST deployment writer, not the
SSH caller's general account. It restarts exactly the existing Visual TEST
unit. The unit itself still executes released code as the existing restricted
Visual read user, never as root.

The source wires the configured TEST receiver and rejects pending joint
recovery in the legacy writer under the same lock. Provisioning still
requires independent review of the fixed launcher and restart permit,
trusted `runtime-enabled` marker (exact `on` line), and separate fixed
`runtime-health-token` credential shared only by the TEST Visual origin and
deployment writer. Expose no health route at Gateway or Nginx. The unit
must load the health credential plus its separate private Base queue token
and launch `runtime/current/asset-origin.py` as its existing non-root user.
Seed a health-capable known-good runtime and verify the current service
before enabling, so rollback can demonstrate an actual healthy process and
read the local TEST Base queue, not merely restore files. Base, PROD, and
an unprovisioned host path remain dark.
