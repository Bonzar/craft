# Assertion Memory: cloud CI and isolated QA delivery

> Historical QA/pilot tooling reference. This branch does not include the old
> push-to-deploy pilot workflow. For the default-branch release workflow, see
> [BASE-RELEASE.md](BASE-RELEASE.md). Do not provision pilot deployment routes
> or credentials from the examples below during the main lifecycle cutover.

**Scope.** Synthetic QA only. It does not use the Mac, personal DB, active QA
preview, existing MCP/Graph processes, any public-facing proxy, or any model.
There is no deployment to main or production. All embedded records in tests are
fictional; runtime fixture and secrets are generated locally and never uploaded.

## GitHub-hosted CI

Push the reviewed branch `codex/assertion-memory-ci-qa` (or open a PR). The workflow
uses GitHub-hosted Linux runners with Node 24, Python 3.12, the upstream npm lockfile
and pinned Python requirements. It builds the Graph, runs Graph and Python tests
for the documented offline contracts and backup/restore, and exercises original authenticated MCP and Graph against a freshly generated
fictional DB. Full semantic/vector fidelity and provider execution are explicitly
not part of this lexical QA.

The build publishes a versioned data-free tarball plus SHA-256, with its commit
and an internal payload checksum manifest. Only the two runtime source subtrees,
the built Graph assets and explicit QA helpers enter the artifact. No virtualenv,
working tree, private configuration, database or auth secret goes in the artifact.

## Gated operator pull and activation

No deployment runner, SSH key or root credentials are stored in GitHub. The
workflow does not deploy a release merely because CI passes; in the absence of a
provisioned dedicated account this is deliberately **manual gated CD**, not
automatic deployment to the VM.

1. In GitHub create the `assertion-memory-qa` environment; restrict deployments
   to the exact reviewed branch `codex/assertion-memory-ci-qa`, require a reviewer
   where supported, and set the environment variable
   `ASSERTION_MEMORY_QA_ENABLED=true` only after provisioning. Leave it unset until
   the dedicated host and user are ready. Run **Assertion Memory QA** manually on
   that branch and verify that both **build** and **Approve pull to isolated QA**
   actually succeeded for the same full SHA. Do not deploy PR artifacts.
2. An authorized system administrator makes a dedicated Unix account named
   `assertion-qa` on the intended QA host (or alongside other services with its own
   home directory), with no sudo, no memberships granting production access, and
   a persistent user systemd manager if wanted. No changes to existing service
   units, proxy, addresses, firewall rules or ports 8801/8803. Verify ports
   8891/8893 are free before provisioning; both new services listen on loopback.
   Configure QA access through an already authorized channel if needed, not a
   public reverse proxy. Use the pinned host identity when transferring artifacts.
3. On the trusted QA host, place the two reviewed `*.service` files under the
   dedicated account's `~/.config/systemd/user/`, and run
   `systemctl --user daemon-reload` **as assertion-qa**. Do not enable the units
   until the first release passes. Keep a trusted copy of `deploy-local.sh` and
   `verify-unpack.py` from the reviewed checkout on the QA host: don't execute
   an installer downloaded from an unreviewed build.
4. Download the approved run's archive and checksum privately. From the QA host's
   verified `assertion-qa` session (Python 3.12+, venv, SQLite FTS5), run
   `./deploy-local.sh /absolute/path/assertion-memory-qa-<SHA>.tar.gz <archive-SHA256> <full-commit-SHA>`.
   This validates the independently obtained checksum, profile and internal
   manifest before running anything. It creates a fresh synthetic fixture, makes
   new per-release test-only credentials, proves in-process and live Graph/MCP
   checks, and only keeps the new active pointer after those checks pass.
5. If the live check fails it stops the new QA processes and returns the
   `current` pointer to the previous QA revision. Failure on the first release
   leaves no active target. The previous release and fictional state remain
   in `releases/`; no production unit or datastore is read or touched.
   Review failure output from the new release's private directory; do not paste
   any credential files. If the systemd manager itself fails, inspect it locally
   before retrying; the old release files remain intact. Old versions can be
   explicitly pruned after validation; never delete the active or rollback target.

