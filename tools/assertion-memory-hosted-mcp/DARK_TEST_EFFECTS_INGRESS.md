# Dark, isolated real TEST effects ingress stage

No database reset, provider/model change, password, D1 owner change, synthetic
capture, active Site or PROD change is performed by these tools. Start from
the previously verified open/readable canonical TEST database and release.

1. Leave the live read listener on 8832. Prepare separate root-owned
   /opt/assertion-hosted-mcp-test-effects and private /etc counterpart. Use the
   new effects unit (do not enable at boot while qualifying). Verify 8834 is
   free; verify exact immutable release Python/installed dependencies and
   TEST listener cgroups from root via supported host access. No assumption
   of shell availability. Stage daemon off first; verify copied modules'
   digests, unit syntax, and exact isolated DB file identity. Device/inode
   pin format stays the same as REAL_TEST_IDENTITY.md; label flag comes from
   actual verified graph argv. Verify VM environment end-to-end read-only.
2. Effects profile: lane=test, base_release=existing release tag,
   canonical_mcp_port=8893, enabled_capabilities=[baseline_read,intake_process],
   test_database_mode=real, effects_only=true. Use canonical exact new
   catalog file hash af97145d37be8e016403417521310c5fafa71bb2062beab0d674d263708101aa
   as catalog_sha256 and old 0315d8a2e1ffe977d9c99bbf39f65aef5523adb929d9d3fe5260dca8e45bb151
   as read_catalog_sha256. Catalog asserts selected canonical release revision.
   This candidate is for v0.1.1 only; if the real host has another release,
   extract and pin its exact catalog before any start. The root-supplied
   effects bearer is new, server-only, independently scoped and guarded
   against reuse of read and canonical bearers. Copy no secret into code,
   CLI args or logs. Loopback 8834 serves only the exact effects path.
3. Stage with stage_nginx_test_effects.py --baseline <exact private copy>
   --expected-baseline-sha256 <approved current Nginx digest> --new-stage-dir
   <fresh private dir>. Require original read fragment exact; prove every
   old byte survives after removing just effects.fragment. Separately verify
   effective nginx -t in an isolated mount namespace, hold existing
   transition fence, and never touch settled/rollback witness.
4. Stage Gateway using stage_test_effects_gateway.py --baseline
   <private get-spec copy> --expected-baseline-sha256 <approved digest>
   --expected-origin <already verified HTTPS origin> --output <new path>.
   Requires eight original exact paths with the correct TEST read mapping.
   Only adds exact POST; new scope maps the separately named header to VM
   Authorization, strips user-supplied cookies, has no redirect/cache.
5. Until exact native verification is authorized, keep effects flag unset
   or false. Stage candidate from exact v22 using stage_test_effects.py, keep
   owner/OAuth/legacy unchanged. v22's old read responses remain valid
   throughout; generated Worker checks the old digest on read and new on
   effects. No alternate credentials or owner fallback. Qualification before
   enablement: loopback and HTTPS no bearer/wrong bearer reject (no body);
   authenticated deliberately disallowed operation rejects before MCP;
   original read path still works. Explicitly do not invoke capture/process
   merely to prove the idle/empty service.
