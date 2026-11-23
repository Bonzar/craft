# Rebuild admission lineage

Local follow-up to `b7de760`; no live DB, VM, main, push or deployment changes.
Derived snapshot construction/write-lock behavior is unchanged.

## Rules

Admission counter/markers are infrastructure state, not transferable business
content. `rebuild_admission` validates them separately at stage fences, live
catchup, readiness validation, publication preparation, candidate validation,
interrupted post-replace recovery and post-restart confirmation. Existing
business-ledger prefix/equality, namespace, path/inode, profile, readiness,
closed-writer barrier, backup and atomic replacement checks remain in place.

Validation requires canonical producer triggers (the same SQL generator used by
schema installation), marker coverage for each current producer identity, valid
integer revisions bounded by the counter, and the correct database instance.
Lineage preserves all baseline identities/tombstones, forbids revision/counter
rollback and forbids newly added identities with pre-baseline revisions. Live
capture can append new identities but cannot alter an existing baseline marker:
raw change-and-restore ABA is rejected even when business bytes look identical.
Stage/runtime marker advancement is permitted only beyond the prior watermark.

At start, the stage-only transaction advances its counter and assigns a distinct
random instance ID. The operation records the exact source/stage instance IDs and
baseline revision, which are rechecked on resume. Imported raw records run through
the stage's own admission triggers; live marker rows are not copied or merged.
Before cutover, the stage counter is at least `live counter + 1`, without changing
the already-recorded stage instance. This floor update is idempotent for recovery
before replacement. Candidates and a target recovered after replacement must
retain the stage lineage and canonical triggers, even if table digests match.

The pre-cutover backup retains the original DB and metadata unchanged. An atomic
restore therefore returns that original lineage; its numbers are not compared as
if they belonged to the newly published instance. Old readers still cannot revive:
each read checks the original inode as well as the instance/revision fence. A plain
read-only fd pins that inode while the reader is open, preventing inode reuse from
making a restored old UUID look like the original file. The fd holds no SQLite
transaction/lock or WAL read mark and is closed before the publication writer.
There is no new restore/deployment command in this change; rollback is exercised
by restoring the real pre-cutover backup with atomic `os.replace` in a temp DB.

Operations created before this lineage contract fail closed with an explicit
request to start a new operation. A source without initialized admission schema
is rejected before cloning; rebuild does not silently migrate the live DB or
retrofit old operation snapshots. Arbitrary privileged edits to the operation
manifest, trigger code or admission ledger are not an authorized producer API.

## Validation boundaries

`test_rebuild_admission` uses temporary SQLite files. It exercises start, legitimate
live capture and stage changes, repeated catchup, the real readiness validator's
explicit pending result, atomic cutover/backup, interrupted replacement before and
after `os.replace`, post-restart confirm, stale readers across cutover and restore,
ABA, missing/backdated/regressed markers, wrong epochs, altered triggers and stale
candidates. An empty DB completes real readiness/cutover/confirm without mocking
the validator. Nonempty cutover tests stub only semantic readiness: they do not
claim a full real-model semantic rebuild. Synthetic stage work follows rebuild's
existing assertion pipeline. Whole-thought marker lineage is tested separately;
unrelated thought-FTS rebuild classification was not broadened.

`test_source_admission` also runs the blocked synthetic verifier, independent
writer and real `wal_checkpoint(TRUNCATE)` test, plus final-write evidence and
privacy checks. The file-identity descriptor does not prevent checkpoint.
No actual provider, embedding call, OS-jail activation or release was performed.

The existing freshness test failure was inspected, not hidden or repaired here:
`ThoughtRawFreshnessTest.test_new_pending_correction_refreshes_cached_answer_then_repeats_without_model`
returns the legacy `draft()` shape (`thoughts` and quotation `support`) to the
current singleton `Construct thought` contract. It fails before the freshness
behavior it intends to check. It was reproduced at unchanged `22bf230` in the
previous follow-up and remains unrelated to admission/rebuild. Production code,
that test and the response contract are unchanged.

Recorded local validation (2026-11-23): expanded relevant suite **195 tests,
1 unchanged baseline freshness error, 2 opt-in skips**; this run is not green.
Final rebuild/admission/WAL/privacy/budget/stdio suite after the descriptor and
identity checks: **57 tests OK, 2 opt-in skips**. `git diff --check` passed.
Evidence outside the repository:
`assertion-memory-rebuild-admission-tests.log` and
`assertion-memory-rebuild-admission-final.log`.
The unchanged-baseline reproduction remains in
`assertion-memory-short-snapshot-baseline.log`.
