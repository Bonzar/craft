# Assertion Memory visual release

This builds the four pinned native QA v2 rendering assets. Verify the MIT
notice included with Three.js. The tree contains no Site project state,
owner-specific credentials, graph snapshots, database or personal content.
The compiled QA assets are not recovered editable sources.

The visual builder retains the exact approved raw TAR and canonical JSON
manifest. Shared assembly validation checks those bytes without repacking,
then shared strict-host SSH carries a visual-scope signed envelope and
unchanged payload. TEST and PROD have separate transport keys, signature
policy and scope. The workflow verifies immutable source/build provenance
and PROD must reference the exact successful visual TEST receipt.

The VM uses one shared release implementation for staging, digest/manifest
qualification, atomic `current` symlink, synchronous origin smoke and
rollback. The visual adapter allows only the four declared assets and
manifest; all retained versions are under `releases/<manifest-sha256>`.
Clients already pinned to prior releases keep their versioned URLs. This
does not make Github's workflow artifact retention unlimited or claim
automatic rollback for a separate ingress or Sites failure after the
receiver has returned.

Install shared code once at `/etc/assertion-release/shared`; configure
independent root-owned visual scope entrypoints, fixed restricted shell and
signing policy. Do not deploy the previous detached pointer-file receiver.
Do not enable either scoped visual deployment until receiver negatives,
manifest/pointer, gateway and private Sites tests pass on the real host.
The synthetic tests do not qualify the actual attached services.

## Owner-only queue and status panel

Visual release assets fix the graph to Connections. The Visual tag is
embedded in the hashed HTML at the original immutable Visual build; the
separately reported Base version and TEST/PROD come from the scoped service,
not URL parameters. The owner-only queue display is a read-only SQLite
snapshot: independent source, maintenance and interactive queues, current
claimable order within each queue, no promised cross-queue or completion
order. It sends job IDs, dates and stages, not messages or prompts. Ownership,
reset and enrollment must not be inferred from access to the visual graph.

The Base release includes the optional ASGI status handler, dark unless the
root-owned lane configuration supplies `visual-status.json` with the exact
owner ID SHA-256 and exact lane-local token file reference. The secret
`state/visual-status-token` must be distinct from graph, MCP, and machine
credentials and is never a public build input. Missing/unusable opt-in
configuration leaves the existing Base service routes unchanged. The fixed
Gateway route to the matching VM path must be separately verified; it maps
the dedicated authenticated Sites relay header and does not expose the internal
route as a generic proxy. Both enrollment and reset remain disabled; the latter
also needs an audited independent process that can quiesce and restore the
lane's actual workers and readers.

The Visual archive carries only the four browser assets plus the manifest,
not the owner-authenticated Sites fetch composition. The Base build also
produces the `sites-mcp-bridge-<release-id>-<commit>` companion, but no committed
workflow currently consumes/publishes that artifact. To stage the status
relay through releases, the native release controller must consume the exact
verified archive plus the two release-owned Visual relay modules; obtain each
existing private Site and revision using the authenticated provider; compose
`makeVisualAdminRelay` once in that Site's verified owner-only route stack
before asset fallback; preserve existing access and plugin identity; publish
the immutable source only after the corresponding release gate; and preserve
native readback/receipt for TEST before promoting the same source to PROD.
Do not mistake browser archive activation for installation of the Sites relay.
The read-only Base route, the owner-only Sites bridge and the Gateway route all
need authenticated in-lane smoke checks before advertising working status.
