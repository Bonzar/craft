# Redeploy a Base version through GitHub Releases

The VM's filesystem is not the history of releases. Select a version from
GitHub Releases, not a directory on the VM. Keep only the active build in
steady state once the receiver's retention policy supports it; preparation and
transactional recovery may still temporarily need both the incoming and active
builds. This procedure changes no deployment authorization or receiver policy.

## Original artifact still available

1. In GitHub Releases, select the canonical immutable Base tag
   `assertion-memory-vX.Y.Z`.
2. In Actions → Base Release → Run workflow, select **main** and enter that
   tag. This deliberate historical redeploy is distinct from the automatic run
   started by publishing a new release.
3. Verify the Build and TEST jobs succeeded and the TEST proof links to the
   immutable release's original artifact. Approve PROD in Review deployments
   only after reviewing that exact TEST run. No VM checkout or direct deploy.

The controller resolves the tag to a commit on main's protected lineage,
requires the immutable published release, and checks the original successful
first-attempt producer and exact nonexpired artifact identity and digest. It
never selects a companion Sites archive. It cannot use an expired artifact or
rebuild under the same tag. For example, Base v0.1.13 uses original build
45640962887, artifact 15034189779; its Actions retention expires 2027-02-23.
Artifact availability proves eligibility to retest; it does not prove that its
prior PROD activation failure is fixed.

## Original artifact expired, or selecting a commit rather than a tag

Resolve the exact full source commit and confirm GitHub compare
`<commit>...main` is `ahead` or `identical`. Never move an existing tag.

For historical commits proven to have a compatible original Base producer,
publish a **new**, unused canonical Base version tag and immutable release at
the exact selected commit. Keep the regular automatic Base entry on protected
main. This produces a **new** build and immutable artifact: no claim that it
matches old bytes. Require a successful initial producer run, new TEST on the
new artifact, and ordinary protected PROD approval. Never approve an older
queued deployment as a fallback. Compatibility means the historical revision
has the canonical producer path/name and build contract expected by the
current resolver. If uncertain, review its source before publishing; do not
use this path for commits with no compatible producer. No generic
protected-current historical build producer is currently implemented; do not
weaken first-attempt/immutable provenance rules to handle those commits.

A code redeploy does **not** restore database contents or schema. Check that
the selected version supports the current database schema, particularly after
migrations; when compatibility cannot be established, use a forward fix.
A successful TEST against test data is not permission to downgrade a PROD
database, and DATABASE restoration is a separate decision.
