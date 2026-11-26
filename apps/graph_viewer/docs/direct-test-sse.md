# TEST-only direct Graph notifications
The managed gateway returned "stream timeout" on a real owner session, then
the TEST experiment was rolled back. Normal owner Visual stays on the same
managed HTTPS origin. Direct TLS to the existing IP origin was independently
validated by the platform operator and a normal client, without credentials.

This source is a dark TEST candidate, not an enabled route. Set neither new
production permission nor an origin flag until the complete installation is
reviewed and approved. No additional cloud resource or IAM grant is required.

## Deployment contract for a later TEST release
- Enable Graph's explicit TEST direct origin only on the current synthetic
  lane with the exact normal owner Origin configured. No output Graph contents,
  revision, session id, URL parameter or cookies go to the direct origin.
- Same-origin POST /api/v1/events-ticket goes through the existing TEST active
  owner ingress. Preserve exact Origin, owner session Cookie, and CSRF header.
  Do not expose a GET issuer, proxy this path to PROD, or accept an arbitrary
  target. The issue request body is empty.
- Have REVIEWED existing TLS Nginx operator route only allowed OPTIONS/GET for
  /__mvt/api/v1/events-direct to TEST Graph /api/v1/events-direct with fixed
  owner-known Host, buffering/cache off, preserving only Origin, Authorization
  and the two CORS preflight headers. Reject any cookie, no query parameters,
  do not log Authorization or response tokens. The current managed gateway
  does not handle the actual long-lived GET.
- The frontend reader is separate and intentionally not wired into the normal
  view yet. A distinct TEST-only packaged browser proof will configure the
  fixed direct origin and a correspondingly narrow connect-src CSP, without
  exposing an auth or configuration input in PROD. Each reconnect creates a
  new 15-second ticket. A reset invalidates the existing graph snapshot and
  refetches the same selector through the original authenticated HTTPS origin,
  never merges events from past connections. If the direct stream fails, keep
  ordinary Visual usable and report disconnected, not "live".

## Verify before any new PROD scope
Strict authenticated same-origin issuance, anonymous/foreign-origin rejection,
one-use/expiry/session logout, no ticket reuse and Access-Control-Allow-Origin
only for the exact configured TEST site. From the real browser, see hello
and another heartbeat over one still-open TLS response without using WebGL,
then verify normal graph behavior separately. No personal data mutation and
no mobile certificate interstitial overrides. Remove only the tentative TEST
flag/exact paths if the direct proof fails.
