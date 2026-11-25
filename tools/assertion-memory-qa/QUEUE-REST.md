# Queue state over the current Base HTTP listener

Base owns database reads and scheduler semantics. Visual owns its private user
route, static UI and presentation; it deploys independently of Base. Neither a
second Base process nor a second Graph process is required for this queue read.
Base does not expose an MCP tool for this contract. The old graph page,
authorization, catalogue, effects and machine routes are untouched.

First rollout is off by default and available **only** on the standard existing
TEST listener at `http://127.0.0.1:8893` (its configured bound port is checked).
Visual's server-side base URL should be configurable, pinned to this loopback
origin for the first rollout. Do not configure public ingress, add a Gateway
route, enter the Base key in user-executed scripts, or forward browser identity
headers/credentials. Host separation in a later rollout requires intentional
private transport/TLS and exact peer policy review rather than relaxing this gate.

A reviewed host setup may add root-owned
`/etc/assertion-release/test/queue-read-rest-v1.json`, with strictly
`{"version":1,"lane":"test","key_sha256":"<sha256 of ASCII key>"}`.
Visual receives a new dedicated random 32-byte (64 lowercase hex characters)
TEST queue-reader secret through its private service configuration; Base
only stores its fingerprint. Do not copy/reuse existing Sites, MCP, machine,
Graph or pause credentials. No defaults or sample keys are accepted. Only
normal approved service activation may read the opt-in. Invalid opt-in
does not change other endpoints and logs only generic activation diagnostics.

- GET `/api/v1/queue-status?limit=50&offset=0`, both parameters optional.
  Limit 1–50, offset 0–1,000,000. Canonical decimal only, no body, no aliases,
  no duplicate or extra fields. One `Authorization: Bearer <internal-key>`.
- The actual loopback peer and server socket and exact Host are required. Any
  forwarding or lane-claim headers are forbidden. Never treat a forwarded
  browser header as identity. 404 disabled, 401 wrong/missing key, 403 bad
  caller/socket/forwarding, 405 wrong method, 400 malformed paging/body, 503
  stale release/data, unavailable database or saturated bounded reads.
- Response 200 private/no-store JSON: `stage:test`, `release_version`,
  `release_generation`, `global_order:false`, `snapshot_at`, `queues`
  exactly source, maintenance and interactive in that order, and
  `processing_control` with `can_change:false`. Uses current immutable Base
  identity and real queue status/phase/order from short read-only SQLite snapshot.
  No raw job data, model call, shared-token credentials or database writes.
- A later pause REST change must use a separate method/path, credential,
  authenticated owner+CSRF/revision, Base scheduler permission and no MCP
  effects tunneling. This GET provides no guard-bypassing pause capability.

Deployment acceptance remains pending until actual private Visual bridge
receives the exact TEST release generation at the normal owner entry point,
proves a denied foreign owner and invalid internal credential, and runs while
the old Sites-facing Graph is not supplying this queue response. Do not infer
that from a local ASGI test or use a route to expose localhost publicly.
