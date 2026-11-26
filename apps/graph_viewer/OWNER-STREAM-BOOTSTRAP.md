# Live startup repair on the existing owner surface

The same reviewed artifact runs on TEST and PROD. Its versioned policy
selects ordinary stream bootstrap only after the existing protected TEST
dashboard and browser owner lane validate; the app constructor stays dark
by default and protected PROD stays dark. An explicit CLI flag can be used
for the already validated TEST lane, not for PROD; this release needs no
server config edits. The old TEST signed direct wire contract is unchanged.

Reused no-query `GET /api/v1/events` now carries authenticated metadata-only
hello/reset/changed and heartbeat on the ordinary TEST route. The browser
validates hello/reset before dispatching graph and queue snapshot reads;
mismatched epochs cannot populate the page. Neither graph nor queue response
bodies are pushed into the stream. If an authenticated stream cannot be
verified, show a labeled snapshot rather than Live.

Before a protected TEST launch, record exact published Base and Visual
baseline, owner auth and route configuration. After qualified paired TEST
activation, verify that the existing public route forwards the cookie and
empty query unchanged and flushes frames promptly; local application tests
don't certify buffering at the edge. Measure Connecting, request,
hello/reset, Live badge, both first reads, heartbeat and a later same-stream
graph/queue event. Revert to exact prior Base and Visual releases on failure;
this source introduces no persistent config, data migration or secrets.
