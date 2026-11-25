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

## Standard owner-only web backend and queue updates

The hosted viewer uses its existing same-origin Graph web backend. Its optional
queue dashboard reads `GET /api/v1/capabilities?queue_status=1&limit=50&offset=0`
through the existing graph read bridge. No new public origin, Sites publish,
Gateway URL, token, or MCP tool is needed **provided the current authorized
bridge forwards these query parameters**. This opt-in requires the deployed
graph service to be given its verified lane's canonical Base scheduler config
(`--queue-status-config .../state/http.json` and `--queue-status-stage test|prod`);
verify the actual mounted source, config names/permissions and active release
directory on TEST before enabling. No auto-discovery guesses lane, Base
version, or scheduler settings. This branch doesn't change installed units.

Each queue reports its own scheduler order, active state and recorded processing
phase. Jobs that are executing do not claim a waiting position. Completed jobs
leave the active list; the view does not infer success from disappearance.
The interactive queue only has its persisted execution step (`query`), not a
model substage. The API and journal do not expose prompts, replies, model
results, event bodies, failure details or credentials.

The existing EventSource URL has an independent channel:
`GET /api/v1/events?channel=dashboard`. It shares the owner/read-session boundary
and graph journal, but only emits bounded `queue_changed`, `graph_changed`,
or `resync` invalidations. The queue watcher detects committed SQLite updates
without holding a transaction. Clients fetch authenticated snapshots on
invalidation, reconnect and periodic fallback; transient DB states between
snapshots are not guaranteed observations. Dashboard event IDs are
`<lane>:<random server epoch>:<integer cursor>`; foreign lanes, retired epochs,
ahead/expired cursors and missing IDs resync. Queue body storage is not added.
Streams recheck authorization, have a connection cap and reconnect after a
bounded window. The older graph EventSource remains compatible.

A future `traversal` event should contain only validated `stage`,
`correlation_id`, `from_node_id`, `to_node_id` (opaque canonical IDs) and, if
needed, a fixed direction/phase enum. Only a trusted in-process producer,
authorized for the same Base deployment, may emit it into a bounded journal;
there is no public submission endpoint. Do not derive IDs by parsing prompts.
Check epoch/lane and graph revision; highlight only endpoints found in the
selected graph, never turn traversal events into query history. No traversal
publisher or live animation is enabled here.

Before rollout, exercise the current authenticated TEST URL end to end:
request two distinct SSE frames while its GET remains open (not just verify
HTTP 200), change an isolated job's persisted phase, check the new snapshot
and reconnect with a prior ID. If the native Sites/API Gateway response
buffers or filters query parameters, rely on its approved same-origin snapshot
polling while resolving streaming support; do not add a direct public origin.
The regular owner web session still provides exact-origin and CSRF security
for its own mutations; the graph read bearer cannot enroll devices or clear
the database. Those operations need their real independent backend wiring.
