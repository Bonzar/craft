# Private Visual without Sites

One existing loopback Visual origin process can serve the immutable
frontend, same-origin owner read APIs and versioned UI. It supports exactly
the same runtime code for TEST and PROD, with distinct protected ports and
credentials. It consumes the queue from that lane's local REST Base, and
uses only the lane's separately authenticated local Graph HTTP reader for
legacy graph/search/SSE. No raw Base SQLite file or protocol code is opened
by Visual. The default remains read-only; old private Sites routes and MCP are
untouched. Optional pause authority is documented separately below. Reset
and enrollment are not supported.

## One artifact

`standalone_bundle.py` builds one deterministic ZIP containing exact
`visual.tar`, `runtime.zip`, and source receipt. The original Visual build
publishes it under an exact separate artifact name; the old artifact
formats remain for unchanged workflows. The source-only resolver verifies
the exact original build and provider digest before unpacking.

This change deliberately does not switch the protected Visual Release
entry. After source review, a separate change pins the Visual-only resolver
and connects the same-run packed TEST witness before PROD. A TEST-only
success cannot authorize production: separately provision the lane-specific
PROD signer, receiver and verified health/rollback baseline first. No
current workflow setting or Base release is changed here.

## Ingress prerequisites (not provisioned)

**Prefer dedicated DNS/TLS virtual hosts.** Confirm two DNS names pointing
to the intended VM and obtain valid certificates for those names. Keep the
old IP-certificate vhost, Sites ingress and all other applications intact.
Mount owner authentication per vhost, for example Nginx Basic Auth with
a separately generated high-entropy owner credential kept in an approved
password manager and a private hashed-password file. Do not reuse Sites
identity/credentials. A previously configured compatible owner-auth gateway
could replace Basic Auth only after independently confirming its real owner
binding, security, and trusted forwarding. No OAuth client is assumed.

**Managed HTTPS alternative.** A dedicated managed gateway can provide
its own named TLS origin only if it actually authenticates the owner before
sending any request to the fixed authorized Visual facility, does not expose
credentials to the browser, and preserves long-lived event streams (or clearly
uses tested polling). The existing bearer-protected asset Gateway URL proves
none of those browser auth requirements; do not use its bearer as browser
access or add any public Base route.

For each lane the proxy must:

- Match exact HTTPS host, terminate valid TLS and authorize the owner before
  routing any path (including assets, graph APIs and events).
- On the private local upstream set the exact public Host and replace
  `X-Memory-Visual-Owner-Ingress` with an independently provisioned
  64-lowercase-hex server credential. Discard every inbound copy of the
  marker and browser `Authorization` (after authenticating), cookies and
  untrusted forwarding/identity headers. Preserve browser Origin only for
  deny checks and pass bounded Last-Event-ID for read replay.
- Forward only read routes for this owner service; do not publish
  `/__visual-runtime-health`, legacy POST assets, Base, or a generic relay.
  Configure no buffering for the SSE API. Verify a nonowner cannot reach
  any route or receive an upstream response.
- Configure the existing Visual unit with exact
  `MEMORY_VISUAL_STANDALONE_ORIGIN`,
  `MEMORY_VISUAL_BASE_ORIGIN` and `MEMORY_VISUAL_GRAPH_ORIGIN`, and mount
  its private credentials `standalone-ingress-token`, `standalone-graph-token`,
  and `queue-base-token`. Never add actual credentials to logs or source.
  Keep these distinct from asset/health credentials. If the PROD Base REST
  endpoint is not available, queue reads are explicitly unavailable.
  Preserve existing Sites/asset health settings.

Review the prepared exact vhost and credential scope, then test owner and
nonowner from a real browser on TEST and prove queue, graph and SSE. Revert
only the new vhost/drop-in if needed; old Sites remains untouched. Repeat
only the required protected PROD promotion/health using the same immutable
bytes and its own trust. Local fixtures exercise the browser-shaped path,
not actual DNS/TLS, owner login or published PROD.


## Active frontend and rollback

The standalone HTTPS route serves only the digest selected by its lane's
atomic active pointer. A direct request for inactive retained HTML, JS or CSS
returns 404. A protected GitHub rollback changes the active pointer first.
The legacy Sites asset protocol retains its current digest compatibility
until cutover.

## Optional owner-only pause and resume

The same package supports both lanes, with its pause capability off by
default. Database reset, device enrollment and application writes are not
part of this feature. Roll out the compatible Base queue-pause REST endpoint
first. Visual validates the exact configured stage and in protected service
startup permits pause only against Base on 127.0.0.1:8893 (TEST) or
127.0.0.1:8903 (PROD). Base's pause handler is not routed publicly.

