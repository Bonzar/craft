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
bridge forwards these query parameters**.

The Graph reader never opens Base's private HTTP or relay configuration. The
verified Base listener publishes a small lane-local in-use projection from its
already loaded scheduler config, real QueryJobs instance, and verified release
identity. It contains only the stage, Base release/generation, canonical DB
device+inode and scheduler control values; no backend config, paths, endpoints,
keys, SQL/HTTP/queue payloads. The Graph process checks the publisher's service
UID, a live file lock, the exact lane and its existing canonical source inode
on each read. No publisher, wrong inode/lane, or stale unlocked data is
unavailable, not an idle/empty queue.

### TEST provisioning preconditions (not applied here)

- The tracked installer runs its Base and local graph user units
  `assertion-test-deploy-mcp.service` and `assertion-test-deploy-graph.service`
  under `assertion-test-deploy`. Their existing `state/http.json` is user-owned
  with creation mask `077` inside a mode-0700 deployment root/state. The repo
  does not pin the live Sites graph ingress unit/UID. Before enabling, inspect
  exact systemd unit identity and verified loopback target, no secrets or full
  Environment/ExecStart dump, and confirm that existing Graph source is
  **already** the canonical TEST database at
  `/srv/assertion-release/test/state/fictional.sqlite3`. If not, stop. Do not
  widen existing DB, deployment root, HTTP or private config permissions.
- Provision only `/run/assertion-memory-dashboard/test` (parent root-managed,
  per-lane path owner `assertion-test-deploy`, 0750 or 2750, group restricted
  to the publisher and verified Graph reader). Give the runtime services only
  the new directory group needed to publish/read metadata. No PROD config or
  group grant. The Base service creates `scheduler.json` as 0640 and a
  publisher-only technical lock as 0600; nothing else is shared. Neither path
  is under the public Graph asset root. If the same service UID is used for
  both, its existing private group is sufficient.
- Release the exact tested Base build before restarting the verified TEST
  graph reader with `--queue-status-config
  /run/assertion-memory-dashboard/test/scheduler.json --queue-status-stage test`.
  Preserve all other service arguments and authentication. Check status via
  owner Sites plus negative missing-auth/wrong-lane requests. When rolling back
  to a Base build without the publisher, the queue status becomes unavailable;
  existing graph, MCP and Site graph access remain intact.
- The current official Base implementation gives private QueryJobs only when
  the verified deployed `intake_process` capability maps to
  `durable_query_jobs`. The projection reports the actual constructed queue
  instance, not a second capability table or guess from the UI. A missing or
  disabled queue is not silently represented as active.


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

### Sites-facing TEST graph reader

The existing Sites wrapper at `/opt/assertion-memory-site-test-graph/start.py`
imports the release-managed Graph web service without CLI dashboard options.
For this verified TEST-only reader, its systemd unit may set only the two
nonsecret options below in a reviewed drop-in:

```ini
[Service]
Environment=ASSERTION_MEMORY_QUEUE_DASHBOARD_STAGE=test
Environment=ASSERTION_MEMORY_QUEUE_DASHBOARD_PROJECTION=/run/assertion-memory-dashboard/test/scheduler.json
```

The fallback requires the actual and effective `assertion-test-deploy` UID,
the exact canonical TEST source, exact projection path and live active Base
release source. It stays dark for PROD, mismatched releases, unknown identities
and invalid settings without disrupting ordinary graph requests. The runtime
projection remains independent of Graph and never exposes configuration or
write capabilities. Deploy the Base release (source verified via
`current`) before restarting the existing Sites-facing unit. No edits to the
wrapper source are needed. Native dashboard query and streaming smoke checks
are still mandatory.

### Stable TEST Sites visual link

The owner-only TEST aliases `/visual` and `/visual/` should redirect to
the current visual manifest digest on the same Site, with the original
query and no caching. They do not serve the pinned fallback as "latest";
failure of the current-pointer origin means unavailable. Existing root,
pinned history URLs, and PROD behavior are unchanged.

