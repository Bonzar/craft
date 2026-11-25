# Canonical native Sites source candidate

New Worker source belongs in Bonzar/craft. The native Sites source repository
must become a generated publication transport, not a second editable source.
The existing two Sites keep separate platform project, origin, audience, D1
data and credentials. Never copy the project ID or ACL into this tree.

This candidate shares Visual delivery, the read-only Graph relay, and the
existing Base-driven dynamic MCP engine. One immutable build profile is selected
per target, consisting of `lane`, exact existing `siteOrigin`, and approved
`gatewayOrigin`; endpoint paths and key **names** come from tracked code, not
HTTP requests. The common engine refuses any other origin/lane. TEST D1 owner
enrollment and PROD platform ID verification are independent. Optional TEST
writes use their own owner/CSRF route and cannot be passed to PROD. Native
boarding should not use the Graph read token to authorize mutation.

Compared the saved native source checkouts for TEST v26 (1c1677f) and PROD v13
(24e7354). Their dynamic MCP relay is already byte-for-byte identical to the
tracked `sites-dynamic/worker.mjs` used here. The Visual shell is equivalent
apart from three per-file TEST/PROD alias conditions; both published releases
already use the same pinned static Visual fallback archive. The current Graph
relay differs mainly in historical PROD binding name (`MEMORY_GRAPH_TOKEN`),
fixed route and absence of the explicit host binding. This candidate preserves
that name and uses the stricter host/lane checks in both modes. It refuses a
mixture of opposite-lane or same-scope key bindings. No deployment source,
hosting manifest, D1 migration, runtime data or fallback asset was copied into
the candidate. Tests use synthetic configuration only.

## Required before cutover

- Explicitly preserve or retire the remaining legacy MCP discovery/fixture
  behavior. The candidate builds composition around an explicit trusted
  `legacy` adapter and verified TEST D1 owner adapter; it does not import these
  modules from a live remote at build time. Review them separately as source
  assets, regenerate wholly fictional fixtures from tracked fixture definitions
  or retire them with approval. Do not copy database rows or unpublished
  content. Until this is complete this is **not** a drop-in native deployment.
- Generate the optional static fallback from the exact verified Visual archive
  instead of copying the embedded native generated module. Bind it by digest;
  the stable alias must still fail rather than silently returning a fallback.
- Freeze before/after native owner ACL and plugin identity, build from the exact
  immutable release, push generated build source via supported owner-private
  publication, and verify native browser and MCP parity in TEST before any PROD
  promotion. Do not copy app enrollment, production data, project manifests,
  deployments, generated fixture snapshots or secrets into this repository.

Run local synthetic route parity: `node --test tools/assertion-memory-hosted-mcp/sites-native/test_worker.mjs`.
