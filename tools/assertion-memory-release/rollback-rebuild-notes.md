# Explicit old-tag rebuild (source-only contract)

A rebuild is a new deployable package of an immutable older source revision, not
restoration of expired binary bytes and not the original release-build identity.
The existing original Release resolver must remain strict; never silently
switch an expired version to a rebuild or execute old workflow definitions with
today's credentials.

Proposed order, not enabled here:

1. A protected current control resolves the exact published immutable Release
   ID, tag, peeled commit and ancestry from GitHub. A reviewed compatibility
   table pins the acceptable exact tuples of product, layout, Release ID, tag,
   revision and supported data-schema migration/rollback constraints. A request
   may select an entry, never define one.
2. A pinned current workflow builds the checked-out candidate source on an
   isolated read-only-token runner, no deployment keys, OIDC, environment
   secrets or persisted Git credentials. Resolve candidate source separately
   from workflow identity. Use a protected adapter for checked historical
   packaging; don't spoof a release event to obtain an activatable tag. An
   unsupported schema, missing dependency or incompatible layout stops here.
3. A separate fresh runner runs only protected code. Independently re-resolve
   source ancestry and builder workflow metadata, download the unique bounded
   same-run ZIP, verify current package layout and exact inner payload, and seal
   a typed claim using a trusted verified attestation signer. The verifier must
   bind repo numeric ID, exact signer workflow and commit, signature issuer and
   subject, not trust a JSON value for any of these. Candidate code cannot run on
   this sealing runner. Use provider ZIP digest as subject or an independently
   verified subject envelope binding that digest; never trust a receipt supplied
   by the build job. Rotate allowlisted control commits deliberately.
4. Explicit rollback deployment consumes the distinct `RebuiltOrigin` identity,
   re-downloads and verifies the exact ZIP and inner payload, then enters
   existing protected TEST environment/concurrency. Produce fresh same-run
   successful activation/smoke witness binding rebuilt origin and digests. Only
   then allow protected PROD approval and exact same artifact. The normal
   source resolver and normal release trigger must not accept this origin.
   Packed Visual requires static/runtime/package binding and the corresponding
   packed TEST witness, not only the UI archive.
5. Code rollback does not roll back SQLite or business data. Unsupported
   downgrades must be refused before deployment. Preserve active runtime and
   verify same-artifact TEST smoke against the relevant supported schema before
   replacing PROD; a tag alone is not proof of that compatibility.

This commit implements the pure origin admission gate and adversarial mocked
provider tests. It does not implement the authenticated signature adapter,
historical builder, archived storage, modified protected controllers, TEST
witness or deployment, and it cannot deploy, rebuild, or publish by itself.
Existing ephemeral Actions artifacts are still subject to expiry.

## Reviewable workflow wiring checklist

The following states are separate from the original-release path. An old-release
version is never passed as the controller checkout ref.

| Step | Trigger and job boundary | Required evidence and failure behavior |
| --- | --- | --- |
| Resolve | Explicit dispatch on protected main; exact known historical tag only | Read immutable Release by tag and ID; peel to commit; verify main ancestry and compatibility allowlist. Verify protected entry workflow path, exact revision and attempt 1. Nothing consumes a candidate-supplied URL, shell command or runner label. |
| Build | Dedicated GitHub-hosted runner, pinned current workflow, read-only repo access, no deployment environment or inherited secrets, no OIDC; candidate checkout by complete SHA | The checkout may contain executable old project code; prohibit its execution outside this job and use no write credentials. Produce the canonical profile package and ensure it can be authenticated as the requested old tag without synthesizing release-event metadata. No retry from an arbitrary workflow/run ID; a new explicit protected dispatch is a new rebuild. |
| Seal | Trusted separately checked-out and pinned workflow on a fresh runner; accepts an authenticated handle, never runs candidate code | Independent repository/path/attempt/revision and artifact provenance; bounded ZIP intake; verify profile layout/allowed contents/payload digest. Trusted signing adapter binds issuer, repo, exact sealer workflow+SHA and ZIP subject; require exact structured claim. Receiver ignores unsigned inputs. |
| TEST | Explicit rollback deployment workflow on protected main, existing configured TEST environment and exact profile concurrency group | Resolve the attested rebuild again; verify provider ZIP and unpacked payload. Authenticate/activate exact digest with protected TEST on-host receiver; read smoke from actual service path. Produce `rollback-rebuild-test-proof.json` only after success and upload as a unique same-run bounded Actions ZIP. |
| PROD | Same protected rollback deployment workflow run, `needs: [test]`, existing configured PROD environment and profile concurrency | Verify stage approval independently (reuse the existing approval policy, never rely solely on green TEST or a workflow variable). Re-resolve the same rebuilt origin, re-download and hash the same provider ZIP and payload. `promote()` independently verifies the protected main run, successful TEST job and required steps, then requires GitHub TEST ZIP, TEST output and expected origin to match. Sign a distinct rebuild audience; normal-release audience/receiver must not interpret it as an original build. No approval can substitute a new or rebuilt-again artifact. |

