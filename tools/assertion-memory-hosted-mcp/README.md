# Hosted canonical MCP: shared relay candidate, no activation

One Worker and one VM relay implementation serve both lane profiles. The
release-generated policy contains the 17 unmodified canonical input schemas
and separate capability projections. TEST and PROD are not selected by route
parameters, tool arguments or an untrusted host. Each deployment explicitly
pins its profile and release-generated catalogue digest. This directory
adds no GitHub Actions, deploy switches, permissions, environment settings,
live domains, customer records, or credentials.

## Reproduce sources and tests

Use exact canonical release v0.1.1 source
f6e76c92752ece77318286d2c1d63ed5d4f95b84, its MCP SDK 1.30.0 lock, and
jsonschema. From a separate checkout of that source (not the live host or
database) set PYTHONPATH to the release checkout. Run test_vm.py and then
test_worker.mjs with Python and Node 22+. Tests use fake owner credentials
and distinct synthetic service markers, but invoke the exact canonical
FastMCP handlers, once per case, not handwritten tool mocks. No filesystem
data, SQLite database or network is required. The runtime fixture manifest
in this directory is for review/tests: schema output from the exact Python
SDK 1.30.0 source overlay was byte-equal to plain installed 1.29.0 output;
do not substitute the fixture for actual release-SDK metadata verification.

To generate production-grade catalog bytes, run extract_v011_schemas.py in
the pinned environment, verify the expected revision/module/schema digest,
then pass the output and independently trusted authority to
policy.compile_catalog(). Verify canonical input schema digest
72876b3d45b9665911c973e5620ff5aa229f2bcdc9bc0fc7d562167f67e76c84 and
common catalog digest 0315d8a2e1ffe977d9c99bbf39f65aef5523adb929d9d3fe5260dca8e45bb151.
The identical bytes must ship to both runtime bundles; the release/credential
profiles are separate. The VM verifies the selected digest and includes it
in every response. Worker checks it against its own fixed profile.

## The one isolated TEST deployment target

1. Reuse a reviewed exact TEST asset bundle or root-owned source files; install
   and test with the canonical SDK. Keep PROD receiver, user units, gateway,
   credentials, Sites and existing release pipeline unchanged. There is no
   enabled PROD runner in this candidate.
2. Install only run_test.py on the independently reachable TEST loopback
   port 8832 (preflight: free; otherwise stop). This runner invokes the same
   common VM adapter as all future profiles, and rejects a non-TEST profile,
   different actual MCP port, wrong catalogue digest or nonexistent release.
   Bundle the included root-owned test_fixture_provider.py (scoped process and
   immutable release evidence only; no SQLite connections or fixture data).
   Render assertion-memory-hosted-test.service.template by replacing exactly
   @VERIFIED_IMMUTABLE_TEST_PYTHON@ with the root-owned exact TEST release
   interpreter path. Before starting check systemd-analyze verify and run
   test_fixture_provider_test.py. Old units remain untouched. The adapter's
   mount must be kernel read-only; this says nothing about the canonical TEST
   process's own mount or privileges. The canonical backend may legitimately
   write its separate TEST state. The request handler still only relays
   baseline_read (effective profile and backend parameter validation).
3. Through the approved systemd credential facility pass read-profile.json
   (lane=test, canonical_mcp_port=8893, baseline_read only, digest and tag),
   exact catalog.json, pinned test_release_identity and two independent
   credentials (gateway-bearer, test-canonical-bearer). Route only the TEST
   path to loopback proxy and forward only x-memory-mcp-test-read-auth to
   Authorization; publish new routing only after review. Do not grant
   access to the canonical socket outside its existing firewall/auth.
4. At site entry, use makeHostedRelay with exact TEST profile origin and
   route, then withHostedMcp wrapping unchanged legacy handler. Use the
   existing matchesEnrolledOwner D1-only function with exact origin/path,
   POST and same-origin check, no env or email fallback. Enumerate legacy
   tools from exact staged source and pass names, never from client input.
   Preserve native serverInfo and standard _meta. Set both site-read rollout
   flag and TEST capability ONLY after dark tests and explicit approval.
5. Verify denial for unauthenticated, foreign owner, opposite-signed request,
   wrong origin/path/scope and all six effectful methods; then prove two
   authorized fictional reads from same immutable TEST base release. Review
   that the TEST DB is synthetic-only before authorizing even these reads.
   Stop instead of routing to PROD or the legacy base on any failed check.

## What this does not expose

PROD remains untouched. No operations writing, registering, starting a
worker, storing artifacts or generation execute through this release.
Future authorized tiers must opt in separately. Do not confuse conventional
read-sounding memory_query with a no-side-effect capability. Four canonical
names in old TEST (memory_sessions, memory_session_messages, memory_source,
memory_history) must be migrated explicitly before offering canonical search
semantics there (deliberate in-place rollout or tracked temporary aliases;
never duplicate names or silently repoint existing contracts). Exact
canonical full schema and outputs, including typed images, are not authorized
by enabling the bounded pilot capability.