For each lane, bootstrap the dedicated HTTPS owner vhost and verify the
owner authenticator before mounting mutation paths. On the exact
capabilities-read, POST `/api/v1/processing/pause/prepare` and PUT
`/api/v1/processing/pause` locations, remove any inbound
`X-Memory-Visual-Pause-Owner-Ingress` instances and set exactly one new value
from the proxy's private per-lane credential **only after successful
authentication of the configured owner**. Never populate it from an inbound
header, cookie or unverified identity. Suppress this header elsewhere. For
example, static Nginx `proxy_set_header` replacement belongs only in exact
locations with their own required owner auth, not a generic API catch-all.
Use both ingress markers and preserve the browser Origin for the request
check. Deny cross-site and non-owner calls; do not expose the Visual loopback
listener to any other ingress. Leave Graph GET and SSE behavior unchanged.

Mount the six protected Visual service credentials using the existing
service credential mechanism, only readable by the intended service
identity (one set per lane; no source/CLI arguments/browser secrets):

| File | Purpose |
| --- | --- |
| `pause-owner-ingress-token` | dedicated opaque marker installed by the owner-auth proxy |
| `pause-owner-digest` | expected single owner digest, also pinned by Base |
| `pause-read-token` | local GET pause readiness/state only |
| `pause-write-token` | local PUT, accepted only with the signed owner proof |
| `pause-csrf-key` | lane/origin/revision-bound short-lived browser proof |
| `pause-proof-key` | exact-request HMAC secret shared only with Base's pause verifier |

Each value is 64 lowercase hex (the two key files decode to 32 bytes).
The read/write fingerprints belong to that lane's Base config; the write
operation is always checked with a fresh nonce and compared against the
current revision inside Base's transaction. No credentials or owner claims
from the browser are forwarded to Base. A successfully authenticated UI
only shows the button after the real prepared state matches its queue
snapshot. Base still makes the final decision; 409 refreshes, 503 never
looks successful. Pausing affects new source/maintenance claims only, not
interactive requests or already leased work.

After proving the exact proxy and Base authority, explicitly set
`MEMORY_VISUAL_STANDALONE_PAUSE=1` in that lane's protected service drop-in;
otherwise the routes remain unavailable. Prove from the real owner URL:
nonowner/foreign Origin/forged marker denied, owner pause and resume each
return a changed revision and persist, stale revision fails, graph/SSE and
interactive work remain usable. Test this on TEST before independently
provisioning and verifying PROD. If any check fails remove the opt-in and
the dedicated proxy locations; leave normal read routes and Sites untouched.

## Signed packed PROD bootstrap (source, default off)

A packed Visual release uses a lane-neutral runtime receipt and identical ZIP
bytes in both stages. The old standalone runtime companion remains v1 and
TEST-only; it cannot be reinterpreted as a PROD package. The packed controller
requires the approved same-run TEST static and packed SHA witnesses before it
can send a PROD command. Both stage flags remain separate and off by default,
and differing TEST/PROD format selection fails instead of serving static only.

The closed PROD runtime scope is the existing
`assertion-memory-visual-prod.service` on `127.0.0.1:8830`. Its process stays
`assertion-visual-prod-read`; its existing deployment writer stays
`assertion-visual-prod-deploy`. The following one-time changes require separate
review and are not applied by this patch:

- Verify the configured PROD signer file permits the distinct
  `visual-runtime-prod-v1` namespace for the existing `visual-prod` principal.
  Install reviewed receiver modules root-owned in the existing PROD scope.
- Install the same fixed restart-helper source at the exact scope-specific
  `configured/prod/runtime-prod-restart.py` path, with a permit only from the
  existing PROD writer and only for that no-argument helper. Retain sandbox
  and fixed unit; do not add any account, generic shell or cross-lane grants.
- Provision the scoped `runtime` directory and healthy prepared baseline
  under the current PROD Visual state root using its existing writer/group
  ownership. Change the existing unit's launch path to immutable current
  runtime and configure its separately issued health credential and a
  verified local PROD Base read credential/origin. Baseline smoke must prove
  running entry hash, active UI digest and successful same-lane queue read,
  so rollback can be verified. Keep health loopback only.
- Only after preflight, independently approve the root-owned PROD
  `runtime-enabled` marker and protected workflow settings. Advance the
  existing Visual-only workflow to an exact reviewed packed controller pin
  and publish a fresh immutable Visual release through the original producer.
  No static-only success substitutes for the packed witness.

This source does not configure, restart or enable a live service. The
default legacy release command remains unchanged in both environments.


The root-owned legacy bootstrap policy may authorize a single signed PROD
pair from the exact pinned old static and script hashes. With it armed, the
current release lock protects an optional copy of the intact old script as
rollback, a durable pair journal, and conversion of that journal to a consumed
seal only after modern runtime health. Legacy proof uses the existing fixed
old service, existing scoped authenticated asset read, and a newly started
matching process. Old health never counts as a deployed new runtime. Without
approved existing scoped legacy health access, do not arm the policy.
