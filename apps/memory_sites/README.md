# Assertion Memory on Sites (migration branch)

This is the first, read-first slice of a **no-Mac, no-VPS** port. Unlike
the existing graph viewer's Sites proxy, the Worker uses hosted D1 and
does not call an upstream machine. It is not the assertion-memory engine,
nor a drop-in replacement for the current binary graph-viewer API.

## Included

- A consent-scoped, local export into `graph-viewer-source/v1`, without
  raw episodes, source quotes, or evidence offsets.
- A bounded and validated import into Cloudflare D1. The graph and saved
  overview versions are read from D1, not embedded in the frontend.
- Read-only MCP tools:
  `memory_graph_snapshot`, `memory_graph_node`, and `memory_saved_overviews`.
  The existing QA frontend is the required visual target; this branch does
  not deploy a substitute or edit the QA Site.

## Owner-only setup and trial

Create a separate owner-private Site; do not reuse the existing synthetic pilot.
Configure its D1 binding as `DB`, with the graph schema in `drizzle/0001_graph.sql`,
and enable the `mcp` capability. Provide the Site-scoped owner identity as
the Worker secret `MEMORY_OWNER_ID`. It is the value of Sites' authenticated user
ID header for the intended owner, not a generic ChatGPT account or workspace ID.
The Worker is designed to run only behind the Sites authenticated gateway.
Do not deploy it behind an origin that accepts user-supplied identity headers.
An unauthenticated MCP discovery can expose only the static tool schema; data
and import require the configured owner. Keep sharing owner-only.

For a local smoke build on Node 22.13+:

1. Run `npm ci` and `npm test`.
2. Use the Sites registration result to populate the ignored
   `.openai/hosting.json`: `project_id`, `d1: "DB"`, and `capabilities: ["mcp"]`.
3. Run `npm run build`. The Worker entry is `dist/server/index.js`.
   Apply the SQL as a schema migration through the managed Sites deployment
   workflow. Verify the migration artifact layout in the destination before
   publication; a plain build does not provision or migrate hosted D1.
4. First use only a small approved synthetic snapshot. Check unauthorized
   requests, the D1 row counts, matching MCP results in ChatGPT and in a
   scheduled Dot run, and the graph with the Mac disconnected.

When real data is authorized for import, start with a reviewed scope, keep the
snapshot local and out of Git, and inspect what it contains before uploading
it in the private Site. Export from the repository root:

```sh
python -m apps.memory_sites.export_approved \
  --source /private/memory.sqlite3 --scope ACTUAL_SCOPE --output /private/approved.json
```

**An import replaces the previous cloud snapshot.**
Keep the preceding export for rollback. A snapshot is not a sync or a backup
of the full local database. Never copy a live SQLite file by itself because
its WAL can contain committed data. Never commit SQLite dumps, exports, source
messages, credentials, Site IDs or hosting secrets to this public repo.

## Attach the existing QA visualization

The separate QA site is the visual reference, not a source of real records.
Reuse the existing Vite frontend in `apps/graph_viewer/web` and port its
`apps/graph_viewer/docs/contract.json` backend boundary behind the same
private Sites Worker as D1 and MCP. Do not point the QA frontend at these
simplified JSON reads as if they were contract-compatible. Serve a coherent
revision manifest, node/edge ordinal buffers, and revision-pinned card reads;
start the cutover with a synthetic export and verify mobile navigation,
selection, and matching IDs before loading real data. Larger graphs should
use owner-protected prepared assets, not a giant JSON record or public assets.

## Not ported yet

- The current graph view's exact binary protocol, history/as-of selectors,
  saved source cards, SSE, and semantic ranking. A bounded imported read
  interface should not be mistaken for parity with them.
- Live intake. Move the producer off the Mac or explicitly connect a cloud
  source; a one-time snapshot won't acquire future local hook events.
- Python worker parity: a bounded durable D1 job ledger, idempotency and leases,
  retry/checkpoint policy, independent review, and a verified scheduled runner.
  Do not assume Sites exposes Cloudflare Queues or Cron. Use a supported
  scheduler or an authenticated, explicitly bounded processing entry point.
- Generation of new thoughts, links, overview versions, or answers.
  Move model generation and reviewer calls to an authorized HTTPS provider
  with secrets managed by Sites, not the local Codex CLI subprocess. Port
  invariants from the current engine tests (immutable versions, provenance,
  review before publish, retries without duplicate writes). Keep saved-answer
  reads separate from any generation or status-changing tool.

To cut over, run the existing engine and the port on the same synthetic
fixtures. Compare policy, versions, retrieval, graph relations, and job
behavior. Verify rollback and an unattended run before disabling the Mac.
