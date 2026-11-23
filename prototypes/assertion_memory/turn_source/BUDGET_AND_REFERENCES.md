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

Unresolved extraction, insufficient tokens, more than 64 windows, incomplete
history/search due to resource ceilings, changed source bytes, or failed review
leave a diagnostic `pending`/eventually `failed` queue job with no new thoughts;
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
  thought filters; 12-basis/ref filters. Provider budget and the existing issued
  catalog/transport resource ceilings now govern these paths.
* Removed silent loss: `source_omission.persist` no longer drops declarations
  after eight; `overview_review` preserves every validated revision constraint;
  `navigation_overview` preserves complete reviewer feedback and relies on its
  explicit request rejection. Visual summary shortening in `presentation_titles`
  now sets `summary_complete=false` when it actually shortens the summary.
* Retained history page size (8 messages/12000 characters), scan size (32),
  16-call/page, 256000-byte, and 128-issued-ref ceilings. These are bounded read
  work/resource guards, not tokenizer estimates. Pages preserve exact offsets and
  cursors. Reaching a ceiling is explicit and blocks source completion. Primary
  text is charged to the token budget, not charged again as history bytes.
* Retained navigation candidate/call/byte/ref ceilings (64/8/64000/40), owner
  grants, frozen membership and chronological cutoffs. A bounded result is not
  complete search and prevents completion of this extraction attempt. Large
  thought text is no longer silently excluded by the old 4000-character filter.
* Retained transport frame/total bytes/frame-count/deadline limits, broker
  isolation, schema/identifier validation, and intake body/JSON resource guards.
  Source windows stay under 131072 serialized UTF-8 bytes to allow for nested
  JSON escaping below the 1 MiB frame guard; these byte limits are distinct from provider tokens.
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

Local evidence is outside the repository under `/workspace/scratch/141d5573074f/`:
`assertion-memory-budget-focused-tests.log`, `assertion-memory-budget-isolated.json`,
`assertion-memory-budget-baseline-isolated.json`, and their `.log` summaries.
`run-memory-offline-suite.py` reproduces the per-module run. The earlier aggregate
runs (`*-tests.log`) were interrupted after the same desktop hang; they are not
used as successful test results. The broad runner isolates modules and bounds
that hang so every other module has a recorded outcome.
