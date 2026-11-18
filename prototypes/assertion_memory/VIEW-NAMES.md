# Editorial view names

`editorial-view-name-v1` retains the append-only `memory_view_names` journal.
A narrow mutable `memory_view_name_work` table holds automatic editorial intent,
leases, retries and checkpoints; it contains no accepted knowledge. A name
is presentation metadata, not an assertion, answer review, source attestation or
new semantic version. No semantic pipeline key changes. Existing definitions,
version snapshots, route plans, child descriptors and audits remain immutable.
The separate table is necessary because `definition.title` participates in
navigation proofs and recorded-history authorization hashes, while view checks
participate in semantic winner selection and require a published version.

## Core API

* `engine.prepare_view_name(view_id_or_spec, authorized_scopes=(scope,),
  operation="stable-editorial-revision", purpose=None)` prepares one bounded,
  scope-checked context and calls the writer. It returns the candidate, exact
  context/input hashes, immutable definition hash and optimistic predecessor.
* `engine.review_view_name(candidate)` makes a separate provider call. The
  reviewer sees the exact title and complete context, without writer reasoning.
  It must bind the exact title/hashes/ordered reference coverage. An acquired
  malformed response permits one structural retry of that same candidate;
  `format_retry=False` exposes the typed contract failure to the durable worker.
  Rejections are errors
  of naming only. These APIs also accept `model=` for an explicitly configured
  naming provider.
* `engine.append_view_name(approved, expected_definition_sha=...,
  expected_previous_name_id=...)` rechecks the definition, current authorized
  references and predecessor in a short write transaction. It returns the
  accepted record. The same input/protocol returns the saved winner even when
  competing writers proposed different names. A changed editorial operation
  permits an intentional correction; retrying an operation does not duplicate it.
* `engine.define_named_view(spec, authorized_scopes=..., operation=...,
  purpose=None, automatic=False)` with `automatic=True` performs no model call:
  it atomically saves/reuses the immutable whole-question definition and a durable
  naming intent. Route CAS, new definition, intent and route revision commit in
  one transaction; a competing losing plan creates none of them. Accepted display
  titles are later appended by the worker. `automatic=False` remains an explicit
  deliberate batch API that can synchronously obtain a reviewed creation title.
* `engine.capture_presentation_snapshot()` returns
  `{presentation_cutoff: UTC_server_time, name_watermark: max_name_id}` from one
  read transaction. This is separate from `QuerySnapshot.descriptor()`.
* `engine.resolve_view_name(view_id, presentation_cutoff=None,
  name_watermark=None)` returns `display_title`, `naming_status`,
  `name_record_id`, `name_recorded_at`, `definition_title`, `name_provenance`.
  It selects only a matching immutable-definition hash, at/before both bounds.
  It does not replace `_spec()` or internal `list_views()`.
* `view_names.presentation(engine, id, snapshot=None, recorded_as_of=None,
  published_at=None)` returns `(compact_name_metadata, pinned_snapshot)`.
  Absent IDs/times are omitted from this compact form.

Names require no semantic version. Schema triggers forbid updates/deletes.
Rollback means a new reviewed editorial operation and append, retaining history.

## Provider configuration and pending names

Normal MCP `ServiceConfig.engine`, CLI and desktop worker builders generically
pass their configured provider as `naming_model=model`; there is no backend-class
allowlist. Embedded engines opt in with `MemoryEngine(..., naming_model=model)`;
the provider may be any custom `Model`. Explicit prepare/review calls can also
use the ordinary `engine.model`. Explicit caller titles passed to `define_view`
remain immutable operator input and do not require automatic model calls.

Automatic naming runs in the existing `LinkWorker` and supervisor loop, never
in query/list/find/read code. It uses at most two writer candidates;
transient provider failures instead retry automatically with capped exponential
backoff (maximum 300 seconds), without the source queue's three-error terminal
limit. Writer and reviewer occupy separate worker turns, each allowing at most
one uncached provider call. The existing maintenance round robin includes
`editorial` alongside audit/source-refresh/semantic-derived work, preserving source
priority and bounded fairness. Provider cache writes, candidate checkpoints and
publication require the same still-owned editorial lease. A lease loser cannot
append. Append and completed status share one transaction, and accepted-row
idempotency recovers publication already completed by another authorized path.

The durable writer checkpoint stores hashes/references and input definition,
not source bodies; review rehydrates the exact authorized context and refuses
stale references. Reviewer outages preserve that checkpoint and never regenerate
the writer merely because review is unavailable. Exhausted output-contract
recovery, semantic rejection or oversize reaches an observable `unresolved` state; it is never
reported as completed. A changed explicit operation/protocol can request another
attempt. None of these states manufacture failed semantic checks or change answer
acceptance. The node
and complete question remain present. A new provisional definition uses its
whole question as an internal handle; the resolver returns `display_title=None`
and `naming_status="naming_pending"`, never an accepted generic/truncated name.
`resolve_view_name` exposes live `naming_work` status/reason/deadline separately;
that mutable operational detail is deliberately excluded from pinned serialized
presentation metadata. `memory_status(section="view_names", scope=...)` returns
bounded typed job pages/counts with continuation, independently of source health.
The full SDK envelope including its continuation is checked against the unchanged
response cap. Normal MCP query completion wakes the existing WorkerLauncher after
a durable intent, including when no source message was captured. Startup under
the existing exclusive flock recovers abandoned editorial leases. Provider-free
jobs stay `waiting_model`; restoring the configured provider needs no new source.

