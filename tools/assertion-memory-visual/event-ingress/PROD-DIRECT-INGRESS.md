# PROD direct owner-stream ingress (inert plan only)

The TEST rehearsal proves Safari can consume an early hello and later heartbeat
over a trusted direct HTTPS transport. The Gateway is not a streaming transport.
This renderer adds only a same-origin owner ticket POST to the PROD Gateway.
The separate VM TLS server carries a one-use authorized direct stream. No
Gateway, VM, DNS, certificate, secret or live product action is in this change.

## Before importing into a protected PROD network release

- Read the exact live PROD owner Graph anchor and Nginx owner subrequest from
  the protected provider. Never print their configuration. Check the provider
  identifies PROD, compare the complete preimage with the review copy, and
  ensure the already allocated IP belongs to the same canonical VM TLS listener
  and PROD Graph :8901, not the TEST Graph :8891 or another release. The
  canonical owner public API origin and direct IP are intentionally distinct
  audiences.
- Verify strict public chain, exact IP SAN and an automatic renewal witness.
  Never disable TLS validation or disclose either browser cookies or tickets.
  The already verified TEST TLS IP can be reused **only** if independently
  confirmed by those PROD anchors and renewal; no purchase/new machine needed
  if so. The previously observed TEST cert expiry is December 2 UTC; observed
  client validity is not renewal proof.
- Ship a lane-aware version of the same app ticket/reader code. The existing
  #325 `Binding.test` and `TEST_DIRECT_PATH` intentionally refuse PROD and
  remain dark until the app owner changes them with separate tests. Pin PROD
  runtime UID, canonical active generation/DB, exact trusted public Origin,
  direct host/path and owner session store separately. Preserve exact one-use
  authorization, revocation, 15-second ticket, connection bounds, metadata-only
  frames, no cross-stage access and no log capture of headers or response JSON.
  The browser must compare returned origin and path against release-provided
  scoped config, never allow server/request-selected arbitrary destinations.
- Only direct Nginx GET/OPTIONS may forward Authorization and never owner
  Cookie; only the ticket POST can receive cookie+CSRF and never direct
  Authorization. Add the fixed direct IP to the reviewed Visual CSP only.
  Nginx must keep buffering, compression, caching and sensitive access logs off
  for the stream; verify there is no inherited debug/header logging. No
  same-origin `GET /api/v1/events` change and no public proof HTML by default.
- Existing #331 artifact/release proves a safe renderer pattern but intentionally
  rejects PROD. Create separate immutable scoped infrastructure artifact; use
  known operator controls, locked exact preimages, private staging
  `nginx -t`, additive full-spec compare and exact reverse. Do not give an app
  release general cloud admin. Avoid conflicts with concurrent owner ingress
  rollout. The graph runtime can remain disabled until both boundaries match.
- Demonstrate anonymous/method/CORS/stage denial and owner use on the actual
  PROD iPhone route after protected authorization: first hello followed by
  later heartbeat on the same connection (minimum 500ms separation, 15s
  no-progress limit). Report only timestamps and status. Cross-origin or
  buffered 200 alone does not pass. Roll back exact additive entries if not
  proven; leave existing REST/polling otherwise intact.

Checked offline against synthetic exact-shape fixtures. Do not infer observed
live rule values from those fixtures.
