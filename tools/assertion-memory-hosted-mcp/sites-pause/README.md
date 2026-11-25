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

### Protected native TEST completion gate

The original Base and Visual TEST jobs prove their own VM work; they do not
attest that the native Sites browser path accepts writes. Do not mark native
pause green from those jobs or from saved-source tests. The suggested
continuation is the TEST-only GitHub workflow **Verify native Sites TEST pause
evidence**. It never deploys, reads a Sites secret or approves PROD.
It stays unavailable until the dedicated main-only non-bypassable GitHub
environment has the verified reviewer configured. Submit evidence only after
the original immutable Base and Visual source pair has deployed to TEST; native
owner publication/readback and the actual same-origin browser checks have
succeeded. Native auth is obtained just in time through the supported owner
connector; no refresh/deployment credential is stored in GitHub.

The owner/publisher captures nonsecret evidence: native published source SHA,
SHA-256 of deployment ID and saved version ID, readback project/ACL/plugin
hashes, exact companion archive SHA, actual TEST Base release generation from
the dashboard and Visual manifest digest from the stable visual entry.
Record the exact UTC observation time and browser response statuses and
revision/boolean pairs for initial prepare/read, a verified toggle and readback,
and a verified restoration and readback, plus denials for missing owner and
bad CSRF. Keep tokens, headers, screenshots and session identity out of the
receipt. Run the toggle only when it is safe to suspend and restore TEST and
the workspace has no unrelated jobs that restoring could wake; otherwise do
not mark the feature verified. The authorized human reviewer independently
compares the authenticated native provider and browser observations against the
submitted receipt before approving the GitHub environment job.

Automated gate checks cover both scoped GitHub TEST activation/smoke receipts,
original release/artifact identity and exact companion; the native provider
observation itself is operator-attested, not an authenticated provider API
call by Actions. A failed/dark route, rollback, old evidence or missing approval
must leave native pause unverified. No automatic provider webhook for
owner-only Sites publication is granted by the documented connector; do not
add a generic GitHub webhook secret or native API emulation to work around it.

### Bootstrapping the TEST Base owner digest

Use only the real platform ID that already matches the bound private TEST
D1 owner. Sites account ACL IDs are not interchangeable with its Site-local
authenticated user ID. If the exact owner digest has no independently trusted
provenance, release the immutable TEST companion with the bootstrap mode.
Set `MEMORY_TEST_PAUSE_OWNER_ATTEST_ONLY=true` briefly with live pause disabled.
Same-origin owner-authenticated `POST /api/v1/processing/pause/prepare` with
`{}` returns only `{stage:"test",owner_sha256}`. Every other request retains
its normal origin/body checks; PUT cannot execute, no CSRF token/cookie is
issued, and no Gateway traffic occurs. If both runtime flags are true the
adapter rejects the route. Once the verified digest is recorded through the
protected operator channel, remove the attestation flag and configure Base
using that digest; only after independently verifying the ingress and separate
bindings enable live pause. Do not reuse an ACL UUID or log a raw identity.
This mode does not support PROD.

### Reuse the TEST transport without sharing authorization

If a production transition is holding the shared nginx TLS/rollback config,
do not edit or reload that config to introduce pause. An alternative, only
after release and scoped live header-mapping review, is
`MEMORY_TEST_PAUSE_CARRIER=effects` on the owner-private TEST Site:

- Existing Yandex/VM path `/memory-mcp-test-effects` remains unchanged, as does
  its existing header slot `X-Memory-Mcp-Test-Effects-Auth`. The Site sends the
  **separate pause bearer** in this transport slot, never the generic effects
  bearer. Confirm cloud Gateway forwards that exact slot and JSON body, and
  the current nginx location maps it to one Base Authorization header; do not
  infer this from a historical source or a printed credential. The current
  tracked native effect client uses that slot for normal requests.
- At Base startup, the carrier is installed only for TEST with exact scoped
  active pause and effects credentials, both valid and distinct. The carrier
  classifies using only a constant-time match of the normalized Authorization
  header before reading a body. Every request with that exact pause bearer is
  subject to the existing native pause route's method, one-header, owner,
  body, action, stage, lease and optimistic-revision rules. Denied requests
  cannot fall back to MCP. All other requests retain original effects
  behavior. No operation is added to the MCP catalog.
- The flag is independent of attestation and off by default. No provider,
  network, shared nginx, or PROD changes are part of this option. If existing
  provider headers/routes do not support the exact transport, stop and review
  a separately provisioned TEST ingress. Do not reconfigure PROD to make it
  work.