The native TEST Site router is not built or published by the Visual TAR
installer. `native-test-visual-alias.mjs` is the exact reviewed source
transformation for the v25 private `visual-pair-shell.mjs`. It detects
preimage and output drift and writes a new file, with no network calls.
The private full Site source is not checked into this repository.

To make this a release-controlled native stage, package the tracked adapter
from the same immutable GitHub Visual release commit as a separately
named companion (not another release or an alternate UI archive). Record
its SHA-256, exact source revision, and upload ID in the authorized release
evidence. The existing native release operator stages this artifact against
the verified private TEST project, comparing the native checkout and
published version before changing anything. An operator-controlled native
publication must be recorded as part of that Visual release, preserving
the existing project, plugin, audience, owner, environment and other files.
No present workflow publishes native Site routers automatically.

For the one explicitly approved rollout, emit the deterministic companion
from the exact GitHub Visual tag commit that the build already verifies:

```sh
revision=$(git rev-parse --verify "refs/tags/${VISUAL_RELEASE_TAG}^{commit}")
test "$revision" = "$VERIFIED_VISUAL_BUILD_REVISION" || exit 1
git archive --format=tar --output=assertion-memory-visual-native-test-alias.tar   "$revision" tools/assertion-memory-visual/native-test-visual-alias.mjs
sha256sum assertion-memory-visual-native-test-alias.tar
tar -tf assertion-memory-visual-native-test-alias.tar
```

Run this only in the selected original Visual build or its approved,
same-commit release evidence stage. If uploading in Actions, use exactly
`assertion-memory-visual-native-test-alias`, with no overwrite; retain the
artifact ID and TAR SHA with the exact build run and revision. Do not use the
companion as the VM Visual archive, and do not add this output to routine
Visual releases. If the existing release gate cannot supply the companion
provenance, stop instead of substituting a later build or publishing a loose
file. The existing native publication still requires an approved operator
and proof from the original private site.

After the release-controlled publication, prove both alias paths through the
existing authenticated TEST Site, no redirect following before comparing:
302, same-origin `/visual/<expected digest>/`, exact original query,
`x-memory-visual-digest` matching the Visual TEST receipt, no fallback
marker, no-store, and matching bytes at the target. Check that unrelated MCP
and graph routing is unchanged, a retained historical URL still pins the
old release, and unauthorized requests do not reach the asset gateway.

For a separately authorized PROD rollout after verified TEST, use the
same reviewed native source adapter with explicit third argument `prod`.
Its expected output digest differs from the TEST output. This creates
only a PROD alias in the existing PROD source; it does not enable a
cross-lane alias, modify the already deployed TEST version, or confer
permission to push or publish. Verify the exact private PROD source
preimage and owner ACL/MCP identity separately before staging.


### Background processing pause (standard TEST owner web backend)

Pause is a separate durable setting in the canonical SQLite file. It does not
change session allowlists, enrollment, raw intake or interactive question
processing. Source capture still enters the queue. Every new source and
maintenance claim checks the setting in its own SQLite write transaction;
work already claimed keeps its lease and may finish. Resuming wakes the Base
worker without waiting for another capture. Queue snapshots truthfully show
paused rows without a forecast position, and existing running rows retain
their phase. A pre-feature Base rollback cannot enforce this new setting;
do not advertise pause on such a rollback.

A standard authorised owner web app can opt in via `--queue-control` together
with exact canonical TEST status configuration. This requires the existing
owner session/password verification, same origin, valid CSRF token and active
TEST release lease. It is **off** for every default/native Sites/PROD startup.
The existing Graph read bearer and published Sites pseudo-session intentionally
never authorize mutation.

- Read: existing dashboard status includes
  `processing_control:{paused,revision,can_change}`. `can_change` is only true
  for a real owner cookie and explicitly enabled standard TEST backend.
- Write: `PUT /api/v1/processing/pause`, JSON exact
  `{stage:"test",paused:boolean,expected_revision:"32 lowercase hex"}`,
  with existing `x-csrf-token` and owner cookie. Success is
  `{stage,paused,revision}`. A stale revision or wrong stage returns 409
  (fetch status again); unavailable release lease returns 503. No query,
  message or administrative SQL API has been added.
