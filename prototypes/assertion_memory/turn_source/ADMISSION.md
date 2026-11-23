# Short source reads and transactional admission

Local follow-up to `22bf230`. No provider, live release, deployment or main change.

## Admission without a long SQLite snapshot

A reader captures `(revision, database instance)` and existing order cutoffs in
one short read transaction. Every later navigation, evidence or verification read
opens its own read transaction and closes it before returning to a model/ranker.
The reader does not copy graph membership, graph payloads or all source texts.
Its only retained content is the existing current-source/issued-reference catalog.
A plain read-only, non-inheritable file descriptor pins the original inode against
reuse during atomic replacement/restore; it is not a SQLite connection, transaction
or WAL read mark, and closes before entering publication.
`freeze_membership` remains accepted for constructor compatibility; membership is
now always revision-fenced. `_anchor` is a compatibility marker, always `None`.

Two DB tables store a monotonic integer counter/instance identity and latest
per-object revisions (kind, identity, version, revision). Deleted identities retain
compact tombstones. Counter increments, marker updates and the producer mutation
commit or roll back together. A revision newer than the attempt watermark is
not admitted even if the content was restored byte-for-byte, an ID was reused,
a ROWID was reused/lowered, or a new source was backdated. The counter rejects
integer overflow rather than wrapping. This is not a wall-clock timestamp.

Schema installation is part of the existing CaptureQueue/ThoughtStore schema
transactions. Existing IDs are seeded once in SQL, atomically with trigger
installation; text/payload columns are not copied. Normal constructors do not
rescan all objects. Triggers cover writes from any SQLite connection, including
SQL that bypasses Python producer methods:

| Table | Tracked operations |
| --- | --- |
| `memory_raw_events` | Insert, any update, delete; old and new IDs on update |
| `memory_event_positions` | Insert/update/delete, including remove-and-restore |
| `memory_capture_jobs` | Insert/delete, event reassignment, transitions into/out of `waiting_origin` |
| `memory_thought_versions` | Insert/update/delete of exact version, including REPLACE |
| `memory_thoughts` | Insert/delete and identity changes; ordinary head advancement preserves historical versions |

Capture, ingest, direct SQL imports, retirement/purge and payload compaction all
hit these table triggers. Ordinary claim/heartbeat/status progress outside the
origin-admission transition does not revoke source membership. Even a lossless
payload re-encoding conservatively invalidates an in-flight version; a fresh
attempt may read it. Missing markers fail closed. The DB schema and triggers are
trusted infrastructure: arbitrary hostile DROP TRIGGER/ledger tampering is not a
supported producer path or an authorization surface.

## Integrity and final publication

Each read still verifies lease, current source and original ordering, full raw
membership, visibility/scope and the host owner grant. Whole thoughts resolve the
exact shown version and hash, including authorization of all original raw bases.
A moving head cannot substitute newer contents. Primary changes anywhere in the
full message invalidate the attempt, including changes outside the selected span.

Before publication, filesystem identity/grant preflight runs outside the writer.
Inside the existing atomic publication transaction a composed guard checks lease
ownership and the reader's DB instance, primary and **all selected refs**, including
foreign context that is deliberately absent from writing bases. Authorization,
revision/content and selected-version checks therefore cover the gap after the
last verifier response. Existing pinned-basis/observed-head checks also run in
ThoughtStore. No reference is reinterpreted as latest-head content. Grant metadata
is checked in memory against the preflight identity and writer instance; no
path/stat/open operations, model calls or embeddings are introduced under the
write lock. The publication guard may perform SQL and content hashing.

The source catalog is attempt-local, not a new durable reference service. Privacy
redaction and exact-draft consolidation from earlier commits remain in place.

## Remaining locks and integration limitation

No deadline, slice timeout, new volume limit or broader locking redesign was added.
A delayed verifier no longer pins source WAL pages. This is not a bound on every
SQL query, WAL size, source duration, or another connection's locks. Navigation can
still scan all candidates inside a read transaction; ranking occurs after it closes.
Final publication holds its existing writer while validating/hashing selected data.
Initial schema migration holds the existing writer for the compact ID projection.

`DerivedWorkQueue.claim` (`derived_work.py`) still calls `capture` or
`_frozen_snapshot` inside its write transaction. That unrelated whole-generation
snapshot assembly and existing queue/publication/maintenance locks are unchanged.
This change does not claim to make all Memory lock lifetimes short.

The rebuild integration blocker found at `b7de760` is addressed by the subsequent
local follow-up documented in [REBUILD_ADMISSION.md](REBUILD_ADMISSION.md). Admission
metadata is checked by explicit lineage/monotonicity guards, not treated as ordinary
immutable business rows or added to an unchecked operational-table exception.

## Synthetic validation

`test_source_admission` blocks a synthetic verifier until an independent SQLite
connection commits a write and runs `PRAGMA wal_checkpoint(TRUNCATE)`; the observed
result is `(0, 0, 0)` while the verifier is still blocked. Tests also exercise full
source ABA, an unchanged quote with a modified remote tail, identical repeated
IDs/ROWIDs, position/origin revoke-and-restore, stale versions, head advancement,
foreign-source/grant revocation during the final-publication gap, trigger rollback,
and migration of existing IDs without copying bodies. A publication test forbids
Path stat/resolve, file open and model invocation in the publication operation.
Existing cross-dialogue late-backdate and privacy tests are included.

Recorded local results (2026-11-23): expanded relevant suite **179 tests**, with
**one pre-existing error and two opt-in skips**. The error is
`ThoughtRawFreshnessTest.test_new_pending_correction_refreshes_cached_answer_then_repeats_without_model`:
its synthetic Construct thought reply violates the model response contract. The
same named test/error was reproduced in an isolated unchanged `22bf230` checkout.
This expanded run is not reported as green. Final admission/privacy/payload/purge
suite: **22 tests OK**. `git diff --check` clean. A separate synthetic DB check
confirmed that the pre-follow-up `_live_guard` rejected a normal new capture
because of changed admission metadata. Those historical results motivated the
rebuild integration follow-up; they do not describe its corrected behavior.

Evidence outside the repository:
`assertion-memory-short-snapshot-tests.log`,
`assertion-memory-short-snapshot-baseline.log`,
`assertion-memory-short-snapshot-final.log`, and
`assertion-memory-short-snapshot-rebuild-check.log`.
