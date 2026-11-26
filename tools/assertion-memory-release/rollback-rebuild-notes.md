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