- Native Sites V25 is read-only: display the pause state, not a working
  control. Only wire a future purpose-built owner mutation adapter after
  preserving the account owner proof and exact lane/Origin/CSRF semantics,
  without extending the existing read-only graph credential.

Device enrollment still needs a release-managed, owner-only enrollment
adapter with a per-device revocable fingerprint registry and no raw bearer
secrets in UI requests. Database reset is a separate capability; explicit
typed single-use confirmation and an independently verified stop/drain
supervisor are prerequisites. Never expose either through this pause API or
the Graph read bridge.

### Replaying an old Visual release without VM history

Use the existing **Visual Release** entry in GitHub Actions on protected
`main`, with the exact immutable tag (for example
`assertion-memory-visual-v0.1.2`). It resolves the original Release build
and downloads its archive from GitHub into a fresh runner; it does not use
the VM as the source of the old package. TEST must pass for this run before
approving PROD. PROD binds to the same run, revision, original archive ID,
ZIP SHA, payload SHA and manifest digest. Do not retag the old bytes, copy
an old directory onto the VM, or publish a substitute build under the tag.

For the current resolver an immutable GitHub Release tag is not enough:
the original build uploaded an Actions artifact retained for 90 days.
Missing/expired artifacts or changed bytes stop the run before activation;
an old build on the VM is never a fallback used by this path. Do not prune
the last restorable copy of any rollback point that must be kept until its
GitHub replay is proven over the required retention period.

A durable extension should follow the protected current-builder pattern:
an old tag is inputs only, not a workflow or executable builder authority.
Run a reviewed recipe from protected main in a separate no-runtime-secret
build job, package only pinned Visual UI assets, and require an immutable,
independently retained original payload/manifest proof before recognizing
the result as byte-identical. Old tag tooling and this package recipe have
changed over time. Do not imply the current builder can reproduce every
tag without a revalidated recipe/proof, or use a GitHub Release's source
ZIP as if it were the original UI TAR. Until durable bytes/proof and its
standard controller verification exist, refusal after expiry is deliberate.

`test_configured_visual_replay.py` exercises the standard configure/resolve/
unpack bindings with independent artifact-only temp runners, plus exact
relayed payload validation and fail-closed expiry/missing/changed cases.

## TEST queue REST migration (default off)

The Visual origin can carry the existing owner-authenticated queue page
without importing Base Python code or opening SQLite. The owner-checked
Sites shell maps the existing GET to a fixed `queue_status` operation on the
existing TEST Visual transport. The Visual runtime calls a configured local
Base HTTP origin at `GET /api/v1/queue-status?limit=50&offset=0`; each page
has a 1–50 limit and offset 0–1,000,000. Its systemd credential
`queue-base-token` is dedicated to this read and is distinct from
`asset-token`. Browser/Sites headers never become the Base credential.
The origin is loopback HTTP only in this phase; separation across hosts
requires an explicitly approved private HTTPS transport, not a public route.

The TEST Visual unit must supply `MEMORY_VISUAL_TEST_BASE_ORIGIN` and mount
the service credential before the TEST Sites owner publisher enables
`MEMORY_VISUAL_TEST_QUEUE_REST=true`. Once enabled, the exact capabilities
page fails unavailable on bad/unavailable Base data; it never silently reads
the old Graph queue path. Ordinary capabilities, Graph reads/event streams,
and PROD are unchanged. The first phase transfers no mutation authority.
The Sites native paired shell must call `testQueueRead` inside its existing
owner/scope check using the already authorized TEST asset transport; do not
replace the owner boundary or publish this reference shell as a whole.

The current signed TAR intentionally holds four web files plus its
manifest. Installing the origin runtime is a separate existing host concern;
do not add executable files to the web TAR. An independent Visual rollout
needs an explicitly digest-pinned runtime companion from that exact Visual
build, a scoped activation/rollback hook for the existing TEST Visual origin
service, and an owner-authenticated browser check. No new listener, public
Base endpoint, or Base release restart should be necessary for Visual-only
changes after the internal Base read endpoint exists.
