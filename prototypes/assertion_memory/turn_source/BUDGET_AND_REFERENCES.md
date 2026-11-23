# Source-turn budget and issued references

This is still an opt-in **offline protocol adapter**. `SourceTurnFactory(...)`
continues to refuse live use without an OS-isolated launcher. These changes do
not enable a provider, change the ordinary worker factory, deploy, or release.

## Accounting and completeness

Trusted host configuration supplies `TokenBudget(window, count, tokenizer, ...)`.
`count` must count actual tokens using the configured provider's tokenizer (or
its token-count API); `tokenizer` identifies that implementation. There is no
implicit characters/4 estimate. The host must use a window/count appropriate to
both extraction and independent verification, conservatively taking the smaller
window if their capacities differ. Provider configuration is not inferred from
an executable name. A missing budget fails explicitly.

Only `_for_protocol_fixture` supplies a default: `offline-fixture:utf8-byte-upper-bound`,
a 262144-unit **synthetic** window counting UTF-8 bytes. This is explicitly not a
real tokenizer or a claim about any provider's context window. The unit tests
also inject named codepoint counters. They test accounting and protocol behavior,
not real tokenization or semantic accuracy.

The same budget checks extraction prompts, tool requests/results, the structured
output reserve, and every actual independent verifier request, including schema.
Instructions, output, and reference allowances are explicit configuration fields.
Window planning additionally counts the existing verifier instructions and its
serialized selected-fragment catalog, plus a 1024-token framing allowance. Actual
requests are checked again; planning never grants authority to exceed the budget.

If the source fits these requirements it is shown whole. Otherwise every original
Python string character is covered by contiguous windows with global `[start,end)`
offsets (the existing DB span convention, not UTF-8 byte offsets). No normalization
or decode/re-encode slicing occurs. A second extraction pass sees hypotheses and
reference handles from *all* first-pass windows, including the tail, and can select
exact earlier passages through the same reference tool. Hypotheses are not evidence.
Every draft receives independent verification; a final per-window verification
sees the full second-pass hypothesis set to catch coverage gaps and qualifications.
All windows must succeed before the worker can publish anything. No chunk is
published on its own. Exact cross-window evidence must fit; otherwise the attempt
fails explicitly instead of silently discarding context. This is a bounded
reconciliation protocol, not a proof that an LLM will understand arbitrary text.

Unresolved extraction, insufficient tokens, changed source bytes, or failed review
leave a diagnostic `pending` queue job with no new thoughts;
they cannot become `completed` with empty output. Output cannot exceed its reserved
tokens. Existing lease checks and atomic publication remain authoritative.

## One issued-reference catalog

`EarlierDialogue.refs` owns immutable `IssuedReference` entries for current RAW,
earlier exact excerpts, and whole thoughts. Navigation no longer owns a second
thought-reference map. `select_references({refs:[...]})` is the single selection
tool; history, search, and `thought_sources` remain read/navigation operations.
The current RAW handle is selected automatically. Proposals must include it and
may use only selected issued handles. The tool accepts no quoted text, IDs,
version, kind, scope, or rights from the model.

Thought receipts fix ID, version, content digest, and the exact shown text. Reads
and verification resolve that version, never current-head content. A later head
can coexist with the selected historical version; the observed head is fenced
at publication. Whole-thought bases have no artificial fragment span. The
existing `PreparedSupport` is a derived verifier projection of the validated
catalog, not another authority or reference service.

Authorized other-dialogue excerpts and thoughts have the same opaque handles,
but produce `external_dialogue_context` records. The verifier receives these as
separate context-only records and explicit rules forbidding their use as current
RAW or writing grounds. They never enter published bases or the verifier's
factual-support fragment selections. Current RAW remains mandatory. Scope grants,
visible-source policy, admission snapshot, lease, chronology/order, and content
hashes are rechecked. The publisher receives pinned hashes. Claim-bound source
publication disables the legacy automatic reread/rebuild on conflict, so it
cannot silently substitute a newer thought version or lose the original scope.

## Audit of other Memory limits

This audit is confined to `prototypes/assertion_memory`.

