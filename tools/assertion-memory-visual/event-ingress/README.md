# TEST Graph event stream ingress

This review-only renderer prepares the existing TEST ingress. It does not access
the cloud, install anything, create credentials or add an unauthenticated read
endpoint. PROD is intentionally unsupported.

## Inputs and invariants

Run python3 -B render_test.py ORIGINAL_ROUTES ORIGINAL_GATEWAY NEW_PRIVATE_REVIEW_DIR.
Use the current reviewed TEST Graph Nginx include and complete TEST Gateway
OpenAPI spec, supplied privately. Python 3 with PyYAML is required.

Preserve all Nginx bytes except one fixed GET-only location. Mirror the exact
Graph Host and cookie forwarding; send no other browser authority to Graph.
Disable buffering and caching and preserve only the replay header and supported
query parameters. Gateway YAML preserves the original parsed spec apart from
one added path. Do not print or publish a real input or result. Compare with
the latest originals and validate both changes through the authorized deployment
mechanism. Rollback removes only these two additions, never a full stale spec.

The ordinary Visual artifact deployment does not manage root Nginx configuration
or the Gateway OpenAPI spec. Do not apply this through an untracked console or
grant the release job admin authority. Use a reviewed infrastructure deployment.

## Proof before PROD

Require unauthorized denial. In the actual signed-in TEST browser, Visual must
change from connected to updating when a heartbeat arrives. Check over managed
HTTPS that hello plus at least one heartbeat arrive at distinct times on the
same open connection. No user data mutation is needed.

The optional bounded probe prints only status, classification and elapsed times.
It can take an already-authorized synthetic operator fixture cookie by stdin;
never extract browser state, pass a credential as a command argument or mint a
session. Compare against authorized loopback. Code 200 or buffered frames
delivered as a batch do not pass. If the integration buffers, leave PROD alone
and use a reviewed streaming-capable owner-protected HTTPS ingress.

## Smallest reviewed release path

Prefer an operator handoff rather than a permanent cloud editor token. Yandex
Gateway editor can modify and delete its assigned Gateway, so do not bind
the role at folder or cloud level just to change one path. The Update API
writes a complete spec, so serialize against all other edits to that Gateway;
the plan and its rollback must be regenerated when a fresh spec digest drifts.

1. Publish a separate immutable TEST ingress release with only the reviewed
   renderer, manifest and pinned tests. Do not put actual routes dumps,
   cloud IDs, Nginx credentials or cookies in public release assets. Do not
   enlist the existing app release principal or a broad remote command.
2. Run read-only plan with a protected TEST operator. Save both originals
   privately and independently verify the exact spec/digest and existing
   owner-protected Graph path. Build a single transaction plan containing
   only allowed path/method changes and discussed TEST rollback.
3. Have an independently reviewed network-config apply authority consume the
   immutable release and reviewed digests: a root-managed fixed TEST operation
   for exactly one preconfigured Nginx file (regular root-owned file,
   no symlinks, lock, expected-byte comparison, same-directory atomic rename,
   nginx -t before reload and restore exact before bytes on failure); an
   existing operator sets only openapiSpec for the exact TEST Gateway.
   The protected operation must not execute arbitrary archive content as root.
   If automating later, maintain separate scope-local credentials with owner
   approval and an explicit serial concurrency key. A new role/binding or
   root-managed operation requires separate review; none is provisioned here.
4. Order: validate and install the Nginx-only route (denial expected without
   credentials), then publish exact scoped Gateway spec change, then prove
   TEST SSE. If TEST fails, remove just those changes in reverse order; recheck
   other paths. Do not stage PROD until genuine streaming and auth are proven.
   Any later PROD plan needs independent TEST evidence, its own exact routing
   and separate approval.

Yandex references: https://yandex.cloud/en/docs/iam/roles-reference ;
https://yandex.cloud/en/docs/api-gateway/apigateway/api-ref/ApiGateway/update .
