# GitHub-only release graph preview

The version tag is the key:
- Base: assertion-memory-vX.Y.Z.
- Visual: assertion-memory-visual-vX.Y.Z.

From GitHub Releases, choose the version tag and target commit from main
history (Target → Recent Commits where GitHub shows it). If an older commit
does not appear, create/select a tag on its verified exact full SHA through
GitHub CLI: gh release create TAG --target FULL_SHA. Do not use an existing
tag until its real Git reference has been checked: when it already exists,
the target argument does not retarget it. Publication/tag creation must be
intentional; neither is done by this workflow. Immutable releases prevent
retargeting once published. A historical application commit must already
contain its compatible existing builder; simply adding a workflow to
current main does not retrofit a tag that predates that builder. Use a
retained archived original build for existing releases; do not republish.

New green release builds for either product assemble once with the existing
workflow. A single workflow_run receiver on current main opens a GitHub
Actions run for the canonical tag. The graph first verifies the release
and upstream build, then TEST preview hashes archived bytes without
extracting or executing them, then PROD preview proves the same digest.
Both preview stages are read-only and do not create a deployment, call the
VM, or send any credentials. The run summary links to the native release
card and original build. No release or TEST numeric ID is typed; internal
IDs and digests come from GitHub metadata after validation. Both workflows
remain unchanged, including any existing independent TEST dispatch.

To rehearse an already published immutable tag, manually run this preview
on main using the tag alone, for example assertion-memory-v0.1.0. All
permission blocks are read-only; there are no environments, write tokens,
secrets, or deploy steps. Only current main is checked out, never the
upstream tag workflow's source or cache. A green preview is not a live
deployment or service smoke. It never edits release notes or attaches an
asset. This also avoids publishing internal artifact files just to show a
link from the Release page.

## What remains before real native approval buttons

GitHub offers native Review deployments on an Actions graph for configured
protected environments. Wire live TEST then PROD only after explicitly
qualifying each scoped environment and protection, enforcing actor/reviewer
separation, and certifying source and actual deployed receipt/digest. Base
TEST currently has no required reviewer, base PROD has one, and visual
environments have not been provisioned. Per-environment main branch policies
are not the same as repository branch protection. The preview does not
enable or claim any of these gates.

The existing production authority deliberately accepts only the exact
manual workflow_dispatch caller and signed claims, and requires a successful
TEST run on exactly that control commit. Do not reuse it for workflow_run
or native in-run approval until there is a separately reviewed host policy
and one-time OIDC qualification. A common real per-release graph may verify
its completed successful TEST job and its exact receipt while the outer run
is still waiting for PROD; do not wait for the whole run to finish before
allowing a dependent job. Native approval separately addresses publisher
versus deployment reviewer. Keep one activation path and independent
scope-specific identities. Remove none of the trusted gates for a simpler UI.

GitHub documents [target selection](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository),
[exact SHA using gh](https://cli.github.com/manual/gh_release_create),
[workflow triggers](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
and [deployment reviews](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/reviewing-deployments).
