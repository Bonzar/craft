# Scoped Graph readiness for memory reset

This is deployment source, not a live change. Do not expose the reset UI until
both canonical readers independently prove readiness and all browser ingress
denial checks pass. Visual is the sole caller and uses its existing lane-local
`standalone-graph-token`. Do not enable `--bridge-read-token-file`: it is a
different, broader capability. The new token authorizes only an exact GET to
`/api/v1/capabilities?database_clear_scope=1`; it cannot log in, fetch ordinary
graph data, access another lane, or mutate anything.

1. Under staffed deployment controls, record both active Base/Graph release
   identities, both Visual static/runtime identities, unit ownership, existing
   process sockets, and authorized owner graph read health. Capture the exact
   effective Graph ExecStart as a small data file without secrets. Do not export
   complete service or environment configuration. Use the tested published
   Base version implementing the dedicated capability in both lanes.
2. Validate the renderer output for the matching unit and exact TEST or PROD
   loopback destination. Keep an existing unknown command or auth setup
   unchanged if the renderer rejects it. Stage the named existing lane token through the versioned
   root system-manager template `assertion-graph-reset-credential@.service` and
   its fixed-path helper into the Graph-user-only `/run` credential. The
   user-manager unit does not need read access to the root-owned original; an
   absent credential leaves Graph running and readiness denied. Never use a cookie, common token, Base write token, unit
   environment variable holding a literal key, or group-readable copy. The
   root credential source and Visual runtime credential must remain untouched;
   the Graph service sees only its same-lane owner-mode-0400 regular file. Track the
   installed helper and system/user unit drop-ins as ops release assets. Rotating
   the Visual graph service key requires re-staging/restarting the matched pair
   under its normal deployment controls. In the absence of a matching key, the
   clear affordance remains disabled while all normal owner reads continue.
3. Treat the staged unit and credential mapping as a deployment-managed pair;
   qualify TEST without changing ingress or attempting a reset. Confirm Graph
   still serves the same owner page, manifest, binary buffers and existing SSE;
   normal owner auth and scheduler state are not changed. Invoke only the local
   read-only readiness GET via the existing authorized Visual service key.
   Return boolean stage and digest-match results only, not the opaque scope.
   No bearer, wrong-lane bearer, wrong method, scope query exposed via browser,
   and ordinary Graph endpoints using only the readiness key must all be denied.
4. Apply the identical staged operation to protected PROD only after TEST
   verification. Until then, leave clear ingress closed and UI unavailable.
   On any mismatch or listener failure, remove only the exact new drop-in,
   reload the manager, restore the prior unit/credential mapping, restart only
   that Graph and verify prior owner/direct health. No Base/Visual downgrade,
   pause change or DB mutation is part of this provisioning.