* Removed: 24000-character primary refusal; 4000-character proposal/eligible
  thought filters; 12-basis/ref filters. Provider budget and per-frame transport protection now govern these paths.
* Removed silent loss: `source_omission.persist` no longer drops declarations
  after eight; `overview_review` preserves every validated revision constraint;
  `navigation_overview` preserves complete reviewer feedback and relies on its
  explicit request rejection. Visual summary shortening in `presentation_titles`
  now sets `summary_complete=false` when it actually shortens the summary.
* Retained history page size (8 messages/12000 characters) and scan batch size
  (32), with exact continuation offsets and opaque cursors. Removed cumulative
  16-call/page, 256000-byte and 128-ref ceilings. Foreign dialogue eligibility
  now searches until an authorized event is found or the complete scan ends.
* Removed navigation 64-candidate/8-call/64000-byte/40-thought-ref ceilings.
  The private source-search 1000-character query ceiling is also removed.
  All authorized candidates reach the injected ranker; every returned ranked
  match is accessible through pages (up to 5 thoughts). `thought_sources` pages
  every exact basis, splitting long spans losslessly. Both operations accept
  an opaque one-use cursor alone for continuation. Cursors are operation-specific
  and cannot change the original query, session, thought version or permission.
  Each returned page is revalidated. Host ranker relevance semantics still decide
  what counts as a match; there is no new claim of semantic search completeness.
* Removed the 64-source-window limit, stdio lifetime 8 MiB/2048-frame limits,
  default 60-second turn timeout and 300-second maximum timeout setting.
  `timeout=None` is the default; an explicitly configured positive finite timeout
  remains a host watchdog for a hung subprocess, not a source-volume policy.
  No total attempt deadline or new slice timeout was added.
* Retained 1 MiB transport frames, broker isolation, auth, schema/identifier
  validation, and public intake body/JSON guards. Source windows stay below
  131072 serialized UTF-8 bytes to allow nested JSON escaping under the frame
  guard. These are per-frame protections, not cumulative source limits or token
  estimates. Oversized requests fail; source chunks preserve every character.
  An 8 MiB **outstanding unconsumed notification buffer** remains: it prevents
  an app-server from flooding memory before answering an RPC. It is released
  as notifications are consumed and does not bound lifetime traffic. Broker
  failures propagate a content-free error even without a timeout.
* Claim-bound source workers no longer construct the legacy bounded context
  preview (history comes from the exact paginated reader). Explicit group runs
  also bypass the legacy 256000-character batch constructor for these extractors,
  preserving the existing group heartbeat. Their expired claims
  and processing failures have no retry-count ceiling. Durable retry backoff is
  retained: `2**min(attempts,8)` seconds (up to 256 seconds), preventing hot loops.
  The explicit unavailable-provider circuit breaker remains; it is a technical
  failure state, not successful processing or a content-size restriction.
  Other workers/public MCP behavior retain their existing policies.
* `thought_engine_inputs` legacy paragraph windows are lossless, and the legacy
  engine performs source-batch review; `source_model_batch` falls back to individual
  processing or explicitly rejects an oversized exact group. Their character
  sizes are batching policies, not purported tokenization.
* Legacy `dialogue`/`dialogue_context` retain explicit input/output errors,
  unavailable-context markers, recovery pages and incomplete-coverage handling.
  `overview`, `navigation_overview`, `overview_projection`, source/relation-gap
  proof and review limits remain bounded tasks with explicit unavailable/error/
  incomplete results, not silently clipped source text. They are not live
  provider-window configuration and have not been broadly rewritten here.
* `session_api` and provenance/RAW/thought service response limits retain explicit
  pagination, `next_offset`, completeness flags, or a request-too-large error.
  Desktop oversized evidence is written intact to an artifact instead of clipped.
  Display title lengths, diagnostic-preview shortening, bounded search ranking,
  repair-attempt ceilings, and hash-prefix lengths remain presentation/resource
  policies. They do not remove stored source content.

## Verification scope

