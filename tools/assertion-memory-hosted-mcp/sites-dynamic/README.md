# Release-owned, dynamic MCP catalog

The Site is the MCP boundary. The VM route accepts a small generic JSON HTTP
envelope, not JSON-RPC. The same Site bridge code runs in both environments;
trusted deployment configuration pins lane, exact Site origin, exact read /
effect URL, and whether the profile permits effects. No tool names or tool
schemas are declared by this overlay.

## Authenticated wire

* POST /memory-mcp-{lane}-tools and optionally /memory-mcp-{lane}-effects on the
  same pinned HTTPS Gateway origin. Separate server-only bearer/header per
  route. Client Authorization, cookies, identity and arbitrary paths never
  travel to the VM.
* Catalogue request: {request_id, operation:"catalog", arguments:{}}.
  Response: {request_id,environment,release,generation,catalog_sha256,
  capability,tools}. Capability is baseline_read or intake_process, derived
  at the VM from route and server credential, not from the client. Each
  descriptor is {name,description,inputSchema,annotations}.
* Invocation: {request_id,operation,arguments,expected:
  {release,generation,catalog_sha256}}. The VM independently authorizes the
  operation and validates input and exact active release identity before
  starting it. Echoed {request_id,environment,release,generation,
  catalog_sha256,status:'ok',result} must match. Base returns plain domain
  result objects. Only Sites renders MCP content and structuredContent.
  The Worker never retries a mutation.
* With effects enabled, list both scopes and require identical release and
  generation; retain each scope's own separate catalog hash, reject any name
  collision. An outage/drift returns unavailable, never a stale catalogue,
  local tool definitions or legacy calls.
* Runtime identity and credential setup reuse existing verified private
  owner binding; verifyOwner must check authenticated Site-scoped identity
  with no service caller promotion. Owner-only scope applies before any
  fetch. Shared code expects protected variables HOSTED_MEMORY_{LANE}_READ_TOKEN,
  and if applicable EFFECTS_TOKEN plus EFFECTS_ENABLED. Opposite lane
  variables are rejected.
* Keep HOSTED_MEMORY_{LANE}_DYNAMIC_CATALOG_ENABLED unset or false on first
  private publication, preserving existing Site audience, MCP plugin ID,
  visuals, other paths and native discovery metadata. When enabled, the
  authoritative catalog replaces legacy tool definitions/calls, intentionally.
  Do not mix fixtures into current canonical Base tools.
* OpenAI states that adding tools to a Sites MCP requires republishing for
  the connected plugin inventory. Dynamic server list changes immediately
  when its release changes; that does not prove already-installed native
  clients re-fetch it. Confirm authenticated native tools/list then a
  read-only real TEST lookup before graduating. Keep user data and secrets
  out of source. No live changes are performed by this worktree.

Run `node --test test_worker.mjs test_base_contract.mjs` from the candidate
repo. The first test uses a local HTTP double. The second reaches the real
Base ASGI route and typed operation registry by bounded subprocess, with no
sockets, database or live model. It adds a required enum argument at Base
only; checks old and new complete descriptors and exact input validation in
both lanes; and verifies unchanged Site Worker bytes. Deployment still needs a real, released VM
endpoint, owner-only private Site rollout and native inventory verification.

## Release and native receipt

The existing Base tag `assertion-memory-vX.Y.Z` also packages the Sites
bridge as a companion artifact in the original Base build. There is no
separate Sites release tag or workflow. The build uses the same trusted,
immutable commit reachable from main, without Sites write credentials. It contains exactly one shared Worker, two hashed config
envelopes and a file manifest. Hash inputs are the exact existing private Site
project ID, its origin, and canonical approved access identity; the public
artifact must not contain the unhashed IDs, owner values, private source,
or credentials. Define access identity as a versioned canonical JSON document
containing the existing access mode and sorted account/group/external allowlists,
and hash those bytes consistently before and after native publish. Provider
revision and timestamps are not access identities. No access policy changes.

A release controller treats the upload-artifact ID and archive SHA256 from the
original workflow run as immutable. It first verifies the selected release and
complete artifact against the TEST envelope; the TEST receipt does not approve
a different digest for PROD. Stage existing private Site source as a separate
step, preserve existing Project and plugin IDs, apply only release Worker and
its config wiring, then push exact source SHA and build the native archive
from it. Its project-specific hosting envelope must match that existing Site.
Operate only in the owner context and keep admission default-dark. Use native
private publication only behind the authorized GitHub release gate. A direct
untracked publish is not a release.

The authorized controller independently reads native saved version/deployment
results and before/after owner ACL/plugin identity, then validates a private
receipt with `verify_native_receipt.py`. The validator checks binding, not
provider authenticity; obtain the IDs only from authenticated native readbacks.
Its loggable summary contains hashed Site IDs only. The immutable release
asset is not rewritten to include later receipts. If the live catalogue changes
types or names, separately verify native plugin inventory refresh; server
`tools/list` freshness is not an installed-client refresh guarantee.


## Switching the private Sites to the full Base catalog

The matching Base release widens the catalog automatically for the owner-only
Sites lanes already provisioned with both route credentials. A read-only lane
without an authenticated effects route stays narrow. No persistent host
configuration changes are required. The Sites identity/access list and
existing machine credentials do not change.
The VM serves the same canonical descriptors as the machine catalog, partitioned
by read-only annotation across the two existing endpoints. Sites does not name
or describe any method. The two endpoints still authorize their own credentials
and invocations using their respective catalog digests. Absent the flag, the
previous five-tool rollout remains unchanged.

Roll out TEST first. After deploying Base, compare authenticated native
`tools/list` to the Base registry, check a source and its snapshot, and invoke
`memory_query` using only fictional data. Verify both a saved answer and a
request requiring model work; don't infer that generation works from discovery.
The Sites release profile allows up to 120 seconds for an effect request.
Confirm the effective Gateway, reverse-proxy and client deadline too. A timed
out query must not be retried automatically. The current Sites JSON envelope
is intentionally bounded and does not promise the machine endpoint's large
binary transfer allowance; use the machine API for large artifacts. Check that
the native client's published inventory refreshes, through the existing Sites
release process if needed. Promote the exact Base/Sites artifacts only after
those TEST checks. Roll back the Base release to restore the prior catalog.
