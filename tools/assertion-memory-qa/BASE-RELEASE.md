# Assertion Memory release lifecycle

**PROD is verification-only and non-operational in this patch.** No readiness
flag can enable archive transmission before the independent host proof bridge
is integrated and separately reviewed.

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
   This is an explicit first-attempt authenticated dispatch by an allowlisted actor;
   an authorized agent may initiate it. The code does not impose a second hard-coded
   human reviewer. Any existing GitHub environment review rules still apply unchanged.
   Currently the run verifies evidence and then deliberately fails at the host-proof
   boundary; it does not send an archive.
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
- TEST retains its existing repository readiness variable
  `ASSERTION_MEMORY_BASE_DEPLOY_READY=true`. PROD independently requires
  `ASSERTION_MEMORY_PROD_READY=true` (unset means disabled); TEST readiness cannot
  enable PROD. Set PROD readiness only on `assertion-memory-production` after
  independent qualification. Even when true, this patch cannot send PROD bytes. Confirm/enable GitHub
  immutable Releases before publication. GitHub
  dispatch by arbitrary branch is intentionally rejected. Configure
  explicit branch permissions for `main` on the TEST/PROD environments after
  authorization; do not remove any current environment review rules or bypass restrictions. Provision the two independent restricted SSH channels, pinned
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

## Explicit PROD dispatch authorization (verification only)

The job uses `assertion-memory-production` and checks `GITHUB_ACTOR_ID` against
both authenticated run `actor.id` and `triggering_actor.id`. It requires the exact
manual production inputs, first attempt, protected `refs/heads/main`, repository,
workflow path and SHA. Environment policy must explicitly select only branch
`main`, never a wildcard or tag. Existing environment reviewer rules are not edited.

`ASSERTION_MEMORY_PROD_ACTOR_IDS` must exist **only on the protected PROD
environment**. Its strict syntax is 1–50 distinct positive numeric GitHub actor IDs
separated by commas, without spaces, wildcards, leading zeros or empty items.
There is no default owner, hard-coded actor ID, or repository/organization fallback.
The verifier reads the exact environment-variable API endpoint, not inherited
`vars`, to establish the configuration source. Missing configuration, unknown actor,
unreadable policy, missing branch/tag type or any API denial fails closed.

Live environment access returned `Forbidden` during development. Configuration
and runtime readability are **not confirmed**. The environment-variable read API
requires Environments read permission; the existing job retains only contents/read
and actions/read, without new credentials or scope grants. Consequently its token
may fail at that boundary. Resolve the read contract in the separately reviewed
host authorization integration, not by adding broad credentials to this workflow.
[GitHub environment-variable API](https://docs.github.com/en/rest/actions/variables#get-an-environment-variable).

## Separate host authorization bridge (design requirement, not implemented)

Implement authorization only in an immutable, full-commit-SHA-pinned reusable job.
It must check out its **own exact trusted SHA**, not caller-provided or release
payload code. Its PROD environment must be branch scoped. Grant `id-token: write`
only to that authorization job, never build/TEST, the current verification job,
or payload code. A trusted host service must validate OIDC signature, issuer,
audience, repository ID, main ref, protected environment, actor, run ID/attempt,
and pinned `job_workflow_ref`/`job_workflow_sha`; caller inputs are not identity.

The independently root-administered host authorization service issues a short-lived,
signed nonce challenge bound to: exact PROD run/attempt and control workflow SHA;
authorized actor ID; TEST run/attempt, job and immutable receipt artifact ID/ZIP
digest; original build run/attempt, release ID, immutable build artifact ID/ZIP
digest; release SHA and inner archive digest; target environment and VM identity.
The service independently verifies the exact successful TEST provenance and artifact
identity. Neither a caller-supplied boolean nor an unsigned receipt authorizes it.

An independent durable service ledger records issuance, expiry, binding and atomic
single use. Reject replay, stale/expired evidence, other runs/actors/VMs, mismatched
bytes and absent ledger authorization. The restricted receiver must consume this
exact authorization before accepting/activating the pinned archive. Keep its existing
root-owned static activation gate intact until a separately reviewed integration
satisfies it; do not lower the gate or add an alternate bypass path. The deploy user
must not write the service configuration, signing keys, ledger or authorization policy.

This patch provides no signing service, reusable OIDC job, nonce issuer, ledger,
receiver integration or production credentials. `lifecycle.py host-proof` and
`send-release.sh production` both unconditionally reject. Root/owner must independently
review this PR and the future host integration before merging/enabling release delivery.