`test_source_budget_references` uses temporary SQLite databases, a patched fake
extractor, named synthetic token counters, and a fake verifier. It checks the old
threshold, whole-message tail, lossless Unicode offsets, multi-window continuation,
insufficient budgets, no partial publication, version changes, separate foreign
context, mandatory RAW, forged refs/type/scope rejection, and source mutation.
`test_source_turn` additionally exercises the real stdio/MCP broker with a
synthetic subprocess, existing history/search/source operations, registry checks,
and lease loss. Real-model and OS-jail integrations remain explicitly opt-in.
Passing these tests demonstrates protocol and publication checks, not semantic
quality on a real model or production readiness.

Recorded local run (2026-11-23, base `82763bec60fa238a3c3950215979258411db5f95`):

* Final focused suite: **52 tests, OK, 2 opt-in integrations skipped**. Modules:
  `test_source_budget_references`, `test_source_turn`, `test_prepared_thought_support`,
  `test_thought_worker`, `test_source_claim_port`, `test_source_lifecycle_boundary`.
* Broad isolated-module run: 149 modules, 102 passing, 46 failing, one 90-second
  timeout (`prototypes.assertion_memory.desktop_test`); 1321 tests reported by
  completed modules. The unchanged #204 control: 148 modules, 101 passing, the
  **same** 46 failing modules and timeout, 1308 reported tests. Both reported
  135 failures and 165 errors. Comparison of every common module's return status
  and every named failing/error test found no differences. This broad run is **not
  green**; its failures are not silently waived. The final focused run also covers
  the subsequent full-MCP-envelope and escaped-frame accounting regressions.
* `git diff --check` passed. No actual provider turn, deployment, VM operation,
  main update, or release was performed.

Local evidence is outside the repository:
`assertion-memory-budget-focused-tests.log`, `assertion-memory-budget-isolated.json`,
`assertion-memory-budget-baseline-isolated.json`, and their `.log` summaries.
`run-memory-offline-suite.py` reproduces the per-module run. The earlier aggregate
runs (`*-tests.log`) were interrupted after the same desktop hang; they are not
used as successful test results. The broad runner isolates modules and bounds
that hang so every other module has a recorded outcome.

## Local review follow-up: inference-only context and exact duplicates

Foreign reference delivery marks a mutable, attempt-local diagnostic scope; the
broker thread shares this marker with the worker. Full authorized text still
reaches the verifier. Once marked, repository-owned emitted diagnostic payloads,
trace request/response bodies, bindings and provider errors are replaced by
content-free records. Whole payload redaction covers arbitrary model paraphrases,
not only literal secret strings. Audit records retain original request/response
SHA-256 identities, policy, character/count metadata and allowlisted verdicts.
Redacted payload lookup keys use a separate hash namespace, so they cannot alias
older unredacted payload rows; the stored descriptor includes the original hash.
Previous queue error text is not recopied into new claim-bound source run inputs.
Protected replies are neither read from nor written to replay caches. Exceptions
cross the extractor boundary as a fixed content-free error, and the worker will
not save a partial-source checkpoint derived from protected failures. This is a
forward-only policy for repository-owned sinks: it does not erase old trace rows
or control an external provider's own logging. Unrestricted local paths retain
existing detailed diagnostics. It is not a general detector of secrets in text.

After all window checks, exact `(text, question)` duplicate drafts are merged
into one publication, preserving the ordered union of all distinct verified
bases. No whitespace normalization, semantic similarity grouping or merging of
incompatible facts is performed. Different wording may still produce duplicate
meaning; reconciling that requires separate design and real-model evaluation.

### Budget and time limitations (not production readiness claims)

The code accepts a host-supplied token-count callback and context window; it does
not discover or validate a real provider tokenizer/window, nor integrate a real
provider token-count endpoint. Only the named synthetic UTF-8 byte upper bound
and codepoint counters have been tested. Static instruction/output/reference
reserves are host assumptions; hidden provider prompts, reasoning consumption,
provider-side compaction and differing extractor/verifier tokenizers have not
been measured. Passing the fake budget tests is not evidence that a live model's
window is enforced correctly.

Each source window uses an independent ephemeral thread. Prior-window material
is conveyed only through bounded draft hypotheses and selectable exact refs;
there is no shared model state, no global semantic deduplication, and no guarantee
that a model will notice a long-distance qualification or request every needed
reference. Independent per-window verification is also not proof of whole-source
semantic completeness. Technical insufficient-budget failures remain pending.

