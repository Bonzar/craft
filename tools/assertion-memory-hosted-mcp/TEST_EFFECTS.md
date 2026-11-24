# Owner-only real TEST capture and processing, dark by default

This change only exposes the two existing canonical handlers. The VM archive,
worker, raw-message/queue logic and generation do not change. No database
reset or data insertion is performed here. Do not seed or use synthetic payloads.

1. Keep the current private TEST Site, exact owner allowlist, OAuth connection,
   owner hash, serverInfo, and all six legacy tools. Confirm current Site archive
   source is the deployed release; use stage_test_effects.py on exact v22
   test-hosted-mcp-entry.mjs to assemble a private stage. It refuses input drift
   and prints only file hashes. Do not publish an infrastructure config or token.
2. Use the real TEST inode/cgroup profile with actual approved release and
   capabilities baseline_read,intake_process. Pin identical compiled catalog
   hashes in Worker and VM. Load a fresh, distinct root-only systemd credential
   test-effects-bearer; never reuse either the canonical bearer or read bearer.
   The generated worker uses separate masked HOSTED_MEMORY_TEST_EFFECTS_TOKEN,
   and stays dark until HOSTED_MEMORY_TEST_EFFECTS_ENABLED=true.
3. A separately authorized gateway/ingress operator later creates exact TEST-only
   POST /memory-mcp-test-effects using the same verified TLS origin as the
   existing read path. Forward only content type and X-Memory-MCP-Test-Effects-Auth
   mapped to the upstream Authorization. Do not accept caller Authorization,
   cookies, wildcard routes, redirects, caches, another host, or a PROD path.
   The loopback receiver independently authenticates that path before processing.
4. Before enablement prove unauthenticated/wrong token and disallowed tools fail
   without touching backend, observe read paths unchanged, stage byte-identical
   Worker+catalog hashes and review private ACL. After enablement verify owner
   native tools/list and read-only status against actual empty TEST. Invoke
   capture/process only for explicitly chosen real source material/check, not a
   placeholder. Never retry an ambiguous call automatically; the caller may
   check canonical receipt/status. Stage can be disabled by turning off the
   Worker flag and removing the separate ingress credential after draining.
   PROD stays read default, generation/search/artifact ingestion are not added.
