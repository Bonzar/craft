# Full port acceptance

Work in progress. The production Sites pilot and upstream Python application
are not replaced by this branch. None of the synthetic tests access private
memory or launch a real model. Do not enable real data, a published processing
endpoint, or automatic cutover while a required item below is incomplete.

Continue the deterministic/read-only port independently of model-provider setup.
The private fixture release is not an export or mirror of a personal memory
database. Provider secret creation, configuration and live calls are deferred;
keep the existing model trial disabled until approved runtime configuration is
intentionally supplied.

| Gate | Evidence / remaining work |
|---|---|
| MCP inventory and input | Compare deployed `tools/list` with the original 17-tool server via `mcp_parity.py`. The private fixture route exposes two original session tool input schemas; the local RPC tests verify original capture metadata, fixture-only filtering, and no mutation. Plugin provisioning and a connected owner call are separate checks. This is not the full menu. |
| Raw input and replay | `capture_message.mjs` matches canonical normalization, event IDs and receipt digests in selected synthetic cases. `raw_capture.mjs` and `0004_raw_capture.sql` atomically create an immutable plain-text raw event and its processing job in local tests. MCP dispatch, attachment and delivery ledgers, and hosted transaction checks remain. |
| Original end-to-end | `test_mcp_reference_flow.py` uses the real original server and a separate worker, with a scripted model. It checks immutable/idempotent capture, scoped processing, Construct → Verify, provenance, source pagination and history. |
| Hosted queue durability | `queue.mjs` and `0002_queue.sql` provide tested local primitives for idempotency, claims, checkpoint recovery and lease fencing. A bounded single-source provider adapter exists; remote processing and unattended recovery are not proven. |
| Semantic publication | The private synthetic preview includes the canonical one-source commit path (`live_source_commit.mjs`): a verified result writes a version and its basis, replay must not duplicate it, and a rejected result does not write a permanent thought. This passed local bundled-Worker tests; a hosted receipt and its matching version have not yet been observed. The multi-input, graph mutation and end-to-end provenance contracts remain open. Keep read-only calls separate from generation. |
| Model, embeddings and review | `live_source_provider.mjs` connects the original single-source prompts and independent reviewer to the fixed Responses transport, behind trusted authorization. The local tests intercept HTTPS. Hosted key/model access, live inference and scheduling still need proof. Never embed local CLI credentials or claim behavior parity from scripted tests. |
| Graph QA | The private preview serves the checksum-pinned QA graph UI and an immutable, invented snapshot from the original Python implementation. Graph rendering has been observed on iPhone. The saved overview fetch passes the bundled test, not yet the iPhone check. `synthetic_qa_wire.mjs` can read a verified synthetic D1 version locally. The snapshot is not a live projection; search, history, events and generated overviews must still be compared with the original. |
| Keyword search | The QA keyword route uses the matching frozen D1 FTS5 index, including saved overview hits before node hits, original scores and revision/query-bound cursors. Original Python HTTP and exact archive tests cover eight cases. This does not implement semantic embeddings, `memory_query`, or historical revision indexing; an unavailable exact index fails closed. |
| Stored source and history | The local raw `memory_source` reader matches authorized text, metadata and code-point spans. The local canonical Thought `memory_history` reader matches version pinning and same-scope question proofs with its new D1 migration; legacy sources/routes remain outside scope. Neither partial reader is registered on the live MCP plugin. |
| Unattended and cutover | Verify the actual intended scheduler can write, retries do not duplicate publication, and both exact QA view and full MCP work with the Mac off. Only then review the source selection, backup and import real data privately. |

Local checks from repo root:
```sh
(cd apps/memory_sites && npm test)
python -m unittest apps.memory_sites.tests.test_mcp_reference_flow -v
python -m apps.memory_sites.mcp_parity --emit-original
```

For hosted acceptance, verify each stage separately. A successful graph page
GET is not a persisted thought. The prior smoke fixture's queued work and the
lexical-search fixture are not evidence for the permanent fixture. Verify the
expected permanent fixture receipt, exact linked version and basis, and no
second version after replay. Verify the separately served graph reads that
matching version from the same database and does not replace the snapshot or
its saved overview. Neither fixture counts as proof of the live model or MCP.

The first hosted model gate is intentionally one invented, context-free raw
message. Use an approved server-only `OPENAI_API_KEY` and explicit, distinct
`MEMORY_CONSTRUCT_MODEL` and `MEMORY_REVIEW_MODEL` settings. Verify the immutable
raw row and its queue job, actual Construct and Verify success, then the exact
receipt, published version and evidence basis. Verify replay produces no new
versions; a failed provider call or rejected review must not mint a thought.
Do not expose arbitrary text, transfer actual memory, or substitute a canned
reply when runtime configuration is absent. Original native Codex CLI runtime
and model behavior are not guaranteed by matching prompt and response schemas.

Start comparisons with the current upstream reference. Some older upstream
overview tests still assert the pre-expansion tool inventory; two upstream
answer fixtures in the checked revision also violate the current single-thought
response contract. Record those as baseline failures, not new port regressions
or passes. Do not alter or skip production checks to conceal the mismatch.
