# Owner memory reset (logical, no backup)

The private Base route `/api/v1/queue-database/clear` is a fixed operation,
not a general database interface. It is loopback-only and uses the same
separate lane read/write credentials and path-bound owner HMAC as other
protected owner operations. Standalone Visual must require the exact
lane-specific confirmation phrase, not offer this on public or legacy paths.
GET returns readiness and a revision bound to the authenticated stage, exact
deployment generation, live SQLite device/inode and durable reset generation.
PUT requires this revision and a one-time proof; success returns a new
revision. Never automatically retry an ambiguous PUT.

The reset holds the lane's release fence and exclusive background/interactive
work fences and does one SQLite transaction. If work is active or the schema
has an unclassified table, the reset does not proceed. This includes all live
source and batch records, text, blobs/artifacts, thoughts, graph relationships,
indexes, derived state, telemetry stored in the main memory DB, and queues.
Schema and triggers remain. Persisted pause, processing policy, runtime
activation, authentication/secrets and release files are not changed. Only
opaque salted replay markers prevent normal producer retransmission of erased
message identities; they cannot yield text or source IDs. Session stream
registrations and prior ingest epochs are cleared, so capture clients can
register fresh streams. Retained chats on their original devices are not
modified.

This is not physical media sanitization: existing external backups, unmanaged
owner exports and independent infrastructure logs are outside this operation.
A WAL truncate is attempted after successful logical commit. The external
Graph reader must observe the durable `memory_database_clear_state.revision`
before serving any cached revision and invalidate before enabling the UI
button in either lane. Do not ship this feature with a Graph reader that could
serve an old revision after the reset.