Startup reconciliation is a resumable bounded SQL cursor in the existing queue
scheduler. It only enqueues demonstrably machine-produced pending definitions
(exact generated ID/hash or immutable route ownership), never a blind rename of
custom caller titles. Failed planner publication also atomically enqueues its
visible pending question before saving the unchanged semantic failure proof.
Current request IDs are recognized with `question_routes._key`, not a guessed hash
length. Temporal pre-upgrade failures are recognized only from their recorded
planning failure receipts whose snapshot cutoffs match and recompute that key.
The separate proven CLI 20-character identity remains compatible. Reconciliation
only adds missing intents; it never replaces an existing durable writer checkpoint.
New producers already enqueue atomically. No per-query full definition scan or
additional process/thread/manual tool/harness is required.
Unscoped definitions do not grant implicit global editorial authority.

## Independent reviewer output recovery

The operational `editorial-view-name-v1/review-format-v1` policy leaves the naming
protocol, accepted historical rows and semantic pipeline identity unchanged.
`accepted` must be a JSON boolean, `reason` a nonempty string, and all title,
context hash, input fingerprint and ordered reference echoes must match exactly
before either acceptance or semantic refusal is usable. Malformed JSON, missing
fields, incorrect types and mismatched bindings are typed contract failures.
The strict decoder never removes `.replace` suffixes, infers a verdict or fills
missing echoes. A correctly bound `accepted=false` sends its critique to the
writer and consumes the existing semantic allowance.

A contract failure retains the exact durable candidate and reviewer phase. One
additional reviewer response is allowed for that candidate; at most two such
repairs are allowed across the two writer candidates in an operation. Thus a
fully exercised operation has at most six acquired nontransport provider calls.
Each automatic worker turn still permits at most one uncached call. Restarts,
transport backoff, cache replay, lease loss and append rollback do not reset the
repair allowance or regenerate the writer. A second invalid reviewer body ends
in `unresolved`, `phase=reviewer`, `error_kind=review_contract_exhausted`, retaining
the candidate and compact request/response hash audit.

Repair records are unchanged, including title, complete question, context hashes
and references. Only static JSON instructions and a bounded diagnostic change
the prompt/cache key. Diagnostics contain a closed structural category and field
list, response SHA and bounded parser offset; no model prose or facts. Every
initial and maximal diagnostic request is preflighted with `format_prompt`
against the unchanged 60,000-character cap before the first reviewer call.
An insufficient complete family fails before provider acquisition and retains
the candidate without consuming a semantic attempt. Malformed cached responses
remain immutable; repaired responses use their distinct ordinary cache keys.

A bounded startup pass over existing work activates this policy independently
of the older definition cursor. It skips accepted names, live leases and known
semantic refusals. Exact old JSON parse/size failures with a compatible saved
candidate resume review without resetting attempts. Old checkpoints that lost
their candidate cannot be reconstructed from a key-and-response-only cache.
They receive one explicitly new bounded operation with `legacy_format_recovery`
audit preserving the previous request, checkpoint, attempts, stored state phase
and failure. The old parser error alone cannot identify the failed provider phase;
failure phase remains unknown and candidate provenance unavailable. This is a new
editorial operation after operational provenance loss. Repeated startup or original producer intent does
not reset it. No new schema, queue or manual recovery command is involved.

Old unrelated placeholders remain `legacy_unreviewed` historical fallback until
corrected by migration. No node is hidden, deleted, retyped or given a fabricated
common purpose. Roots retain their mechanical catalog role and original query.

Leaf context contains the complete question plus selected authorized current
source rows with actor/scope/clock provenance. Index context contains all
immediate member questions. Legacy request-root IDs resolve membership through
the existing validated immutable automatic-route registry, including historical
registry purposes. Invalid registries are unresolved rather than reinterpreted.
Oversized complete contexts are explicitly unresolved; this version does not
silently omit members or implement multi-packet naming. A production migration
with such residuals is incomplete and must be reported as such.

## Current versus historical presentation

A current graph can show today's name beside an old semantic version. Capture
one presentation snapshot and use the default resolver for every node. Merely
having `version_id` is **not** a reason to cap names at version publication time.
Cache keys must include the name watermark separately from semantic version /
`memory_state.version`; a label append intentionally changes neither of those.

