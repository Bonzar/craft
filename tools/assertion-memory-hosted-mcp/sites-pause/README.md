# TEST native Sites pause adapter (default dark)

This is a narrow **pause only** route, independent of standard Graph owner
sessions, Graph read bearer, MCP scopes, device enrollment and reset. It never
routes HTTP or methods provided by the browser onward. No code here publishes
a Site or changes its access settings, ingress or runtime.

## Native integration

Use the release-authorized controller to stage the existing private TEST Site
from its exact saved source, retain project/plugin IDs and ACLs, and import
these two modules. In its verified V25-style entry module, wrap
`withVisualAtMcpOrigin('test', visualGraph, previous)` with
`attachTestPause({legacy, origin, gateway, withinEnrolledOwner: matchesEnrolledOwner})`.
Use the existing real `matchesEnrolledOwner` from `test_owner_enrollment.mjs`.
Set the immutable TEST Site origin and existing TEST Gateway hostname with the
new exact route suffix in the release composition; not user query/headers.
Do **not** use a Boolean presence check for platform headers, the synthetic
probe predicate, an env identity fallback, or native /session pseudo-auth.
The existing Graph owner email hash and exact native D1-enrolled owner must
both match. Verify the published private Sites entry really authenticates and
strips/overwrites client spoof attempts before activating the feature.

Protected Site settings: `MEMORY_TEST_PAUSE_ENABLED=true`,
`MEMORY_TEST_PAUSE_UPSTREAM_KEY` and `MEMORY_TEST_PAUSE_CSRF_KEY`.
Both keys are separate random 32-byte hex, distinct from one another and all
Graph/Visual/Base/device/reset/etc. credentials. No value in code or release
bytes. Browser prepare uses `POST /api/v1/processing/pause/prepare` with JSON
`{}`, exact Origin and same-origin Fetch Metadata. It checks the live Base
revision first and returns `{stage,paused,revision,csrf_token}` with HttpOnly/
Secure/SameSite=Strict host-only cookie. Then PUT the exact scoped
`{stage,paused,expected_revision}` to `/api/v1/processing/pause` with that
nonce cookie plus `x-csrf-token`. CSRF expires in 120s and is authenticated
for the exact owner digest, nonce, Site origin and prepared revision. The
frontend must discard state on stale revision (409), denied request or
unavailability and cannot label a backend operation succeeded before a
matching 200. The rev guards parallel requests and retries. The browser receives
no upstream or CSRF signing secret, and its headers/cookies never leave Sites.

## Base and Gateway integration

After approval, provision only the fixed TEST route
`POST /memory-visual-test-pause` to the current TEST neutral Base listener.
Map **only** `X-Memory-Test-Pause-Auth` to backend Authorization on this exact
route; strip Cookies and external Authorization/platform identity headers.
No wildcard paths, proxying external URLs, other methods or shared authority.
Base requires its root-owned optional `native-pause.json` in the existing
TEST private config dir, containing only `owner_sha256` and `token_file`.
The owner hash domain is UTF-8 `native-queue-pause-test-v1\0` followed by the
actual platform owner ID, derived independently from verified enrollment;
do not expose the underlying ID. The token file must be the exact private
lane state `native-pause-token`, user-owned and marker credentials separate.
It holds the same dedicated upstream key as the protected Site binding. The
Base publisher live projection and release fence are required independently.
This backend never shares the existing MCP, graph or effect authorizations.

There is no committed live native publication consumer of the companion Sites
archive in this repository. Until authorized release publication, exact TEST
Gateway route and owner-auth runtime checks exist, leave both settings dark.
Verify native no-op prepare and negative missing auth/header spoof, wrong
origin, cross-lane, stale/incorrect CSRF and credential reuse before any
isolated synthetic pause/resume. Verify via actual TEST owner Site, not raw
worker headers; preserve existing graph/MCP and all release rollback behavior.
Do not use this route for device authorization or clearing data.

Run synthetic harness from repo root with
`node --test tools/assertion-memory-hosted-mcp/sites-pause/test-native-pause.mjs`.
With owner authorization, point `V25_SITES_ROOT` at the exact saved V25
checkout to exercise its actual enrolled-owner code and legacy entry without
modifying/publishing it. The harness replaces D1 and upstream with synthetic
fixtures; it does not authenticate to the live Site.
