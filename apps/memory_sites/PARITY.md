# Full port acceptance

Work in progress. The production Sites pilot and upstream Python application
are not replaced by this branch. None of the synthetic tests access private
memory or launch a real model. Do not enable real data, a published processing
endpoint, or automatic cutover while a required item below is incomplete.

| Gate | Evidence / remaining work |
|---|---|
| MCP inventory and input | Compare deployed `tools/list` with the original 17-tool server via `mcp_parity.py`. The current snapshot interface intentionally fails this gate. |
| Original end-to-end | `test_mcp_reference_flow.py` uses the real original server and a separate worker, with a scripted model. It checks immutable/idempotent capture, scoped processing, Construct → Verify, provenance, source pagination and history. |
| Hosted queue durability | `queue.mjs` and `0002_queue.sql` provide tested local primitives for idempotency, claims, checkpoint recovery and lease fencing. No real model adapter or end-to-end semantic publication is wired. Verify remotely before cutover. |
| Semantic publication | `publication.mjs` and `0003_publications.sql` provide a local verification-gated, lease-fenced atomic artifact and completion. Original committed versions, scope, pagination, provenance, freshness fences, and graph mutations are not implemented by this primitive. Keep read-only calls separate from generation. |
| Model, embeddings and review | Preserve original input/output and failure contracts; verify supported HTTPS provider, independent review, timeouts and retries. Never embed local CLI credentials or claim model parity from scripted tests. |
| Graph QA | `qa_binary.mjs` verifies wire bytes against Python formats. Reuse the existing QA frontend and implement the exact immutable revision, node/edge/topic, search, history and live-event API behind it. An encoder isn't a graph projection or a frontend deployment. |
| Unattended and cutover | Verify the actual intended scheduler can write, retries do not duplicate publication, and both exact QA view and full MCP work with the Mac off. Only then review the source selection, backup and import real data privately. |

Local checks from repo root:
```sh
(cd apps/memory_sites && npm test)
python -m unittest apps.memory_sites.tests.test_mcp_reference_flow -v
python -m apps.memory_sites.mcp_parity --emit-original
```

Start comparisons with the current upstream reference. Some older upstream
overview tests still assert the pre-expansion tool inventory; two upstream
answer fixtures in the checked revision also violate the current single-thought
response contract. Record those as baseline failures, not new port regressions
or passes. Do not alter or skip production checks to conceal the mismatch.