`SourceTurnConfig.timeout` now defaults to `None`; a trusted host can explicitly
supply a subprocess watchdog without a project maximum. There is no finite limit
on source windows, total turns or durable retries. The synchronous verifier has
no overall deadline here. Replay renews leases before model calls; supervised
pool/group processing also heartbeats active claims. A standalone worker still
requires host renewal for a single call longer than its lease; an expired lease
prevents publication and is reclaimable. This change does not grant the model
lease-renewal rights or resurrect expired/revoked claims.
The subsequent local admission revision change removes the model-lifetime read
transaction entirely; see [ADMISSION.md](ADMISSION.md). There is still no global
attempt deadline. Individual SQL reads, ranking and final publication have no
new wall-clock cap, but a blocked model no longer keeps a SQLite read snapshot
open. Host memory (candidate ranking, issued refs, window hypotheses) can grow
with admitted input/history. Resource exhaustion remains an attempt failure,
never proof of completion.
The synthetic window test now verifies the absence of a retained connection,
repeated lease renewal and exact duplicate consolidation; it does not claim
a real-time bound or a bound on WAL file size.

Local follow-up validation (2026-11-23, against published `ba386ab`): 81 tests,
OK, two opt-in integrations skipped; `git diff --check` clean. Includes real
`ThoughtWorker`/`TracedThoughtWorker` and SQLite with fake extraction and verifier:
quoted and paraphrased rejection, invalid response repair, provider exception,
partial-checkpoint exception, trace callback, prior queue error, historical payload
alias isolation, unchanged local whole-thought behavior, exact duplicate windows
and snapshot closure. Relevant replay/routing, repair accounting, source boundary,
prepared-support and worker regression modules were included. No real provider,
wall-clock timeout enforcement, provider-side logging behavior, or semantic
long-distance completeness was tested. No push, merge or deployment in this follow-up.

## Local removal of cumulative quotas (after 9b784e46)

The remaining source-path boundaries are: configured provider context and output
reserves; lossless page/frame sizes; strict schemas (nonempty search query,
canonical session ID up to 256, ISO date up to 40); auth/sandbox/visibility/ordering,
lease ownership and immutable content checks; SQLite lock wait (reader 2 seconds)
and process cleanup grace periods; the outstanding protocol buffer; optional
explicit host watchdog; durable failure backoff and the provider-unavailable
circuit breaker. Immediate malformed-verifier-response repair still has the
existing finite two-call contract-repair loop; failing it leaves durable source
work retryable instead of looping immediately forever. The fixed two extraction
passes are the reconciliation algorithm, not a cutoff on source length or windows.
Other public MCP/intake/maintenance limits are unchanged. This adapter remains
disabled for live use; no real tokenizer/provider or OS-jail integration was activated.

`test_source_unbounded_navigation` adds actual SQLite/worker tests for input over
8.6 million characters and over 64 frame-derived windows with atomic exact-draft
merge; over 16 history pages/256000 bytes/128 refs; search beyond 64 candidates,
8 calls/64000 bytes/40 thoughts; paginated long thought bases with exact offsets;
five failures followed by success with backoff; reclaim after nine expirations;
and a real synthetic pipe stream beyond 2048 frames/8 MiB. Protocol-buffer/frame
protection and content-free broker failure are tested separately. Extraction,
verification and tokenization are fakes; no real-model semantic claims follow.

Local validation for this follow-up (2026-11-23): **124 relevant tests OK,
2 opt-in integration skips**, plus **2 final targeted tests OK** after the final
malformed-broker-frame guard and legacy retry regression. The relevant suite
includes unbounded navigation, privacy, budgets/refs, stdio, prepared support,
source claim/lifecycle, worker/selection/repair, queue priority, source model
batch, trace, replay/routing and processing pool/integration modules.
`git diff --check` passed. Logs outside the repository:
`assertion-memory-no-total-caps-tests.log` and
`assertion-memory-no-total-caps-final-checks.log`.
The earlier broad baseline comparison above was not rerun or presented as green.
No real model, push, merge, deployment, main update or release in this follow-up.
