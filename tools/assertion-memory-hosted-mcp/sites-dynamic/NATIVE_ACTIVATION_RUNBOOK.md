# One-time Sites bridge activation from a GitHub release

Source/operations handoff only. Do not deploy from an unmerged draft, no
direct publication today, no credential creation/rotation during this review.

## Existing Site authority

Always resolve both existing exact owner-private Sites freshly from the native
connector (MCP connection included). Never accept literal project or owner
identifiers as public GitHub artifacts. Preserve both owned projects, their
native MCP plugin IDs, sharing and all surrounding visuals.

`scope_pins.py` consumes only the two trusted native get_site results on
stdin and derives the six repository-level variables. Project and origin
hash the exact UTF-8 value without LF. ACL hashes sorted-key compact ASCII
JSON version 1 plus exactly one LF. It requires the owner role with one
matching allowlist ID, no groups/editors/external users, and excludes provider
timestamps, access revisions and display metadata. Both lanes may have the
same ACL hash when genuinely under the same sole owner; their Project and
origin pins must differ. Do not disclose raw provider metadata or secret
values. Validate the same canonical identity before and after deployment.

## Controller inputs (no endpoint or session substitutions)

Controller input: immutable published GitHub release tag + release ID + tag
commit; original trusted package workflow run/artifact ID; provider ZIP digest
and recorded inner package SHA256; target lane; exact protected Base release
identity and runtime/gateway checks; prior saved native publication receipt.
PROD also requires exact same inner package SHA256 in the green TEST receipt
and separate explicit PROD promotion. Neither a branch build nor a user-edited
manifest is sufficient to supply authority.

Use the GitHub connector to read the published release, original exact workflow
run, and artifacts; use github_download_workflow_artifact with exact artifact
ID only. It returns file reference for the ZIP, not a Sites archive. Hash the
ZIP against independently retrieved artifact metadata, reject unexpected entries,
hash the inner archive against the release's build evidence, and verify the
manifest's tag/source SHA and every payload hash. Reject missing, expired,
wrong-repository, wrong-workflow or unverified original artifacts. Validate
exactly one shared Worker and two lane config-only envelopes; no private material
in GitHub. Do not download a "latest" name.

## Qualification and native publishing

1. Confirm the verified Base private generic HTTP catalogue and operation
   handlers are active for the target lane, using its exact release, digest,
   generation, approved lane proxy allowlist, and its existing separate
   credential scope. The old 17-method relay does not satisfy this handshake.
   Confirm that the opposite lane and unauthenticated access reject. No real
   data writes are needed for preflight.
2. Read the existing Site + MCP connection with get_site. Require the owner,
   private ACL and prior exact source/native receipt expected by the selected
   manifest. Derive the project pin, origin pin and canonical access identity
   pin independently. Never use the private publish action as an access probe.
3. Clone/reopen the exact existing private source with native source helper
   authority, without auto-publish. Stage ONLY the released shared Worker and
   each existing Site's thin profile/identity wiring. Bind the TEST D1 owner
   verification / PROD existing owner pin through the existing authenticated
   Site identity; no new fingerprint output, owner migration, client token or
   arbitrary upstream. Preserve the legacy bridge when dark and all visual
   routes/profiles; active dynamic calls use authoritative Base only.
4. Retrieve dedicated lanes' secret **names only** and require installed,
   server-side values matched to Base. TEST already has its distinct read
   secret; its effect secret is not installed. PROD already has its owner pin,
   not the new hosted read and effect secrets. A prior pilot/fixture/graph
   secret cannot be reused. For strict secret custody, directly authorized
   masked owner input or independently verified vault delivery must install
   new values; current Sites update tool takes value:string and is_secret:true
   only protects readback. Do not treat that as a zero-plaintext transport
   guarantee; never pass secrets through GitHub artifacts. No re-login,
   re-enrollment or owner sharing change is otherwise expected.
5. Under the actual GitHub release gate only, the controller pushes exact
   reviewed Site source; Site source SHA is normally different from GitHub
   bridge source SHA. Build the native archive using the Site helper from
   exactly the pushed SHA. Then call save_version_and_deploy_private with
   existing project_id, exact commit_sha and absolute archive path; never
   publish to a new Site or fall back to another audience. Retain version_id
   and deployment_id. Nonterminal response follows get_deployment_status;
   on a saved-version partial failure do not save again. Re-read the owner
   policy, exact source and unchanged plugin identity after success.
6. Leaving flags unset/false permits a reversible dark stage even before
   effect secret bootstrap; it is not a feature acceptance. Once credentials
   and Base are ready, apply only protected runtime flags; Sites environment
   changes require a native deployment of the selected saved version, not
   just config write. Keep current source and pinned version unchanged.
   Verify authorised native catalogue/list and a real read-only TEST action.
   Do not add/replay a cinema record or run effectful actions solely for smoke.
   Native client inventory may need supported refresh/new session; do not
   infer that it refreshed from server list alone.

Receipt: GitHub release tag/source + original run/artifact + exact ZIP and
inner SHA; lane, Site exact pushed SHA, native saved-version/publication IDs
+ env revision, before/after same project/owner-only ACL/plugin identity and
read-only native receipt. Validate against the same immutable bundle using
verify_native_receipt.py; keep native IDs private. Failed gate leaves original
version/ACL in service or reverts by an explicitly authorised release-linked
rollback to the previously verified saved version. No independent native
publication is authorised by this runbook.
