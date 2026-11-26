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