For an explicit `known_then` perspective, cap `presentation_cutoff` at the
requested **recorded** cutoff. Valid/world time never limits editorial knowledge.
For an explicit saved-as-then publication lookup additionally cap at its
`created_at` or check publication time (`published_at=`). Old snapshots without
a name watermark use their historical recording/publication cutoff. New pinned
presentation snapshots also fence later rows sharing the same timestamp. A new
definition created with an accepted canonical title cannot leak that name through
definition fallback before its first name record: the existing input-definition
provenance hash distinguishes accepted creation from a legacy correction.
Today's name alongside historical content belongs in a separate explicitly
dated `current_presentation`, never a rewritten historical descriptor.

`memory_history(section="metadata")` accepts additive `presentation_cutoff` and
`name_watermark`. The first page captures both; every generated continuation pins
both historical and separate current presentation using that same snapshot.
Noninitial unpinned metadata offsets are rejected. `snapshot_id` remains exclusively
the semantic check/route namespace. Query-generated metadata continuations also
carry the presentation pins; labels cannot form a hybrid when renamed mid-read.

Current `memory_query` returns accepted compact `presentation` and
`presentation_snapshot`; pending naming adds only `naming_status`, avoiding
empty-envelope bloat. Historical metadata/overview add historical/current
presentation separately when there is an accepted name. Exact versions,
summary, route/check JSON and semantic proof arrays remain unchanged.
`find_views` retains raw `title` and adds display fields. Navigation catalog
cards add accepted display fields and pin the name watermark in their catalog
identity; a rename invalidates continuation identity rather than mixing pages.
CLI export retains import-compatible raw definitions in `views` and supplies a
separate `view_presentation` map and one `presentation_snapshot`. Desktop recall
adds separate presentation metadata. Artifact snapshot mode explicitly uses a
historical cutoff; current artifact mode uses today's name.

## Graph adapter handoff

A graph source adapter must consume the above resolver and watermark;
renderer-only title substitution or rewriting `definition.title` is incorrect.
For a current export, use one snapshot and emit `display_title`, `naming_status`,
`name_record_id` and `name_recorded_at` per view node, retaining the source ID and
raw definition/audit. Preserve pending nodes/questions visibly and do not call
provisional query text an accepted name. Invalidate frozen runtime/source-cache
entries on the separate naming watermark. Keep historical-as-then handling
explicit. `overview_export.py` exports model prompts, not the graph; it is
intentionally unchanged.

## Migration CLI

`python -m prototypes.assertion_memory.view_name_migration plan --config
/path/service-config.json --scope EXACT_SCOPE --operation editorial-rollout-v1
--manifest /protected/name-plan.json`

The config is the existing `ServiceConfig` JSON shape and normal provider.
Planning opens the source SQLite database read-only and works on a consistent
in-memory copy; even schema initialization happens only in that copy. The
manifest includes every existing definition in the exact scope (or explicit
`--view-id` targets), full questions, definition hashes, purpose, source/route
references, exact candidate/review, predecessor, input fingerprint and counters.
It contains private context and belongs in the normal protected production area.
An existing manifest is never overwritten. Keep generated plans outside the source tree.

`python -m prototypes.assertion_memory.view_name_migration apply --config
/path/service-config.json --scope EXACT_SCOPE --manifest /protected/name-plan.json
--backup /protected/new-before-names.sqlite3`

Apply requires a new backup path, creates a standard integrity-checked consistent
backup before opening the writable engine, and never constructs/calls a model or
embedding provider. Every target gets one outcome in `counter_map`: inserted,
already_applied, unresolved, stale_conflict or out_of_scope_rejected. Their counts
sum to target_count; accepted_count is an additional review statistic. Reapply
the same manifest with a new backup path to resume idempotently after interruption.
Do not call migration complete with unresolved/conflicted targets. Re-export and
verify actual display names and recording times after applying. Review the plan and its scope before applying it to a live database.

## Evidence

Synthetic tests live in `tests/test_view_names.py`, `tests/test_view_name_work.py`,
`tests/test_view_name_pagination.py` and `tests/test_view_name_review_format.py`.
The original 27 cases remain, with machine
creation assertions adapted to eventual accepted display names and immutable full
questions. Coverage includes independent review,
immutable semantic bytes and NAV publication validation after root/child
renaming, actor/current source eligibility, legacy root registry context,
recording-time/watermark bounds, current presentation of old content, historical
metadata, competing winners/CAS, pending retry, outage isolation, complete
bounded membership refusal, and read-only-source plan / backed-up apply.


## Why the operational table is separate

`memory_capture_jobs` requires a real assertion XOR raw event and changes source
processing state; no fake capture is created for editorial work. Existing
`memory_derived_work` is keyed by scope, owns a semantic frontier/frozen snapshot
and has scope-terminal conditions. Reusing it would tie independent view names
to semantic success or replay the scope pipeline for a label. The refresh response
cache has no ownership/deadline and cannot itself schedule pending work. Accepted
name rows are append-only. One narrow per-view work table therefore changes less
than generalizing any of these mechanisms. Its desired request may change while
a lease runs; the claimed request/checkpoint stays protected and a superseded
owner cannot publish. This is operational state, not a new graph entity.