A future automatic deploy may use a narrowly scoped artifact-pull identity on the
dedicated tenant; do not install a general self-hosted GitHub runner against
untrusted PR code on the user's VM.

## Same-VM TEST → PRODUCTION release branch

The existing manual QA pull above is a separate legacy path. The release branch
`codex/assertion-memory-release` uses **Assertion Memory CI and Release**:

1. A reviewed push to that exact branch in `Bonzar/craft` builds **once** on a
   GitHub-hosted Linux runner. CI tests Graph/MCP on fictional data. The archive
   contains code/assets only, not the real or fictional SQLite runtime files.
2. `Deploy TEST` runs automatically after green build. It downloads the build's
   immutable GitHub artifact by ID, verifies run/SHA and ZIP digest, then verifies
   the inner release digest and sends those exact bytes through a pinned,
   restricted SSH command. A green job must include an actual activation and
   successful isolated TEST smoke receipt from the VM.
3. `Promote to PRODUCTION` waits on that green TEST job and on a human approval
   in GitHub for this run. Verify the TEST receipt's revision and digest before
   approving or rejecting. A second check via GitHub's approval-history API
   rejects production even if someone forgot to configure the reviewer rule.
   The job downloads the same artifact ID and compares its revision and digest
   with the successful TEST receipt. It never builds anything. If a promotion
   is stale or the artifact expired, push/review a fresh candidate instead.
4. Branch-only push events support this before merging any workflow to the default
   branch. Pull requests, legacy branches, and an ordinary manual workflow
   dispatch cannot deploy either tier. Re-running failed jobs alone after a new
   run attempt can leave only a prior-attempt artifact; that fails closed. Re-run
   all jobs for a fresh artifact with a new TEST receipt and production approval.

### Provision explicitly in both environments before enabling

Create GitHub Environments `assertion-memory-test` and
`assertion-memory-production`. Permit deployment only from the exact reviewed
release branch and protect that branch from arbitrary direct pushes, especially
changes to the workflows/deployment scripts. TEST needs no reviewer; configure a
real required reviewer for production and prevent administrator protection
bypass where available. If the repository plan does not support required
reviewers for that environment, production remains unavailable. An API failure
or missing human approval is never treated as success.

Set these independently in **each environment** (not shared repo defaults):

| Name | Scope/type | Meaning |
| --- | --- | --- |
| `ASSERTION_MEMORY_DEPLOY_ENABLED` | Variable | Set to `true` only when the target-specific VM gate, service units, health and rollback are provisioned. |
| `ASSERTION_MEMORY_SSH_HOST` | Variable | Verified VM DNS name or IPv4. |
| `ASSERTION_MEMORY_SSH_PORT` | Variable | Explicit port; no implicit production target. |
| `ASSERTION_MEMORY_SSH_USER` | Variable | `assertion-test-deploy` or `assertion-production-deploy`, exactly. |
| `ASSERTION_MEMORY_DEPLOY_KEY` | Secret | Separate, rotatable key accepted only by that user's fixed command. No root/admin key and no reused personal login. |
| `ASSERTION_MEMORY_SSH_HOST_PIN` | Secret | One independently verified Ed25519 known_hosts line (host, or `[host]:port` for non-22). Never discover or autoaccept it in CI. |

VM provisioner must enforce two restricted accounts/keys, separate TEST and
PRODUCTION release trees/config/data, and a target-specific forced command
`deploy-release ENV GIT_SHA TAR_SHA256` (archive via standard input). No GitHub
generic self-hosted runner, shell, forwarding, PTY or production credentials for
TEST. It must validate its own environment, archive, digest and (for
production) the durable corresponding green TEST receipt before activation.
Production also requires the host-side activator to open its exact SHA/digest
gate for that release; a GitHub approval alone does not replace this check.
It must return exactly one JSON object:
`{"status":"ok","environment":"test|production","revision":"<40 lowercase hex>","sha256":"<64 lowercase hex>"}`
only after activation and smoke; a merely staged release or a failed health
check must never be reported as activated. The VM owns atomic activation,
rollback and protection of the existing primary database; the CI sender has no
shell or DB access. If keys, host pin, GitHub protection, VM gate, or receiver
are absent, deployment is blocked by design. CI jobs for the two tiers are
separately serialized, without cancellation of an active transfer.
