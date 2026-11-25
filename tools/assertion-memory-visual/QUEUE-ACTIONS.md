# TEST queue management domain candidate

No UI or remote writer is enabled here. Bind this separately to an exact
owner-only TEST operation, not to the existing Graph read proxy or a broad
manager API. Authenticated actor, stage, projection and active release lease
must come from the trusted backend; they are not browser arguments. A
successful command may wake the existing scheduler via its normal launcher
after commit. Never report that an item is running until the worker holds a
real lease and its snapshot confirms it.

Each selected row is `{queue:"source"|"maintenance", id:"exact queue ID"}`.
Selections are explicit, unique and at most 25. No selection of interactive
queries in this candidate.

- **Take one**: `take(item)` prioritizes exactly one current ready unleased
  row in its own lane. The new request replaces a previous unclaimed priority
  in that lane. Actual claim still uses ordinary source/session/causal scope,
  global pause, available time, attempt and lease rules. Other eligible work
  can proceed in parallel. Snapshot order matches actual claims. A changed
  row revision ceases to have priority.
- **Enqueue selection**: `batch_enqueue(items)` validates the entire selection
  before updating it, then requeues existing failed source/maintenance and
  cancelled source jobs under the normal worker path. Existing pending items
  are idempotent and their backoff stays intact. Running, unknown, completed,
  unverified-origin and out-of-scope rows abort the batch. Enqueue while
  paused is durable but does not start processing.
- **Cancel selected jobs**: `prepare_cancel(items)` issues a 90-second
  opaque confirmation for the exact TEST owner and current rows. Then
  `cancel(items, confirmation_id=..., confirmation="ОТМЕНИТЬ TEST")`.
  Only unstarted raw-source jobs without a lease, prior extraction receipt
  or checkpoint are cancellable. A claim, changed selection, mismatch,
  expiry or replay refuses the entire cancellation. The row remains terminal
  `cancelled`, raw capture is not deleted, and dry audit shows who performed
  the operation on what queue item and when. Active or mandatory maintenance
  jobs are not cancellable by this candidate: silently dropping causal
  recomputation can leave derived data stale. No deletion of query receipts.

## Deleting graph nodes is a separate lifecycle

A graph node can be a raw source, legacy assertion or canonical thought.
It is not interchangeable with a queue row. Canonical `ThoughtStore.retire`
removes the active head and search projection while preserving pinned versions
and a retirement receipt. Existing callers use accepted verified semantic
replacement or source-policy exclusion, exact expected version and publication
guards. That method is not currently an audited owner-removal API. Do not
invent a model 'accepted' receipt, detach raw sources, hard-delete via SQL, or
skip dependent version and relationship reconciliation. A reviewed owner
retirement application contract should explicitly identify node subtype and
head version, retain history/audit, handle linked dependents, and say whether
source-backed work can regenerate a retired thought. None is exposed here.
