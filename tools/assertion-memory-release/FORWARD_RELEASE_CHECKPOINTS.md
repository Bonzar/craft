# Integration and platform limits for the forward train

The checkpoint adapter is source-only. Its protected callbacks need to be
implemented and reviewed before any call from the real controls. Do not put
its mutex in GitHub workflow concurrency: it must serialize receiver effects
across all authorization sources for a given product (ordinary deployment,
supervised restoration and publication). Products remain independent.

| Existing entry | Required protected action at the boundary |
| --- | --- |
| No present canonical prepublication workflow | Reserve exact tag and SHA in authority, verify fresh PROD or exact fix-only evidence; only then let an approved publishing principal create the tag and immutable Release. Bind the exact returned ID. |
| Base and Visual Release-triggered build jobs | Independently resolve provider Release and exact peeled commit; assert previously bound reservation before compiling; atomically bind exact output/archive digests via existing trusted artifact resolver before an activation can consume it. No PR test exception is a release-build exception. |
| Base deploy-test, Visual test action | Resolve immutable artifact using existing lifecycle helpers; require current exact reservation before release send; actual served lane probe verifies version/revision/digest and generation. |
| Base protected production via reusable configured bridge, Visual prod action | Keep existing protected approval. Inside receiver compare fresh actual TEST, reserve one outcome, activate only exact artifact, and close only after actual PROD verification. When result is uncertain, keep blocking and reconcile, not automatic force unlock. |
| Current 0.1.x, urgent gateway, old-tag rollbacks | No version changes now. Supervised, exact old release exception must use existing protected entry and same product lock, isolated from forward reservations. No blanket emergency bypass, and no special exception for a new version. |

## What can and cannot be guaranteed without a new token

* GitHub tag rulesets support Restrict creations, updates and deletions. Reserve
  first, then restrict version-tag creation to the existing narrowly scoped
  publishing principal, if one exists and can be restricted to guarded entry.
  Test effective deny before cutover. Current Base/Visual VM deployment
  credentials are not GitHub publisher credentials. Do not repurpose them.
* A publisher may attach a Release to an already-created tag. Apply the gate to
  both tag creation and the exact first publication step; staged tag reservations
  do not authorize an arbitrary new Release ID. Post-event build gates do not
  make a prior manual publication impossible.
* GitHub's workflow GITHUB_TOKEN cannot emit the existing Release-triggered
  build run, even if repo writes were granted. A redesigned explicit dispatch
  or same reviewed workflow can do builds if an existing restricted publisher
  is available. Do not create a broad PAT. If no suitable existing principal
  exists, Owner-only supervised prepublication is operational policy, not an
  enforceable no-bypass guarantee. Repository Owner can reconfigure controls.
* Restrict eligible bypasses; do not promise Owner is technically locked out.
  Source and proof checkpoints remain necessary after a publisher is selected.

References: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets
and https://docs.github.com/en/actions/concepts/security/github_token.
