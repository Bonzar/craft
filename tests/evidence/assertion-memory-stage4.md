# Operational producer/frontier delivery — 2026-11-16

TDD red: new frontier tests failed before Coordinator existed. Green: **242 tests
passed, 21.431s**, including 11 new frontier cases and the existing 231 tests.
After optimizing one-time legacy sequence migration, frontier tests passed again;
no other behavior changed. diff --check passed. All fixtures were synthetic; no
model, live endpoint, private source, global configuration or real token was used.

Protocol matched with the core owner:
- memory_ingest_register(producer_id, session_id, replace_epoch=None)
- memory_ingest_report(stream_id, epoch, report_seq, observed_seq, scan_state,
  gap_count, lifecycle='active')
- memory_capture_message(..., delivery={stream_id, epoch, seq})
- register/report return flat stream state; capture receipt.delivery is the same
  flat owner/frontier state. The server computes acked_through.

The local producer UUID, stream/epoch, monotone report counter and exact pending
report are durable. Raw messages and contiguous per-stream delivery sequences
commit together; these sequences are not producer/source order. Reports publish
observed pending delivery and scan/gap state before upload. Lost report ACK replays
its exact body before a fresh heartbeat; lost capture ACK replays identical source
arguments and envelope. A matching event without matching delivery owner is not a
receipt. Acknowledged-prefix rollback or epoch replacement fails closed, retaining
local raw data and receipts. Message/receipt commit failure rolls back locally and
replays safely after a remote commit. Out-of-order accepted seq can have an older
acked prefix; the adapter does not invent a newer prefix.

The worker reports unknown/catching_up/gapped/current, heartbeats active streams
every 20 seconds, and retries per stream with bounded backoff. EOF/task completion
never closes a session. Explicit close is guarded by current scan, no gap and all
remote receipts; automatic SessionEnd evidence is not available. Changing or
removing binding still stops the old worker before further sends.

Startup hooks still perform no HTTP/model calls. Their local snapshot includes
pending delivery receipts and last known operational state. Stale/unobserved or
unreported producers and unseen offline updates remain unknown. Operational
current is dated, never world-current knowledge. Domain extraction/review remains
separate from raw/transport acknowledgment.

Live native hook→remote receipt→core query acceptance and both actual laptop
profiles are pending the root's integrated deployment review. This unit alone does
not claim end-to-end or global readiness.
