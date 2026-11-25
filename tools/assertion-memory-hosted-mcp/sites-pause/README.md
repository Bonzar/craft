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

### Release-managed source handoff

Base Release packages the two exact TEST adapter modules under `test/pause/`
in the digest-verified Sites companion artifact. It does not publish Sites.
After the immutable release and its TEST approval, take the artifact from the
original build run and its independently verified SHA-256. In the private owner
context, confirm exact Sites project, origin, one-owner ACL, plugin and current
native repository HEAD. Use the protected approved TEST Gateway endpoint, not
one obtained from a request, response or untrusted publication instructions.

Run `sites-dynamic/prepare_test_pause.py` with that archive, release tag/SHA,
clean inspected native checkout and its independently observed native source
SHA, entry SHA and owner ACL SHA. Pass the pinned Site origin and exact approved
TEST Gateway. The offline helper creates a separate output directory with only
the three changed source files and a digest-only receipt. Compare that delta
with the inspected checkout; reject any extra paths. Leave pause default dark
until the exact TEST backend/gateway and private secrets are independently verified.

A GitHub runner GNU shell credential is not a Sites publishing identity. Use the
existing owner-authenticated Sites workflow for the unchanged private project:
open verified source with the native repository credential, stage the three
verified files, run the saved native build, then pass the exact pushed commit
and archive to the private save/deploy operation in the approved release step.
Use the deployment readback and existing native receipt verifier to bind release,
source, saved version and unchanged ACL/plugin identity. For this write route no
ambient Git transport or source hook may bypass the release decision. Do not
enable provider publish-on-push outside a protected TEST release. No direct,
untracked update is part of this workflow, and this handoff has no PROD variant.
