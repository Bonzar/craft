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

## TLS renewal and expiry readiness

For the established direct VM IP, the served public leaf was observed with
system trust and the matching IP SAN. The public leaf issuer organization was verified as **Let's Encrypt**; its
observed `NotAfter` was **2026-12-02T15:19:26Z**. This does not establish
which client or account manages the lineage. No matching system renewal timer or bounded
system/root cron marker was found. This does not exclude an off-host scheduler,
another client or a differently named existing unit. Do not infer renewal from
a valid leaf, renewal configuration alone, staging, or the age of the key.

1. Use the existing authorized VM operator to identify only the public issuer
   and whether the exact live IP certificate lineage is already managed by a
   compatible renewal client. Read no key/account contents. Prefer the
   existing client/scheduler if it is real and scoped to this same lineage.
   Do not start another client, timer or challenge while its ownership and
   challenge reachability are unknown.
2. With an independently established Let's Encrypt IP lineage, the supported
   short-lived validity is 160 hours. Prefer existing compatible ACME client
   with ARI; Let’s Encrypt recommends consulting ARI at least twice daily
   with jitter and renewing at roughly half life if ARI is unavailable.
   Certbot gained IP identifier support in 5.3 and IP webroot support in 5.4.
   An already-running webroot challenge also requires validated inbound HTTP
   port 80 reachability. Use that only if it is the verified supported
   validation path for the existing lineage; do not guess. No new DNS, IP,
   paid certificate or VM is required if this lineage can be renewed in place.
3. A future protected renewal job must run due-only for the exact lineage and
   use existing private storage on the host. Do not force issuance. Its
   separately reviewed deploy hook must be limited to that lineage, validate
   IP SAN/chain and the Nginx config before a graceful reload, and preserve the
   still-valid served leaf on failure. Do not modify other Nginx routes. Test
   the exact protection and rollback independently from app release readiness.
4. A **separate, read-only** monitor runs hourly from an existing approved
   external observer with a strict system-CA TLS handshake to the exact IP on
   443, with certificate hostname checks enabled and exact IP SAN. It sends
   no HTTP, browser session or credentials. Alert on repeated failed trust/SAN
   checks or remaining life at or below 72 hours (critical at or below 24 hours);
   also alert when samples cease. Emit time, expiry, status and a bounded
   public fingerprint only, deduplicate by state/severity, and report recovery
   once. This monitor never issues, replaces or reloads a certificate.
5. Wait for the next **actually due automatic** renewal and verify the external
   monitor sees an independently valid new leaf with later NotAfter and changed
   fingerprint on the same existing public identity. A staging pass or an
   active timer is not this proof. Do not enable PROD direct transport before
   that evidence and the actual authorized mobile SSE acceptance. If cert
   cannot renew, leave the existing polling fallback in place.

Issuer references: [Let's Encrypt short-lived profiles](https://letsencrypt.org/docs/profiles/),
[Certbot IP support and install hooks](https://letsencrypt.org/2026/03/11/shorter-certs-certbot),
[ARI and renewal behavior](https://letsencrypt.org/docs/integration-guide/#when-to-renew).
