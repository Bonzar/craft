# Configured Visual first live profile (inactive candidate)

Version under test: existing immutable Visual v0.1.0. Build verifies the
original GitHub Actions archive and does not rebuild. TEST and PROD reuse the
canonical Visual provider and digest checks, run-scoped same-test evidence and
named GitHub environment approvals. A second stage cannot use a legacy
approval, evidence archive name, signing key, SSH account, or readiness flag.
Base is deliberately not offered in the entrypoint while its native proof
contract is migrated; adding Base binds a provider, not parallel component jobs.
Templates remain inert outside .github/workflows.

Before enabling:
1. Independently review exact source, approval API behavior for reusable
   jobs and test runner's reported workflow/job identities. No secrets proof
   should be interpreted as evidence real credentials are correctly scoped.
2. Provision two distinct restricted VM SSH accounts
   assertion-configured-visual-{test,prod}-deploy, one new SSH keypair and
   a different signer per environment. Each account's forced receiver
   must validate its configured-only signer allowlist and stage, then use the
   existing canonical scoped visual pointer and lock. The legacy receiver's
   allowed-signers file must not contain the new keys and vice versa. If
   sharing the canonical destination requires an OS permissions change,
   isolate policy checks from the shared writer; retain existing rollback.
3. Pre-create configured-release-visual-test and configured-release-visual-prod.
   Require the expected reviewer, main-only deployment and no bypass;
   set immutable expected Environment/reviewer IDs and ready flags only after
   authenticated readback. Add the four CONFIGURED_VISUAL_* named secrets
   in their respective environments only (not repository); use exact pinned
   host, gateway route/token and split keys. Keep the global flag false.
4. Verify an exact evidence-only approval/claim shape from protected main;
   then install templates under .github/workflows with global flag false.
   After root approves and all above checks are verified, enable and dispatch
   once on main selecting Visual v0.1.0. Review TEST, check canonical smoke,
   approve PROD for existing same artifact, then verify canonical pointer.
   No change to previous workflow or data paths at setup.
5. For Base: current v0.1.1 success used its legacy OIDC path. Add explicit
   generic workflow ref/SHA and job attestations with a new scoped audience
   to the Base bridge. In separate tests accept only the exact configured
   Base profile/test receipt, and preserve the successful legacy policy.
