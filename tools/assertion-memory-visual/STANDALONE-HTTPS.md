# Private Visual without Sites

One existing loopback Visual origin process can serve the immutable
frontend, same-origin owner read APIs and versioned UI. It supports exactly
the same runtime code for TEST and PROD, with distinct protected ports and
credentials. It consumes the queue from that lane's local REST Base, and
uses only the lane's separately authenticated local Graph HTTP reader for
legacy graph/search/SSE. No raw Base SQLite file or protocol code is opened
by Visual. This is the read-only slice; old private Sites routes and MCP
are untouched, and Visual gains no pause/reset/enrollment write authority.

## One artifact

`standalone_bundle.py` builds one deterministic ZIP containing exact
`visual.tar`, `runtime.zip`, and source receipt. The original Visual build
publishes it under an exact separate artifact name; the old artifact
formats remain for unchanged workflows. The source-only resolver verifies
the exact original build and provider digest before unpacking.

This change deliberately does not switch the protected Visual Release
entry. After source review, a separate change pins the Visual-only resolver
and connects the same-run packed TEST witness before PROD. A TEST-only
success cannot authorize production: separately provision the lane-specific
PROD signer, receiver and verified health/rollback baseline first. No
current workflow setting or Base release is changed here.

## Ingress prerequisites (not provisioned)

**Prefer dedicated DNS/TLS virtual hosts.** Confirm two DNS names pointing
to the intended VM and obtain valid certificates for those names. Keep the
old IP-certificate vhost, Sites ingress and all other applications intact.
Mount owner authentication per vhost, for example Nginx Basic Auth with
a separately generated high-entropy owner credential kept in an approved
password manager and a private hashed-password file. Do not reuse Sites
identity/credentials. A previously configured compatible owner-auth gateway
could replace Basic Auth only after independently confirming its real owner
binding, security, and trusted forwarding. No OAuth client is assumed.

**Managed HTTPS alternative.** A dedicated managed gateway can provide
its own named TLS origin only if it actually authenticates the owner before
sending any request to the fixed authorized Visual facility, does not expose
credentials to the browser, and preserves long-lived event streams (or clearly
uses tested polling). The existing bearer-protected asset Gateway URL proves
none of those browser auth requirements; do not use its bearer as browser
access or add any public Base route.

For each lane the proxy must:

- Match exact HTTPS host, terminate valid TLS and authorize the owner before
  routing any path (including assets, graph APIs and events).
- On the private local upstream set the exact public Host and replace
  `X-Memory-Visual-Owner-Ingress` with an independently provisioned
  64-lowercase-hex server credential. Discard every inbound copy of the
  marker and browser `Authorization` (after authenticating), cookies and
  untrusted forwarding/identity headers. Preserve browser Origin only for
  deny checks and pass bounded Last-Event-ID for read replay.
- Forward only read routes for this owner service; do not publish
  `/__visual-runtime-health`, legacy POST assets, Base, or a generic relay.
  Configure no buffering for the SSE API. Verify a nonowner cannot reach
  any route or receive an upstream response.
- Configure the existing Visual unit with exact
  `MEMORY_VISUAL_STANDALONE_ORIGIN`,
  `MEMORY_VISUAL_BASE_ORIGIN` and `MEMORY_VISUAL_GRAPH_ORIGIN`, and mount
  its private credentials `standalone-ingress-token`, `standalone-graph-token`,
  and `queue-base-token`. Never add actual credentials to logs or source.
  Keep these distinct from asset/health credentials. If the PROD Base REST
  endpoint is not available, queue reads are explicitly unavailable.
  Preserve existing Sites/asset health settings.

Review the prepared exact vhost and credential scope, then test owner and
nonowner from a real browser on TEST and prove queue, graph and SSE. Revert
only the new vhost/drop-in if needed; old Sites remains untouched. Repeat
only the required protected PROD promotion/health using the same immutable
bytes and its own trust. Local fixtures exercise the browser-shaped path,
not actual DNS/TLS, owner login or published PROD.


## Active frontend and rollback

The standalone HTTPS route serves only the digest selected by its lane's
atomic active pointer. A direct request for inactive retained HTML, JS or CSS
returns 404. A protected GitHub rollback changes the active pointer first.
The legacy Sites asset protocol retains its current digest compatibility
until cutover.
