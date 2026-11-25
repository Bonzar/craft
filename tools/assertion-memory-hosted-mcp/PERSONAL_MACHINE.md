# Personal machine access

The machine API is separate from the Sites MCP relay. A valid machine key has
the same full canonical Base tool catalog as every other enrolled key in that
lane. There are no access levels or per-tool permissions. A key cannot access
the opposite lane or Sites MCP. It never grants shell, administration or
release permissions. Keep it limited to personal trusted installations.

1. On the device, generate 32 random bytes and store their lowercase hexadecimal
   encoding (64 characters) in the device keychain or an owner-only file. Do
   not print or send the token in chat, arguments, logs, or release material.
2. Compute SHA-256 over the **64 ASCII hex characters**, not decoded bytes
   and not a line ending. Register only its 64-character lowercase digest
   under a human ID in root-managed
   `/etc/assertion-release/{test,production}/private-operations.json` as
   `"machine_clients": {"personal-mac": "<digest>"}`. Generate a different
   token for each lane and installation. Missing or empty map is disabled.
3. Qualify the scoped hashed key and exact authorized TLS ingress route for
   that lane; reuse the already release-managed Base HTTP listener. Preserve
   existing Sites route/auth settings. Runtime reads the protected profile at
   startup; apply/reload only as part of the authorized release operation.
   Remove a client ID for revocation; rotate by replacing its digest and
   reloading the scoped service. There is no issuance endpoint.

Send `POST /memory-api-test` or `POST /memory-api-prod` with one
`Authorization: Bearer <token>` and `Content-Type: application/json`.
A catalog request is `{request_id, operation:"catalog", arguments:{}}` (UUID
request ID). To invoke, send the same envelope plus
`expected:{release,generation,catalog_sha256}` copied from the catalog. The
machine capability is always `machine`. On stale identity, fetch a new
catalog for subsequent requests; never automatically repeat an ambiguous
effect. Missing/wrong credentials fail before dispatch. The endpoint holds the
existing release lock shared after valid authentication; release activation
holds it exclusively.


## Automatic source processing (disabled by default)

Use the ordinary Base release to deliver this feature. Keep the host's
automatic legacy selection as `processing_session_ids: []`. Only after the
release and an isolated canary are verified, an authorized PROD operator may
opt in with `service.auto_process_fresh_mac: true` in the release-managed host
service configuration. The neutral PROD binding requires an existing protected
`machine_clients.personal-mac` credential fingerprint; the server resolves it
from bearer authentication, never a request's producer or role labels. Do not
share this credential with other clients. Local fresh-original-session and
human-input checks remain necessary; server first registration does not by
itself prove how a client collected any message.

Only a new, previously unseen local session registered through that verified
client can acquire this automatic scope. The worker will select exact newly
committed raw events with receipts from the admitted stream; prior events and
replays cannot be upgraded. Other clients keep their existing access and may
still select an exact one-time processing session through the normal operation.
The common worker lock and durable scheduling recover admitted pending source
calls across restarts. Automatic selection does not run coalesced maintenance
causes, old work or historical backfills. Verified source thoughts are indexed
normally; maintenance retains its existing policy.

For rollback, disable the flag via the normal approved configuration path.
This does not delete raw receipts, thoughts, or previously committed admissions.
A running worker must be drained by the standard release lifecycle before
replacing its configuration; never interrupt the database writer in-place.

## Atomically processing a complete source batch

The Base machine catalog advertises `memory_capture_batch` only to the
configured fresh Mac credential. Other clients and legacy MCP keep
`memory_capture_message` unchanged. The batch is an ordered list of ordinary
raw messages from one authenticated stream; Base does not interpret the
occasion or wait for another member. The stable batch ID and exact raw
members commit in one transaction, with individually identifiable receipts.

Roll out Base first. On the Mac, opt-in at a fresh collector cutover with
`capture.capture_batch: true` in the installed machine binding. Follow the
existing immutable module install and fresh cutover procedure above; do not
switch an active old/outbox cohort in place or replay pending old entries.
Native capture sends a complete batch only after its local completion proof.
Dropped acknowledgements retry the same batch and cannot duplicate raw
events or enqueue a second draft. Keep the ordinary PROD automatic
selection empty and the existing owner-authorized fresh-Mac mode enabled.

The normal shared worker runs one group construction for the exact batch
and may verify evidence separately per source. It does not silently fall
back to individual construction. If dispatch is interrupted or verification
fails, raw records stay present and the batch requires operator review;
there is no automatic second construction. A different injected extractor
without a group constructor also fails closed. This release does not
enable batch capture on the Mac on its own.
