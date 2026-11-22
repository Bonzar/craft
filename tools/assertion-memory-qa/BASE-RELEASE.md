# Assertion Memory release lifecycle (proposed, local only)

1. In **Releases → Draft a new release**, select a **new** tag
   `assertion-memory-vMAJOR.MINOR.PATCH` and target the chosen full commit
   SHA already in `main` (if the UI only offers branch tips, an authorized
   operator creates the exact tag at the selected SHA before publication).
   Publish it. The release event runs CI and builds one artifact on GitHub.
   A draft does not build. The final release must report `immutable: true`.
2. After the build is green, open **Actions → Assertion Memory Base Release →
   Run workflow**, choose **main**, paste the numeric GitHub **release ID**,
   and run once. This initiates TEST; there is no separate build action.
   The release build card’s run name shows its release ID. IDs are
   stable identity, tags label versions, and Action run IDs are not inputs.
3. After the receiver replies it activated and smoke-tested TEST, open
   **Review deployments → assertion-memory-production → Approve and deploy**
   on that exact run. This is the sole promotion action. Protected PROD runs
   reverify the release and download and hash the same artifact, and compare
   release ID, artifact ID, full SHA, and inner archive SHA-256 with the
   actual successful VM TEST receipt. No rebuild happens here. No PR or
   push triggers this workflow’s deployment jobs.
4. To roll back, select an older still-retained immutable release ID at step 2.
   Verify it in TEST again and approve its own run in step 3. Avoid approving
   stale runs accidentally; check release ID in the run title. Skipping TEST,
   rerunning old approvals, or retargeting a release is unsupported.

## Required before end-to-end

- This new workflow must be registered on the default branch (assumed
  `main`) before native Run workflow is visible. This branch does **not**
  put it there. Only releases of commits with these workflow/build files
  and compatible receiver payload are supported: this does not retroactively
  make arbitrary historical main commits releasable. Do not enable the older pilot branch automatic deploy settings;
  this main integration excludes its workflow entirely.
- The deployment jobs are skipped until repository variable
  `ASSERTION_MEMORY_BASE_DEPLOY_READY=true` is separately provisioned.
  Absent the variable, a dispatch does not deploy. Confirm/enable GitHub
  immutable Releases before publication. GitHub
  dispatch by arbitrary branch is intentionally rejected. Configure
  explicit branch permissions for `main` on the TEST/PROD environments after
  authorization; retain required reviewer `Bonzar` and disable bypass where
  supported. Provision the two independent restricted SSH channels, pinned
  host keys, deployment enable flags and TEST/PROD receiver environments.
- The existing optional root-owned per-artifact activation gate must
  still validate the exact SHA/digest. If it requires an extra manual host
  write, this proposal **cannot activate PROD with one click yet**. The host
  gate must separately be prepared to consume authenticated approval and
  successful TEST evidence for the identical bundle; do not remove or
  silently weaken it. No production keys, flag, or gate are changed here.
- Current artifact retention requested is 90 days, subject to repository
  policy. This workflow intentionally does not append assets to the published
  immutable GitHub Release. An expired/deleted/missing/duplicate artifact fails closed; an
  original failed build is not rebuilt under the same release identity,
  use a new release. A long rollback window needs a separate durable
  content-addressed store or equivalent, not an unverified rebuilt archive.
- Smoke validates what the receiver reports; it does not replace live
  receiver/port/systemd/data migration qualification or known smoke
  coverage. GitHub Actions deployment concurrency is not a FIFO; resolve
  pending runs deliberately. No rollout/approval is performed by this patch.
