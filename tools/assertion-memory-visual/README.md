# Independent assertion-memory visual releases

This candidate builds the Graph Viewer from **the selected public main commit**. It does not
copy any private hosted export. The exact currently deployed native bundle is a newer separately
built renderer and cannot be reproduced from this main branch. The release pipeline verifies
the checked-in viewer, not native visual parity, a physical phone, or database behavior.

## Release process
1. Publish an immutable GitHub release tagged `assertion-memory-visual-vX.Y.Z`, selecting a protected
   main commit that contains this workflow and the relative Vite build flag. Its original run
   publishes one artifact from that commit. Verify the artifact ID, source SHA, TAR digest and
   manifest digest as audit evidence. Reruns and duplicate original artifacts fail closed.
2. Dispatch the visual workflow on main using that release ID to start TEST. It verifies the
   original successful release workflow, GitHub artifact digest and TAR receipt, transfers
   exactly that TAR to a scope-specific restricted VM receiver and checks the gateway returns
   all four approved bytes. This is not an owner-visible rendered/browser test.
3. Only after TEST succeeds, approve the protected PROD job for that same run. It independently
   resolves that original artifact, requires matching TEST outputs and the required workflow
   review, then sends the same TAR to a different receiver account and verifies the PROD visual
   gateway. No database migration, restart or data is part of this release. A later release
   does not mutate an existing release directory; previous versions remain for rollback.
   A network smoke failure after the atomic switch stops the workflow and requires operator
   reconciliation or restoration of an independently approved previous release; the candidate
   does not claim network-aware automatic rollback.

## Provisioning intentionally off
Do not set repository `ASSERTION_MEMORY_VISUAL_READY=true` until the visual-only environments,
trusted reviewer and receivers are verified and deployment separately approved. Use distinct
`assertion-memory-visual-test` and `assertion-memory-visual-prod` environments and never reuse the base
deploy identities. Each grants only the distinct forced-command `assertion-visual-<mode>-deploy`
key with no PTY/forwarding, pre-pinned host key and corresponding visual asset smoke token.
Set the configured required visual production reviewer and do not allow bypass. Missing provisioning,
missing receipt, wrong environment, untrusted origin, changed artifact and absent approval all fail.
Run the root-owned `receive.py <mode>` as that environment's writer; root must independently set
the exact approved TAR SHA in its own non-group-writable config approval file. The same workflow/
SSH key must not administer the approval file. The simple independent gate means the candidate is not
yet literal three clicks without provisioning an independent approval/attestation mechanism.

Run `asset-origin.py <mode> <port>` behind the existing authorized TLS ingress, as a separate
read-only account with only that environment's release tree and one systemd credential
`asset-token`. Use explicit loopback ports and fixed TEST/PROD POST routes only. No new public
listener. Verify end-to-end TLS identity and exact body/MIME (no redirects, no gateway cache).
Use native owner-only Sites with the generic `visualShell(mode, graph)` adapter and the existing
read-only graph handler. Each Site has a distinct verified host, its own dedicated asset key, a
verified gateway hostname and separate graph credentials. Native project IDs, manifests, native
Sites versions and runtime config do not belong in this repository. Anti-spoofing of identity headers
depends on the native owner-only boundary; none are forwarded. The browser sees only its own origin
with private/no-store responses and a version digest which pins HTML/CSS/JS and lazy Web Worker.
Protect source and its CI equivalently to graph read access: shipped same-origin JS can use the
owner's read-only graph session. Never upload an arbitrary JS archive from workflow inputs.

Settings and secrets are documentation-only here; dashboard, VM, Sites, Gateway and PROD behavior
were not changed by this code candidate. Main has a different renderer from the private Site;
do not switch TEST or PROD until previewed and accepted. Local tests cover source-token boundaries,
release provenance validation, temporary filesystem and token-only HTTP, not live services.

## Verified renderer gap
The main-source public renderer limits ordinary assertion stars to screen-space point
sprites (12–20 CSS pixels); it does not contain the newer native solid-world node shader.
Consequently the mobile world-radius cap from the private release has no public counterpart
to patch, and matching current bounds is not evidence that the newer controls or renderer
were reproduced. The public-source readiness banner now independently suppresses informational
codes and de-duplicates warnings, while preserving actionable warnings and the existing
snapshot/fixture indicators. The current native export additionally exposes session filters,
session-run controls, a topic toggle and a dedicated source-limitation indicator that this
historical GitHub viewer does not render. Treat its versioned source build as an infra test
only until the maintained newer source is located and independently tested.
