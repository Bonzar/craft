# Assertion Memory release lifecycle

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
   stable identity; tags label versions. Keep the successful TEST run ID for promotion.
3. TEST saves a `test-receipt.json` artifact **only after** the restricted receiver
   confirms activation and smoke success. Wait for the entire TEST run to finish
   successfully. Open **Run workflow** again on **main**, explicitly choose
   **production**, enter the same release ID and the successful **TEST run ID**.
   Approve **assertion-memory-production** under **Review deployments** in this
   new promotion run. Approval by the required reviewer is checked again in code.
4. Promotion validates the TEST workflow ID/path, repository, main ref, first
   attempt, completed successful run/job, activation and receipt-upload steps,
   immutable receipt artifact provenance and ZIP digest. The receipt binds the
   release ID, original build run/attempt, artifact ID, release SHA, build ZIP
   digest and inner archive digest. Its environment must be TEST. The downloaded
   original build must match every field. PROD has no build step.
5. TEST and PROD must use the **same workflow commit on main**. If main advances,
   perform a fresh TEST of the same release on the new main commit. This deliberately
   binds the TEST job's protected environment configuration to the reviewed code
   executing PROD. It never rebuilds or changes the selected release artifact.
6. To roll back, select an older still-retained immutable release ID at step 2,
   then explicitly promote its new green TEST run. Missing, expired or mismatched
   receipts fail closed. The old transport-only TEST run `45170496615` has no
   durable receipt and cannot authorize promotion; perform a new TEST after merge.

A receipt is retained for 90 days (subject to repository policy), not indefinitely.
No push, PR, release publication or TEST completion automatically deploys PROD.
Both receivers must reject non-release commands before any archive is transmitted.

## Required before end-to-end

- This new workflow must be registered on the default branch (assumed
  `main`) before native Run workflow is visible. This branch does **not**
  put it there. Original builds must already contain compatible release/build files
  and receiver payload; the newer control workflow can promote those retained artifacts: this does not retroactively
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

## Qualification still required

This change does not provision the physical PROD receiver, keys, host pin,
activation gate or protected environment. Those must be independently verified
before dispatch. The code is not a claim of production readiness. Repository
configuration and TEST infrastructure are unchanged by this patch. The PROD
credential must remain restricted to `assertion-production-deploy` and the exact
production receiver; an admin credential is not a substitute.
