# Execution-owned publication fences — 2026-11-16

Baseline: integration 1c4b96a680b1023cc8f32a609b644bc9f88f7588, isolated source
snapshot /private/tmp/assertion-publication-fences-utf4v298. No private source,
production DB, model provider, runtime deployment or graph policy was used.

TDD checkpoints (local source branch):
- b88580a: new guard API missing (compile/import RED is the intended absent API).
- d0af73c: naming fixture corrected to the actual reference-manifest contract;
  functional RED demonstrated stale view/current/name/failure publication.
- 9712bce: direct route-reuse and answer-applicability writer bypasses both RED.
- The fix commits all production changes together after green verification.

Requirement -> regression test:
- Expired/replaced captured claim -> no view/check/name/current/error/failure writes.
- Revoked during deterministic fake generation or writer failure -> GuardLeaseLost,
  no persisted negative proof and no false semantic knowledge-limit outcome.
- Valid claim -> normal publication; valid owned failure -> ordinary unaccepted proof.
- Nested child engine sharing store -> all parent/child checks compose and reset.
- Foreign connection/read transaction -> cannot validate a write publication.
- Owned old frozen snapshot -> retained history, newer current pointer preserved.
- Copied thread context -> different store's guard cannot cancel this store.
- Direct name/failure writer -> no wrapper bypass.
- Reused route/fixed-answer review -> exact own-transaction guard, not only build path.

Verification after final source changes:
- 14 new fence tests + 16 existing failure/frozen-source/race tests: 30 PASS (2.368s).
- 3 existing default query/applicability acceptance tests: 3 PASS (0.178s).
- Syntax compile of 6 changed modules + test: PASS (7 files, no bytecode writes).
- git diff --check: PASS.
- Python typecheck/lint/build scripts are not configured in this package; not run.

Expanded name-format suite was checked separately against clean baseline and the
changed source: 22 tests in each, same 28 error entries and 3 failure entries.
These pre-existing stale naming fixtures are unchanged; detailed matching test IDs
are in publication_fences_baseline_names.json. They are not reported as green.

Guard state uses a ContextVar stack keyed by exact engine.store identity. The
check executes after BEGIN IMMEDIATE (or inside an already owned write transaction)
and before changes, using that same SQLite connection. No global corpus watermark
is a cancellation fence. Causal-run context, source policy, pipeline keys, artifacts
and semantic content remain unchanged. Queue/worker/pool claims and heartbeat are
owned by the adjacent runtime units, not duplicated here.