Current configured controllers (`experimental/configured_release_run.py` and
`configured_provider_checks.py`) verify GitHub stage approval, call the
canonical original resolver, and before PROD match an independently downloaded
TEST receipt against job output and expected identity; a separate origin and
receiver proof must keep the same guarantees. Do not bind the new deploy
workflow ID or accepted signer through dispatch values. Obtain them from
reviewed current policy and verify provider metadata from authenticated GitHub.

`rollback_rebuild_promotion.py` now implements the offline same-run evidence
contract for steps TEST/PROD. It cannot approve an environment or authorize
a receiver call. Remaining integration includes the actual historical
packager, real cryptographic adapter and trust-root pinning, wiring existing
stage-approval/receipt authenticity to the new origin, and a product-specific
receiver audience. Verify live GitHub metadata semantics (runner/job names,
download size metadata, workflow identity and attempt fields) in disposable
non-PROD tests before any activation. Existing deployed release workflows,
environment protection, retention, and active runtimes remain untouched.

`rollback_rebuild_base_input.py` implements the Base sealing runner's first
non-executing input qualifier for the documented `base-v1` layout: bounded
extraction/digest, exact historical tag and revision, exhaustive archived
membership, exact immutable historical Git blobs and derived dependency list.
It does not prove the generated Web/schema semantics, run a historical build,
or sign any artifact. The protected integration must pin a compatible build
adapter that explicitly supplies the selected tag (the current ordinary build
writes it only on the real release event); it must neither fake release event
variables nor run the old checkout on the trusted sealing runner. One separate
packed Visual qualifier is still required. The compat table may admit only
precisely rehearsed older tags, not all versions sharing a tag prefix.

The trusted assembly adapter `rollback_rebuild_base_compile.py` can now
materialize a fresh deterministic Base-v1 archive without the old workflow,
and calls the qualifier before returning its digest. `rollback-rebuild-base-isolated.sh`
runs historical compilation only in a preloaded digest-pinned, unprivileged,
network-disabled Docker container with no host secrets or HOME, passing an
explicit selected revision to the catalog extractor. The review specimen
`rollback-rebuild-disposable-build.yml` is deliberately disabled/outside
workflows: exact compatibility resolver, offline image production/verification,
and signed generated-output handoff are not implemented. Do not relax sandbox
restrictions or fake a GitHub release event to run the old compiler.

The disabled receiver-side contract `rollback_rebuild_receiver.py` models the
final Base PROD authority. Current Base already verifies signed GitHub
OIDC claims, pins the configured caller/reusable workflow and uses a
receiver-owned SQLite challenge ledger before its host writer. The new
contract instead requires the distinctly typed rebuilt promotion and a
separate rebuild audience, with exact old release, built archive/payload,
protected current caller/child/actor/lane, run, TEST witness and VM in scope.
It verifies RS256 against independently supplied issuer JWKS and compares the
verified TEST payload hash with a measured staged payload hash. Nonce and
first-attempt run reuse are rejected durably. An original-release promotion
cannot satisfy this contract, even when a valid issuer signs its normal
audience.

Still missing from live integration: policy installation and authenticated
fixed-issuer JWKS retrieval, authentic caller/lane bindings from a real
protected workflow, and a scoped receiver entry bound to an owned durable
ledger and the same sealed input file/FD from hash through activation.
Do not route this module directly from SSH, use a caller-supplied measured
digest or pass staged bytes to a host writer before verification/consumption.
The separately signed builder/sealer evidence adapter also remains required.
No token, key, host configuration or product runtime is installed by this PR.
