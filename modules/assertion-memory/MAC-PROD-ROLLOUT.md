# Mac PROD autocapture rollout

This procedure assumes an approved combined Base + Mac release and a released
private machine endpoint at the configured HTTPS origin (path
`/memory-api-prod`). It does not alter the installed ChatGPT Sites plugin.
Never use VM or Gateway service credentials on the Mac.

## Prepare and check the complete release

Use a clean detached checkout of the immutable release tag. Verify the approved
commit independently; verify the signed release artifact's checksums if an
archive supplies the checkout. Keep the launch/profile configuration from the
prior installation (extra module roots, active Desktop trust profile and
project-vs-global hook scope).

```sh
cd "$RELEASE_CHECKOUT"
test "$(git rev-parse HEAD)" = "$APPROVED_RELEASE_SHA"
test -z "$(git status --porcelain)"
"$JARVIS_PYTHON" -B -m unittest tests.test_assertion_memory_machine_capture \
  tests.test_assertion_memory_lifecycle tests.test_assertion_memory_capture \
  tests.test_assertion_memory_frontier
stage="$(mktemp -d)"
chmod 700 "$stage"
"$JARVIS_PYTHON" tools/jarvis-install --harness codex \
  --settings-dir "$stage" --no-trust --dry-run --source-head "$APPROVED_RELEASE_SHA"
```

Review the installer plan. Do not pass an assertion-memory-only module root:
Jarvis removes prior owned entries absent from the requested module set.
Preserve the exact prior `--extra-modules`/profile options. Run the same installer
with the existing target profile (`--settings-dir "$CODEX_SETTINGS_DIR"` and
the approved profile/trust arguments, not `--no-trust` indiscriminately).
The installed module and hook trust are a unit; swapping a few .py files is not
the supported install method. `tools/jarvis-desktop-setup` is the project
bootstrap, not a substitute for updating the active global profile.

## Bind, verify, and roll back

1. Close the Mac Codex client and stop only this collector if it is running.
   Make a private backup of the existing binding/installer manifest/config
   as files (do not display them), or leave the existing profile untouched
   until the operator can recover its prior release. Installer file writes
   are atomic individually, not a transaction across the profile.
2. Verify the machine catalog via the released client's approved read-only
   route after adding the owner-approved Mac credential to Keychain. Verify
   all four collector receipt operations are present. An independent operator
   must confirm the exact PROD HTTPS origin. Do not change global MCP/plugin
   registrations to point at the private Base HTTP API: it is not an MCP URL.
3. The legacy installer generates its own `mcp/installed.json`; it does not
   install machine bindings automatically. Apply a machine binding only to the
   assertion-memory module, after the install, with a same-directory
   mode-0600 temporary file and atomic rename while Codex is stopped. Preserve
   installed actor/scope metadata. The connection contains only
   `transport=private_base_http`, exact HTTPS `url`, and Keychain service/account
   references, never a token. Set `capture.enabled=false` initially; use a new
   owner-only state root and establish one timezone-aware UTC cutover timestamp.
   Compute `endpoint_sha256` over the UTF-8 bytes
   `private_base_http\\0prod\\0<URL>\\0<fresh_since>` (each `\\0` is a NUL).
   Re-read and run `verify_machine_binding` against the exact staged bytes.
4. Restart only after approval to enable. Set `capture.enabled=true` atomically
   with the final cutover timestamp and recomputed digest; do not open/replay
   existing sessions as proof. Confirm the new one-session owner-authorized
   canary reaches PROD once; no expansion of permission or silent backfill.
5. To roll back, atomically restore disabled binding first. The worker compares
   the file each pass and exits on change; close the client if cutting over
   before any new session, allow bounded in-flight requests to settle, and
   stop only this worker after verifying process identity if needed. Preserve
   the outbox. Reinstall the prior approved complete release with the identical
   profile options if rolling source back; re-read the binding afterwards
   (the installer can regenerate it). Rollback does not erase accepted PROD
   records.
